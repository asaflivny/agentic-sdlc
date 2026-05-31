import asyncio
import logging
from datetime import datetime, timezone

from openai import AsyncOpenAI

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


class WorkflowOrchestrator:
    def __init__(self, config: Settings):
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config.ollama_base_url,
            api_key="ollama",
        )

    async def run(self, workflow: WorkflowDefinition, event: PushEvent) -> WorkflowResult:
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
        context = AgentContext(push_event=event, git_diff=git_diff)
        agents = self._build_agents(workflow)

        if workflow.mode == ExecutionMode.PARALLEL:
            results = await self._run_parallel(agents, context)
        else:
            results = await self._run_sequential(agents, context)

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
            agent = spec.agent_class(self.client, self.config)
            for sub_name in spec.can_call:
                sub_class = AGENT_REGISTRY.get(sub_name)
                if sub_class:
                    sub_agent = sub_class(self.client, self.config)
                    agent._register_agent_tool(AgentTool(sub_agent))
                else:
                    logger.warning("unknown sub-agent: %s", sub_name)
            agents.append(agent)
        return agents

    async def _run_sequential(self, agents: list[BaseAgent], context: AgentContext) -> list[AgentResult]:
        results = []
        for agent in agents:
            result = await self._run_with_timeout(agent, context)
            results.append(result)
            if result.status == "success":
                enrichment = self._format_context_enrichment(agent.display_name, result)
                context = context.model_copy(update={
                    "additional_context": context.additional_context + enrichment
                })
        return results

    async def _run_parallel(self, agents: list[BaseAgent], context: AgentContext) -> list[AgentResult]:
        tasks = [self._run_with_timeout(agent, context) for agent in agents]
        return list(await asyncio.gather(*tasks))

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
