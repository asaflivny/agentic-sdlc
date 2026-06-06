from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from tools.base import ToolDefinition, ToolParameter, ToolResult

if TYPE_CHECKING:
    from agents.base import BaseAgent
    from models.results import AgentContext

logger = logging.getLogger(__name__)


class AgentTool:
    def __init__(self, agent: BaseAgent):
        self.agent = agent
        self._context: AgentContext | None = None

    def bind_context(self, context: AgentContext):
        self._context = context

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=f"call_{self.agent.name}",
            description=f"Delegate to {self.agent.display_name}. {self.agent.description}",
            parameters=[
                ToolParameter(
                    name="task_description",
                    type="string",
                    description="Specific task or question for the agent to investigate",
                    required=True,
                ),
                ToolParameter(
                    name="focus_areas",
                    type="string",
                    description="Specific areas or files to focus on (optional)",
                    required=False,
                ),
            ],
        )

    async def execute(self, task_description: str, focus_areas: str = "") -> ToolResult:
        if self._context is None:
            return ToolResult("", "AgentTool has no bound context", is_error=True)

        extra = f"\nFocus on: {focus_areas}" if focus_areas else ""
        sub_context = self._context.model_copy(
            update={"additional_context": task_description + extra}
        )

        logger.info(
            "sub_agent_delegated parent_agent=%s child_agent=%s task_bytes=%d",
            getattr(self._context, "trace", None) and self._context.trace.agent_trace_id,
            self.agent.name,
            len(task_description),
        )

        try:
            result = await self.agent.run(sub_context)
            logger.info(
                "sub_agent_complete parent_agent=%s child_agent=%s findings=%d tools=%d status=%s",
                getattr(self._context, "trace", None) and self._context.trace.agent_trace_id,
                self.agent.name,
                len(result.findings),
                len(result.tool_calls_made),
                result.status,
            )
            payload = json.dumps(
                {
                    "agent": self.agent.name,
                    "summary": result.summary[:3000],
                    "findings": [f.model_dump() for f in result.findings],
                }
            )
            return ToolResult("", payload)
        except Exception as e:
            logger.exception(
                "sub_agent_failed parent_agent=%s child_agent=%s error=%s",
                getattr(self._context, "trace", None) and self._context.trace.agent_trace_id,
                self.agent.name,
                e,
            )
            return ToolResult("", str(e), is_error=True)
