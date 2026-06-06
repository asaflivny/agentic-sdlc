import asyncio
import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph

from agents.base import BaseAgent
from agents.code_reviewer import CodeReviewAgent
from agents.dep_auditor import DepAuditAgent
from agents.performance_analyst import PerformanceAnalystAgent
from agents.security_analyst import SecurityAnalystAgent
from agents.test_coverage import TestCoverageAgent
from config import Settings
from models.events import PushEvent
from models.results import AgentContext, AgentResult, WorkflowResult
from tools.agent_tool import AgentTool
from tools.git_tools import fetch_diff
from workflows.base import ExecutionMode, WorkflowDefinition

logger = logging.getLogger(__name__)

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    CodeReviewAgent.name: CodeReviewAgent,
    SecurityAnalystAgent.name: SecurityAnalystAgent,
    PerformanceAnalystAgent.name: PerformanceAnalystAgent,
    DepAuditAgent.name: DepAuditAgent,
    TestCoverageAgent.name: TestCoverageAgent,
}


class WorkflowState(TypedDict):
    push_event: PushEvent
    git_diff: str
    shared_context: str  # sequential enrichment passed forward between agents
    agent_results: Annotated[list[AgentResult], operator.add]  # reducer merges branches


class WorkflowOrchestrator:
    def __init__(self, config: Settings):
        self.config = config
        self.rag_store = None
        self._repo_config_cache: dict[tuple[str, str], dict] = {}  # (repo_url, after_sha) -> config

    def set_rag_store(self, rag_store):
        """Set the RAG store instance."""
        self.rag_store = rag_store

    def _make_llm(self, agent_name: str) -> ChatOllama:
        return ChatOllama(
            model=self.config.model_for_agent(agent_name),
            base_url=self.config.ollama_native_url,
            num_predict=self.config.max_tokens,
            temperature=0,
        )

    async def _load_repo_overrides_cached(self, event: "PushEvent") -> dict:
        """Load repo config with caching by (repo_url, after_sha)."""
        from workflows.repo_config import load_repo_overrides

        cache_key = (event.repository.clone_url, event.after)
        if cache_key in self._repo_config_cache:
            logger.debug(
                "repo_config cache hit repo=%s sha=%s", event.repository.name, event.after[:8]
            )
            return self._repo_config_cache[cache_key]
        overrides = await load_repo_overrides(event)
        self._repo_config_cache[cache_key] = overrides
        if overrides:
            logger.info(
                "repo_config cache store repo=%s sha=%s", event.repository.name, event.after[:8]
            )
        return overrides

    async def run(
        self, workflow: WorkflowDefinition, event: PushEvent, run_id: str | None = None
    ) -> WorkflowResult:
        started_at = datetime.now(timezone.utc)
        logger.info(
            "workflow=%s repo=%s branch=%s mode=%s agents=%d run_id=%s",
            workflow.name,
            event.repository.name,
            event.branch,
            workflow.mode,
            len(workflow.agent_specs),
            run_id,
        )

        git_diff = await self._fetch_diff(event)
        logger.debug("git_diff_fetched size=%d bytes", len(git_diff))

        # Retrieve critical knowledge from RAG store if enabled
        critical_knowledge = ""
        if self.rag_store:
            try:
                known_issues = await self.rag_store.search(
                    "known_issues",
                    event.repository.name,
                    limit=3,
                    where={"repo": {"$in": [event.repository.name, "global"]}},
                )
                if known_issues:
                    critical_knowledge += "=== Known Issues for this Repository ===\n"
                    for result in known_issues:
                        critical_knowledge += f"- {result.get('content', '')[:200]}\n"
                    critical_knowledge += "\n"
                logger.debug(
                    "rag_search_completed collection=known_issues results=%d", len(known_issues)
                )
            except Exception as e:
                logger.warning("Failed to retrieve knowledge from RAG: %s", e)

        from workflows.repo_config import apply_overrides

        repo_overrides = await self._load_repo_overrides_cached(event)
        agent_names = [spec.agent_class.name for spec in workflow.agent_specs]
        workflow, agent_names = apply_overrides(workflow, agent_names, repo_overrides)
        agent_name_set = set(agent_names)
        if len(agent_name_set) < len(agent_names):
            logger.debug(
                "repo_config_applied original_agents=%d effective_agents=%d",
                len(agent_names),
                len(agent_name_set),
            )
        filtered_specs = [s for s in workflow.agent_specs if s.agent_class.name in agent_name_set]
        from workflows.base import WorkflowDefinition

        effective_workflow = WorkflowDefinition(
            name=workflow.name,
            description=workflow.description,
            agent_specs=filtered_specs,
            mode=workflow.mode,
        )

        agents = self._build_agents(effective_workflow)
        logger.debug(
            "agents_built count=%d execution_mode=%s",
            len(agents),
            "parallel" if workflow.mode == "parallel" else "sequential",
        )
        builder = self._build_workflow_graph(
            effective_workflow, agents, effective_workflow.agent_specs
        )

        init_state: WorkflowState = {
            "push_event": event,
            "git_diff": git_diff,
            "shared_context": critical_knowledge,  # Seed with critical knowledge
            "agent_results": [],
        }
        invoke_config: dict = {"recursion_limit": max(25, len(agents) + 5)}

        if self.config.enable_checkpointing and run_id:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            async with AsyncSqliteSaver.from_conn_string(self.config.checkpoint_db_path) as saver:
                graph = builder.compile(checkpointer=saver)
                invoke_config["configurable"] = {"thread_id": run_id}
                final = await graph.ainvoke(init_state, config=invoke_config)
        else:
            graph = builder.compile()
            final = await graph.ainvoke(init_state, config=invoke_config)

        results = self._order_results(final["agent_results"], agents)
        results = self._deduplicate_findings(results)
        completed_at = datetime.now(timezone.utc)
        total_findings = sum(len(r.findings) for r in results)
        logger.info(
            "workflow=%s done duration=%.1fs total_findings=%d",
            workflow.name,
            (completed_at - started_at).total_seconds(),
            total_findings,
        )

        result = WorkflowResult(
            workflow_name=workflow.name,
            repo_name=event.repository.name,
            branch=event.branch,
            commit_sha=event.after,
            started_at=started_at,
            completed_at=completed_at,
            agent_results=results,
            overall_summary=self._aggregate_summary(results),
        )

        # Auto-index findings into RAG store if enabled
        if self.rag_store and run_id:
            try:
                await self.rag_store.index_findings(run_id, result)
                logger.debug("findings_indexed run_id=%s count=%d", run_id, total_findings)
            except Exception as e:
                logger.warning("Failed to index findings into RAG: %s", e)

        return result

    def _validate_no_delegation_cycles(self, specs: list) -> None:
        """Validate that the delegation graph has no cycles."""
        graph: dict[str, list[str]] = {}
        for spec in specs:
            graph[spec.agent_class.name] = spec.can_call

        def _has_cycle_from(node: str, visited: set[str], rec_stack: set[str]) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if _has_cycle_from(neighbor, visited, rec_stack):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.remove(node)
            return False

        visited = set()
        for agent_name in graph:
            if agent_name not in visited:
                if _has_cycle_from(agent_name, visited, set()):
                    logger.error("delegation_cycle detected: %s", agent_name)
                    raise ValueError(f"Delegation cycle detected involving {agent_name}")

    def _build_agents(self, workflow: WorkflowDefinition) -> list[BaseAgent]:
        self._validate_no_delegation_cycles(workflow.agent_specs)

        agents = []
        for i, spec in enumerate(workflow.agent_specs):
            model = self.config.model_for_agent(spec.agent_class.name)
            agent = spec.agent_class(
                self._make_llm(spec.agent_class.name), self.config, self.rag_store
            )
            logger.debug(
                "agent_init order=%d name=%s model=%s",
                i + 1,
                spec.agent_class.name,
                model,
            )
            for sub_name in spec.can_call:
                sub_class = AGENT_REGISTRY.get(sub_name)
                if sub_class:
                    sub_model = self.config.model_for_agent(sub_name)
                    sub_agent = sub_class(self._make_llm(sub_name), self.config, self.rag_store)
                    agent._register_agent_tool(AgentTool(sub_agent))
                    logger.debug(
                        "sub_agent_registered parent=%s child=%s model=%s",
                        spec.agent_class.name,
                        sub_name,
                        sub_model,
                    )
                else:
                    logger.warning("unknown sub-agent: %s", sub_name)
            agents.append(agent)
        return agents

    # ------------------------------------------------------------------
    # Workflow graph: sequential chain (shared_context flows) or parallel
    # fan-out (results merged by the operator.add reducer).
    # ------------------------------------------------------------------

    def _make_agent_node(
        self, agent: BaseAgent, sequential: bool, file_filter: list[str] | None = None
    ):
        async def node(state: WorkflowState) -> dict:
            git_diff = state["git_diff"]
            if file_filter:
                git_diff = agent._filter_diff_by_files(git_diff, file_filter)
                if git_diff == "(no changes in matching files)":
                    logger.info("agent=%s skipped (no matching files)", agent.name)
                    return {"agent_results": []}

            prior_context_len = len(state.get("shared_context", ""))
            context = AgentContext(
                push_event=state["push_event"],
                git_diff=git_diff,
                additional_context=state.get("shared_context", ""),
            )

            chunks = self._chunk_diff(git_diff)
            if len(chunks) > 1:
                result = await self._run_chunked(agent, context, chunks)
            else:
                result = await self._run_with_timeout(agent, context)

            logger.debug(
                "agent_completed agent=%s findings=%d duration=%.2fs status=%s",
                agent.name,
                len(result.findings),
                result.duration_seconds or 0,
                result.status,
            )

            update: dict = {"agent_results": [result]}
            if sequential and result.status == "success":
                enrichment = self._format_context_enrichment(agent.display_name, result)
                updated_context = state.get("shared_context", "") + enrichment
                update["shared_context"] = updated_context
                logger.debug(
                    "context_enriched agent=%s prior_context_bytes=%d enrichment_bytes=%d total_bytes=%d",
                    agent.name,
                    prior_context_len,
                    len(enrichment),
                    len(updated_context),
                )
            return update

        return node

    def _build_workflow_graph(
        self, workflow: WorkflowDefinition, agents: list[BaseAgent], specs: list
    ) -> StateGraph:
        builder = StateGraph(WorkflowState)
        sequential = workflow.mode != ExecutionMode.PARALLEL
        # Map agent.name -> spec for quick lookup
        spec_map = {spec.agent_class.name: spec for spec in specs}

        if sequential:
            prev = START
            for i, agent in enumerate(agents):
                node_name = f"{agent.name}_{i}"
                spec = spec_map.get(agent.name)
                file_filter = spec.file_filter if spec else None
                builder.add_node(
                    node_name,
                    self._make_agent_node(agent, sequential=True, file_filter=file_filter),
                )
                builder.add_edge(prev, node_name)
                prev = node_name
            builder.add_edge(prev, END)
        else:
            for i, agent in enumerate(agents):
                node_name = f"{agent.name}_{i}"
                spec = spec_map.get(agent.name)
                file_filter = spec.file_filter if spec else None
                builder.add_node(
                    node_name,
                    self._make_agent_node(agent, sequential=False, file_filter=file_filter),
                )
                builder.add_edge(START, node_name)
                builder.add_edge(node_name, END)

        return builder

    def _order_results(
        self, results: list[AgentResult], agents: list[BaseAgent]
    ) -> list[AgentResult]:
        """Restore deterministic agent-definition order (parallel branches finish in any order)."""
        by_name: dict[str, list[AgentResult]] = {}
        for r in results:
            by_name.setdefault(r.agent_name, []).append(r)
        ordered: list[AgentResult] = []
        for a in agents:
            bucket = by_name.get(a.name)
            if bucket:
                ordered.append(bucket.pop(0))
        for bucket in by_name.values():
            ordered.extend(bucket)
        return ordered

    def _chunk_diff(self, diff: str) -> list[str]:
        """Split a large diff into overlapping chunks. Returns [diff] unchanged when chunking is disabled or unnecessary."""
        chunk_bytes = self.config.diff_chunk_size_kb * 1024
        if chunk_bytes <= 0 or len(diff) <= chunk_bytes:
            return [diff]
        overlap = max(0, chunk_bytes // 10)  # 10% overlap between consecutive chunks
        chunks: list[str] = []
        offset = 0
        while offset < len(diff):
            chunks.append(diff[offset : offset + chunk_bytes])
            offset += chunk_bytes - overlap
        logger.warning(
            "diff chunking enabled total_size_kb=%d num_chunks=%d chunk_size_kb=%d overlap_kb=%d",
            len(diff) // 1024,
            len(chunks),
            self.config.diff_chunk_size_kb,
            overlap // 1024,
        )
        return chunks

    async def _run_chunked(
        self, agent: BaseAgent, context: AgentContext, chunks: list[str]
    ) -> AgentResult:
        """Run the agent once per diff chunk and merge findings."""
        all_results: list[AgentResult] = []
        for i, chunk in enumerate(chunks):
            chunk_ctx = AgentContext(
                push_event=context.push_event,
                git_diff=f"[chunk {i + 1}/{len(chunks)}]\n{chunk}",
                additional_context=context.additional_context,
            )
            logger.debug(
                "chunk_run agent=%s chunk=%d/%d size_kb=%d",
                agent.name,
                i + 1,
                len(chunks),
                len(chunk) // 1024,
            )
            result = await self._run_with_timeout(agent, chunk_ctx)
            logger.debug(
                "chunk_complete agent=%s chunk=%d findings=%d duration=%.2fs",
                agent.name,
                i + 1,
                len(result.findings),
                result.duration_seconds or 0,
            )
            all_results.append(result)

        merged_findings = [f for r in all_results for f in r.findings]
        merged_summary = "\n\n".join(r.summary for r in all_results if r.summary)
        statuses = {r.status for r in all_results}
        final_status = (
            "success"
            if statuses == {"success"}
            else ("error" if "error" in statuses else "timeout")
        )
        total_duration = sum(r.duration_seconds or 0 for r in all_results)
        total_tokens = sum(r.tokens_used for r in all_results)
        logger.debug(
            "chunk_merge agent=%s total_findings=%d final_status=%s tokens=%d",
            agent.name,
            len(merged_findings),
            final_status,
            total_tokens,
        )
        return AgentResult(
            agent_name=agent.name,
            status=final_status,
            findings=merged_findings,
            summary=merged_summary,
            duration_seconds=round(total_duration, 2),
            tokens_used=total_tokens,
        )

    async def _run_with_timeout(self, agent: BaseAgent, context: AgentContext) -> AgentResult:
        import time

        logger.debug(
            "agent_start agent=%s diff_size_kb=%d context_bytes=%d",
            agent.name,
            len(context.git_diff) // 1024,
            len(context.additional_context),
        )
        start = time.monotonic()
        try:
            async with asyncio.timeout(self.config.agent_timeout_seconds):
                return await agent.run(context)
        except TimeoutError:
            elapsed = round(time.monotonic() - start, 2)
            logger.warning(
                "agent_timeout agent=%s timeout_sec=%d elapsed_sec=%.2f",
                agent.name,
                self.config.agent_timeout_seconds,
                elapsed,
            )
            return AgentResult(agent_name=agent.name, status="timeout", summary="Agent timed out.")
        except Exception as e:
            elapsed = round(time.monotonic() - start, 2)
            logger.exception(
                "agent_error agent=%s elapsed_sec=%.2f error=%s", agent.name, elapsed, e
            )
            return AgentResult(agent_name=agent.name, status="error", summary=str(e))

    def _deduplicate_findings(self, results: list[AgentResult]) -> list[AgentResult]:
        """Remove duplicate findings across agents (same title + file_path, keep first seen)."""
        seen: set[tuple] = set()
        deduped = []
        dropped_count = 0
        for r in results:
            unique = []
            for f in r.findings:
                key = (f.title.lower().strip(), f.file_path or "")
                if key not in seen:
                    seen.add(key)
                    unique.append(f)
                else:
                    dropped_count += 1
                    logger.debug(
                        "dedup: dropped duplicate finding title='%s' from %s", f.title, r.agent_name
                    )
            if dropped_count > 0 and len(unique) < len(r.findings):
                logger.debug(
                    "dedup_agent agent=%s original_findings=%d unique_findings=%d dropped=%d",
                    r.agent_name,
                    len(r.findings),
                    len(unique),
                    len(r.findings) - len(unique),
                )
            deduped.append(r.model_copy(update={"findings": unique}))
        if dropped_count > 0:
            logger.info("dedup_summary total_dropped=%d unique_kept=%d", dropped_count, len(seen))
        return deduped

    def _format_context_enrichment(self, agent_display_name: str, result: AgentResult) -> str:
        """Build a compact finding summary safe to pass as sequential context."""
        lines = [f"\n\n{agent_display_name} completed ({len(result.findings)} finding(s))."]
        for f in result.findings:
            loc = f" ({f.file_path}:{f.line_number})" if f.file_path else ""
            lines.append(
                f"  [{f.severity.upper()}] {f.title}{loc} — {f.recommendation or f.description[:120]}"
            )
        return "\n".join(lines)

    async def _fetch_diff(self, event: PushEvent) -> str:
        repo_url = event.repository.clone_url
        if not repo_url:
            logger.warning("no clone_url in push event, skipping pre-fetch")
            return ""
        result = await fetch_diff(repo_url, event.before, event.after)
        if result.is_error:
            logger.warning("diff pre-fetch failed: %s", result.content)
            return ""
        return result.content

    def _aggregate_summary(self, results: list[AgentResult]) -> str:
        parts = []
        for r in results:
            status_tag = f"[{r.status.upper()}]"
            findings_tag = f"{len(r.findings)} finding(s)" if r.findings else "no findings"
            parts.append(f"### {r.agent_name} {status_tag} — {findings_tag}")
            if r.findings:
                for f in r.findings:
                    parts.append(f"  - [{f.severity.upper()}] {f.title}")
        return "\n".join(parts)
