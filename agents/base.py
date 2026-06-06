import json
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
from tools import rag_tools

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    findings: list[Finding]
    summary: str
    tokens_used: int
    tool_calls_made: list[str]  # Track which tools were called
    llm_call_count: int  # Track number of LLM invocations


class BaseAgent(ABC):
    name: str
    display_name: str
    description: str

    def __init__(self, llm: BaseChatModel, config: Settings, rag_store=None):
        self.llm = llm
        self.config = config
        self.rag_store = rag_store
        self._tool_definitions: list[ToolDefinition] = []
        self._tool_registry: dict[str, Callable] = {}
        self._agent_tools: list = []
        self._knowledge_retrieved: list[dict] = []  # Track RAG documents used

        # Register RAG search tool if enabled
        if self.config.rag_enabled:
            self._register_rag_tool()

    def _register_tool(self, definition: ToolDefinition, executor: Callable):
        self._tool_definitions.append(definition)
        self._tool_registry[definition.name] = executor

    def _register_agent_tool(self, agent_tool):
        self._agent_tools.append(agent_tool)
        self._tool_definitions.append(agent_tool.definition)
        self._tool_registry[agent_tool.definition.name] = agent_tool.execute

    def _register_rag_tool(self):
        """Register the search_knowledge tool for RAG queries."""
        self._tool_definitions.append(rag_tools.SEARCH_KNOWLEDGE)

        # Wrap search_knowledge to track what was retrieved
        async def tracked_search_knowledge(
            query: str, collection: str, limit: int = 5, rag_store=None
        ):
            store = rag_store or self.rag_store
            if not store:
                return rag_tools.ToolResult(
                    tool_call_id="search_knowledge",
                    content="ERROR: RAG store not available",
                    is_error=True,
                )
            # Search once, track results and format in single pass
            try:
                results = await store.search(collection, query, limit)
                import json

                formatted_results = []
                for i, r in enumerate(results):
                    formatted = {
                        "content": r.get("content", "")[:500],
                        "metadata": r.get("metadata", {}),
                        "relevance": 1 - r.get("distance", 0),
                    }
                    formatted_results.append(formatted)
                    # Track only top 3 for audit trail
                    if i < 3:
                        self._knowledge_retrieved.append(
                            {
                                "query": query,
                                "collection": collection,
                                "content": r.get("content", "")[:200],
                                "metadata": r.get("metadata", {}),
                                "relevance": formatted["relevance"],
                            }
                        )

                return rag_tools.ToolResult(
                    tool_call_id="search_knowledge",
                    content=json.dumps(
                        {
                            "query": query,
                            "collection": collection,
                            "results_count": len(formatted_results),
                            "results": formatted_results,
                        }
                    ),
                    is_error=False,
                )
            except Exception as e:
                logger.error(f"Error searching knowledge base: {e}")
                return rag_tools.ToolResult(
                    tool_call_id="search_knowledge",
                    content=f"ERROR: Search failed: {str(e)}",
                    is_error=True,
                )

        self._tool_registry[rag_tools.SEARCH_KNOWLEDGE.name] = tracked_search_knowledge

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

    @staticmethod
    def _filter_diff_by_files(diff: str, patterns: list[str]) -> str:
        """Filter diff to only include files matching glob patterns. Returns full diff if patterns is empty."""
        if not patterns or not diff:
            return diff
        from fnmatch import fnmatch

        lines = diff.split("\n")
        filtered: list[str] = []
        current_file: str | None = None
        in_header = True
        for line in lines:
            if line.startswith("diff --git"):
                in_header = True
                parts = line.split(" b/")
                if len(parts) == 2:
                    current_file = parts[1]
                    if any(fnmatch(current_file, p) for p in patterns):
                        filtered.append(line)
                    else:
                        current_file = None
            elif in_header and (
                line.startswith("index ")
                or line.startswith("---")
                or line.startswith("+++")
                or line.startswith("old mode")
                or line.startswith("new mode")
                or line.startswith("similarity index")
                or line.startswith("rename from")
                or line.startswith("rename to")
            ):
                if current_file:
                    filtered.append(line)
            elif line.startswith("@@"):
                in_header = False
                if current_file:
                    filtered.append(line)
            elif current_file:
                filtered.append(line)
        return "\n".join(filtered) if filtered else "(no changes in matching files)"

    @staticmethod
    def _coerce_text_tool_call(message: AIMessage, tool_names: set[str]) -> AIMessage:
        """Promote a tool call the model emitted as JSON *text* into a real tool_call.

        qwen2.5-coder on Ollama sometimes returns a ```json {"name","arguments"}``` block in
        the message content instead of using the native tool-calling API. Without this, the
        graph would route straight to extraction and produce zero findings. We detect that
        shape and attach a proper tool_call so ToolNode executes it.
        """
        if message.tool_calls or not isinstance(message.content, str):
            return message
        import re
        import uuid

        for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", message.content, re.DOTALL):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            name, args = data.get("name"), data.get("arguments")
            if name in tool_names and isinstance(args, dict):
                logger.warning("agent emitted tool call as text, coercing: %s", name)
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": name, "args": args, "id": f"text_call_{uuid.uuid4().hex[:8]}"}
                    ],
                )
        return message

    # ------------------------------------------------------------------
    # LangGraph subgraph: call_model <-> tools, then structured extraction
    # ------------------------------------------------------------------

    def _build_graph(self, lc_tools: list):
        llm_with_tools = self.llm.bind_tools(lc_tools) if lc_tools else self.llm
        structured_llm = self.llm.with_structured_output(FindingList)

        tool_names = {t.name for t in lc_tools}
        turn_counter = {"n": 0}

        async def call_model(state: AgentState) -> dict:
            turn_counter["n"] += 1
            response = await llm_with_tools.ainvoke(state["messages"])
            response = self._coerce_text_tool_call(response, tool_names)
            calls = [c["name"] for c in response.tool_calls] if response.tool_calls else []
            content_len = len(response.content) if isinstance(response.content, str) else 0

            tokens_this_call = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens_this_call = response.usage_metadata.get(
                    "output_tokens", 0
                ) + response.usage_metadata.get("input_tokens", 0)

            logger.debug(
                "llm_call agent=%s turn=%d tool_calls=%s content_len=%d tokens=%d",
                self.name,
                turn_counter["n"],
                calls or "none",
                content_len,
                tokens_this_call,
            )
            if calls:
                for call in response.tool_calls:
                    args_len = len(json.dumps(call.get("args", {})))
                    logger.debug(
                        "tool_call agent=%s tool=%s args_bytes=%d",
                        self.name,
                        call.get("name"),
                        args_len,
                    )
            return {
                "messages": [response],
                "tokens_used": tokens_this_call,
                "llm_call_count": state.get("llm_call_count", 0) + 1,
            }

        async def extract_findings(state: AgentState) -> dict:
            summary = ""
            for m in reversed(state["messages"]):
                if isinstance(m, AIMessage) and m.content:
                    summary = m.content if isinstance(m.content, str) else str(m.content)
                    break
            logger.debug(
                "extraction_start agent=%s summary_bytes=%d",
                self.name,
                len(summary),
            )

            tokens_used = state.get("tokens_used", 0)

            try:
                extraction: FindingList = await structured_llm.ainvoke(
                    [
                        SystemMessage(
                            content=(
                                "Extract every concrete issue from the analysis below as structured "
                                "findings. Preserve titles, severities, file paths, line numbers, and "
                                "recommendations. If there are no issues, return an empty list."
                            )
                        ),
                        HumanMessage(content=summary or "No analysis was produced."),
                    ]
                )
                findings = list(extraction.findings)

                if hasattr(extraction, "usage_metadata") and extraction.usage_metadata:
                    tokens_used += extraction.usage_metadata.get(
                        "output_tokens", 0
                    ) + extraction.usage_metadata.get("input_tokens", 0)

                logger.debug(
                    "extraction_success agent=%s findings=%d severity_dist=%s",
                    self.name,
                    len(findings),
                    {
                        s: sum(1 for f in findings if f.severity == s)
                        for s in {"critical", "high", "medium", "low", "info"}
                    }
                    if findings
                    else "{}",
                )
            except Exception as e:
                logger.warning("agent=%s structured extraction failed: %s", self.name, e)
                findings = []

            if not findings and summary and self.config.extraction_retry:
                logger.debug(
                    "extraction_retry agent=%s summary_bytes=%d",
                    self.name,
                    len(summary),
                )
                try:
                    retry_extraction: FindingList = await structured_llm.ainvoke(
                        [
                            SystemMessage(
                                content=(
                                    "You are a JSON extractor. Read the analysis and output ONLY a JSON "
                                    "object matching this schema exactly:\n"
                                    '{"findings": [{"title": str, "description": str, "severity": '
                                    '"critical"|"high"|"medium"|"low"|"info", "file_path": str|null, '
                                    '"line_number": int|null, "recommendation": str}]}\n'
                                    'If there are genuinely no issues, output: {"findings": []}'
                                )
                            ),
                            HumanMessage(content=summary),
                        ]
                    )
                    findings = list(retry_extraction.findings)

                    if (
                        hasattr(retry_extraction, "usage_metadata")
                        and retry_extraction.usage_metadata
                    ):
                        tokens_used += retry_extraction.usage_metadata.get(
                            "output_tokens", 0
                        ) + retry_extraction.usage_metadata.get("input_tokens", 0)

                    logger.debug(
                        "extraction_retry_success agent=%s findings=%d",
                        self.name,
                        len(findings),
                    )
                except Exception as e:
                    logger.warning("agent=%s retry extraction also failed: %s", self.name, e)

            return {"findings": findings, "summary": summary, "tokens_used": tokens_used}

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
            tool_node = ToolNode(lc_tools)

            async def tools_with_tracking(state: AgentState) -> dict:
                """Track which tools are called."""
                result = await tool_node.ainvoke(state)
                last_msg = state["messages"][-1]
                if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
                    called_tools = [c["name"] for c in last_msg.tool_calls]
                    current_calls = state.get("tool_calls_made", [])
                    result["tool_calls_made"] = current_calls + called_tools
                    logger.debug(
                        "tools_executed agent=%s tools=%s",
                        self.name,
                        ",".join(called_tools),
                    )
                return result

            builder.add_node("tools", tools_with_tracking)
            builder.add_conditional_edges(
                "call_model", should_continue, {"tools": "tools", "extract": "extract"}
            )
            builder.add_edge("tools", "call_model")
        else:
            builder.add_edge("call_model", "extract")
        builder.add_edge("extract", END)
        return builder

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        # Tool-free by default: the orchestrator pre-fetches the diff into the prompt, so
        # the agent can analyze it directly in one call → structured extraction, with no
        # ReAct loop. Small local models thrash on the tool loop (hallucinated repo_url,
        # huge tool outputs → timeouts). Set agent_use_tools=True for capable models.
        if self.config.agent_use_tools:
            for at in self._agent_tools:
                at.bind_context(context)
            # Force repo_url to the real local path; the model only sees the repo name and
            # otherwise passes an unresolvable value to the git tools.
            # Also pass rag_store for search_knowledge tool.
            repo_overrides = {
                "repo_url": context.push_event.repository.clone_url,
                "rag_store": self.rag_store,
            }
            lc_tools = [
                to_langchain_tool(d, self._tool_registry[d.name], arg_overrides=repo_overrides)
                for d in self._tool_definitions
            ]
        else:
            lc_tools = []
        builder = self._build_graph(lc_tools)

        system_prompt = self.get_system_prompt()
        initial_message = self._build_initial_message(context)
        logger.info("=== [%s] SYSTEM PROMPT ===\n%s", self.name, system_prompt)
        logger.info("=== [%s] USER INPUT ===\n%s", self.name, initial_message)

        context_bytes = len(context.additional_context)
        context_truncated = False
        init_state: AgentState = {
            "messages": [
                SystemMessage(content=system_prompt),
                HumanMessage(content=initial_message),
            ],
            "findings": [],
            "summary": "",
            "tokens_used": 0,
            "tool_calls_made": [],
            "llm_call_count": 0,
        }

        invoke_config: dict = {"recursion_limit": self.config.agent_recursion_limit}

        try:
            if self.config.enable_checkpointing:
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                import hashlib

                # Generate a stable thread_id from agent + repo + diff hash
                diff_hash = hashlib.md5(context.git_diff.encode()).hexdigest()[:8]
                thread_id = f"{self.name}_{context.push_event.repository.name}_{context.push_event.after[:8]}_{diff_hash}"

                async with AsyncSqliteSaver.from_conn_string(
                    self.config.checkpoint_db_path
                ) as saver:
                    graph = builder.compile(checkpointer=saver)
                    invoke_config["configurable"] = {"thread_id": thread_id}
                    final = await graph.ainvoke(init_state, config=invoke_config)
            else:
                graph = builder.compile()
                final = await graph.ainvoke(init_state, config=invoke_config)

            findings = final.get("findings", [])
            summary = final.get("summary", "")
            tokens_used = final.get("tokens_used", 0)
            tool_calls_made = final.get("tool_calls_made", [])
            llm_call_count = final.get("llm_call_count", 0)
        except GraphRecursionError:
            logger.warning(
                "agent=%s hit recursion_limit=%d", self.name, self.config.agent_recursion_limit
            )
            findings, summary, tokens_used = (
                [],
                "Agent stopped after reaching the recursion limit.",
                0,
            )
            tool_calls_made, llm_call_count = [], 0

        if context_bytes > 0 and len(context.additional_context) < context_bytes:
            context_truncated = True

        # Estimate tokens if not calculated by graph (fallback for graphs that don't track tokens)
        if tokens_used == 0 and (summary or findings):
            from models.results import estimate_tokens
            import json
            # Estimate from input context + output text
            input_text = system_prompt + initial_message + context.additional_context
            output_text = summary + json.dumps([f.dict() for f in findings])
            tokens_used = estimate_tokens(input_text) + estimate_tokens(output_text)

        logger.info(
            "agent_complete agent=%s findings=%d tools_called=%d llm_calls=%d tokens=%d knowledge_used=%d context_bytes=%d%s",
            self.name,
            len(findings),
            len(set(tool_calls_made)),
            llm_call_count,
            tokens_used,
            len(self._knowledge_retrieved),
            context_bytes,
            " (truncated)" if context_truncated else "",
        )
        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            summary=summary,
            duration_seconds=round(time.monotonic() - start, 2),
            tokens_used=tokens_used,
            knowledge_used=self._knowledge_retrieved,
            tool_calls_made=list(set(tool_calls_made)),
            llm_call_count=llm_call_count,
            context_received_bytes=context_bytes,
            context_truncated=context_truncated,
        )
