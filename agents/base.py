import logging
import time
from abc import ABC, abstractmethod
from typing import Annotated, Callable, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from config import Settings
from models.results import AgentContext, AgentResult, Finding, FindingList
from tools.base import ToolDefinition
from tools.langchain_adapter import to_langchain_tool

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    findings: list[Finding]
    summary: str


class BaseAgent(ABC):
    name: str
    display_name: str
    description: str

    def __init__(self, llm: BaseChatModel, config: Settings):
        self.llm = llm
        self.config = config
        self._tool_definitions: list[ToolDefinition] = []
        self._tool_registry: dict[str, Callable] = {}
        self._agent_tools: list = []

    def _register_tool(self, definition: ToolDefinition, executor: Callable):
        self._tool_definitions.append(definition)
        self._tool_registry[definition.name] = executor

    def _register_agent_tool(self, agent_tool):
        self._agent_tools.append(agent_tool)
        self._tool_definitions.append(agent_tool.definition)
        self._tool_registry[agent_tool.definition.name] = agent_tool.execute

    @abstractmethod
    def get_system_prompt(self) -> str: ...

    def _build_initial_message(self, context: AgentContext) -> str:
        event = context.push_event
        commits_str = "\n".join(
            f"  - {c.id[:7]} by {c.author.name}: {c.message}"
            + (f" (modified: {', '.join(c.modified[:5])})" if c.modified else "")
            for c in event.commits
        )
        parts = [
            f"Repository: {event.repository.name}",
            f"Branch: {event.branch}",
            f"Pusher: {event.pusher.name} <{event.pusher.email}>",
            f"Commits:\n{commits_str}",
        ]
        if context.git_diff:
            diff_preview = context.git_diff[:25000]
            parts.append(f"\nGit diff:\n```diff\n{diff_preview}\n```")
        if context.additional_context:
            parts.append(f"\nPrevious analysis:\n{context.additional_context}")
        parts.append("\nPlease perform your analysis now.")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # LangGraph subgraph: call_model <-> tools, then structured extraction
    # ------------------------------------------------------------------

    def _build_graph(self, lc_tools: list):
        llm_with_tools = self.llm.bind_tools(lc_tools) if lc_tools else self.llm
        structured_llm = self.llm.with_structured_output(FindingList)

        async def call_model(state: AgentState) -> dict:
            response = await llm_with_tools.ainvoke(state["messages"])
            return {"messages": [response]}

        async def extract_findings(state: AgentState) -> dict:
            summary = ""
            for m in reversed(state["messages"]):
                if isinstance(m, AIMessage) and m.content:
                    summary = m.content if isinstance(m.content, str) else str(m.content)
                    break
            try:
                extraction: FindingList = await structured_llm.ainvoke([
                    SystemMessage(content=(
                        "Extract every concrete issue from the analysis below as structured "
                        "findings. Preserve titles, severities, file paths, line numbers, and "
                        "recommendations. If there are no issues, return an empty list."
                    )),
                    HumanMessage(content=summary or "No analysis was produced."),
                ])
                findings = list(extraction.findings)
            except Exception as e:
                logger.warning("agent=%s structured extraction failed: %s", self.name, e)
                findings = []
            return {"findings": findings, "summary": summary}

        def should_continue(state: AgentState) -> str:
            last = state["messages"][-1]
            if isinstance(last, AIMessage) and last.tool_calls:
                return "tools"
            return "extract"

        builder = StateGraph(AgentState)
        builder.add_node("call_model", call_model)
        builder.add_node("extract", extract_findings)
        builder.add_edge(START, "call_model")
        if lc_tools:
            builder.add_node("tools", ToolNode(lc_tools))
            builder.add_conditional_edges(
                "call_model", should_continue, {"tools": "tools", "extract": "extract"}
            )
            builder.add_edge("tools", "call_model")
        else:
            builder.add_edge("call_model", "extract")
        builder.add_edge("extract", END)
        return builder.compile()

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        for at in self._agent_tools:
            at.bind_context(context)

        lc_tools = [to_langchain_tool(d, self._tool_registry[d.name]) for d in self._tool_definitions]
        graph = self._build_graph(lc_tools)

        system_prompt = self.get_system_prompt()
        initial_message = self._build_initial_message(context)
        logger.info("=== [%s] SYSTEM PROMPT ===\n%s", self.name, system_prompt)
        logger.info("=== [%s] USER INPUT ===\n%s", self.name, initial_message)

        init_state: AgentState = {
            "messages": [SystemMessage(content=system_prompt), HumanMessage(content=initial_message)],
            "findings": [],
            "summary": "",
        }

        try:
            final = await graph.ainvoke(
                init_state, config={"recursion_limit": self.config.agent_recursion_limit}
            )
            findings = final.get("findings", [])
            summary = final.get("summary", "")
        except GraphRecursionError:
            logger.warning("agent=%s hit recursion_limit=%d", self.name, self.config.agent_recursion_limit)
            findings, summary = [], "Agent stopped after reaching the recursion limit."

        logger.info("agent=%s findings=%d", self.name, len(findings))
        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            summary=summary,
            duration_seconds=round(time.monotonic() - start, 2),
            tokens_used=0,
        )
