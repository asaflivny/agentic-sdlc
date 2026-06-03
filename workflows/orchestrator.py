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

    def _make_llm(self, agent_name: str) -> ChatOllama:
        return ChatOllama(
            model=self.config.model_for_agent(agent_name),
            base_url=self.config.ollama_native_url,
            num_predict=self.config.max_tokens,
            temperature=0,
        )

    async def run(
        self, workflow: WorkflowDefinition, event: PushEvent, run_id: str | None = None
    ) -> WorkflowResult:
        started_at = datetime.now(timezone.utc)
        logger.info(
            "workflow=%s repo=%s branch=%s mode=%s agents=%d",
            workflow.name,
            event.repository.name,
            event.branch,
            workflow.mode,
            len(workflow.agent_specs),
        )

        git_diff = await self._fetch_diff(event)
        agents = self._build_agents(workflow)
        builder = self._build_workflow_graph(workflow, agents)

        init_state: WorkflowState = {
            "push_event": event,
            "git_diff": git_diff,
            "shared_context": "",
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

        return WorkflowResult(
            workflow_name=workflow.name,
            repo_name=event.repository.name,
            branch=event.branch,
            started_at=started_at,
            completed_at=completed_at,
            agent_results=results,
            overall_summary=self._aggregate_summary(results),
        )

    def _build_agents(self, workflow: WorkflowDefinition) -> list[BaseAgent]:
        agents = []
        for spec in workflow.agent_specs:
            agent = spec.agent_class(self._make_llm(spec.agent_class.name), self.config)
            for sub_name in spec.can_call:
                sub_class = AGENT_REGISTRY.get(sub_name)
                if sub_class:
                    sub_agent = sub_class(self._make_llm(sub_name), self.config)
                    agent._register_agent_tool(AgentTool(sub_agent))
                else:
                    logger.warning("unknown sub-agent: %s", sub_name)
            agents.append(agent)
        return agents

    # ------------------------------------------------------------------
    # Workflow graph: sequential chain (shared_context flows) or parallel
    # fan-out (results merged by the operator.add reducer).
    # ------------------------------------------------------------------

    def _make_agent_node(self, agent: BaseAgent, sequential: bool):
        async def node(state: WorkflowState) -> dict:
            context = AgentContext(
                push_event=state["push_event"],
                git_diff=state["git_diff"],
                additional_context=state.get("shared_context", ""),
            )
            result = await self._run_with_timeout(agent, context)
            update: dict = {"agent_results": [result]}
            if sequential and result.status == "success":
                update["shared_context"] = state.get("shared_context", "") + \
                    self._format_context_enrichment(agent.display_name, result)
            return update

        return node

    def _build_workflow_graph(self, workflow: WorkflowDefinition, agents: list[BaseAgent]) -> StateGraph:
        builder = StateGraph(WorkflowState)
        sequential = workflow.mode != ExecutionMode.PARALLEL

        if sequential:
            prev = START
            for i, agent in enumerate(agents):
                node_name = f"{agent.name}_{i}"
                builder.add_node(node_name, self._make_agent_node(agent, sequential=True))
                builder.add_edge(prev, node_name)
                prev = node_name
            builder.add_edge(prev, END)
        else:
            for i, agent in enumerate(agents):
                node_name = f"{agent.name}_{i}"
                builder.add_node(node_name, self._make_agent_node(agent, sequential=False))
                builder.add_edge(START, node_name)
                builder.add_edge(node_name, END)

        return builder

    def _order_results(self, results: list[AgentResult], agents: list[BaseAgent]) -> list[AgentResult]:
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

    async def _run_with_timeout(self, agent: BaseAgent, context: AgentContext) -> AgentResult:
        logger.info("starting agent=%s", agent.name)
        try:
            async with asyncio.timeout(self.config.agent_timeout_seconds):
                return await agent.run(context)
        except TimeoutError:
            logger.warning("agent=%s timed out after %ds", agent.name, self.config.agent_timeout_seconds)
            return AgentResult(agent_name=agent.name, status="timeout", summary="Agent timed out.")
        except Exception as e:
            logger.exception("agent=%s failed: %s", agent.name, e)
            return AgentResult(agent_name=agent.name, status="error", summary=str(e))

    def _deduplicate_findings(self, results: list[AgentResult]) -> list[AgentResult]:
        """Remove duplicate findings across agents (same title + file_path, keep first seen)."""
        seen: set[tuple] = set()
        deduped = []
        for r in results:
            unique = []
            for f in r.findings:
                key = (f.title.lower().strip(), f.file_path or "")
                if key not in seen:
                    seen.add(key)
                    unique.append(f)
                else:
                    logger.debug("dedup: dropped duplicate finding '%s' from %s", f.title, r.agent_name)
            deduped.append(r.model_copy(update={"findings": unique}))
        return deduped

    def _format_context_enrichment(self, agent_display_name: str, result: AgentResult) -> str:
        """Build a compact finding summary safe to pass as sequential context."""
        lines = [f"\n\n{agent_display_name} completed ({len(result.findings)} finding(s))."]
        for f in result.findings:
            loc = f" ({f.file_path}:{f.line_number})" if f.file_path else ""
            lines.append(f"  [{f.severity.upper()}] {f.title}{loc} — {f.recommendation or f.description[:120]}")
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
