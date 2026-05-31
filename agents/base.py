import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Callable

from openai import AsyncOpenAI

from config import Settings
from models.results import AgentContext, AgentResult, Finding
from tools.base import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    name: str
    display_name: str
    description: str

    def __init__(self, client: AsyncOpenAI, config: Settings):
        self.client = client
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

    async def _execute_tool(self, name: str, tool_call_id: str, arguments: dict) -> ToolResult:
        executor = self._tool_registry.get(name)
        if not executor:
            return ToolResult(tool_call_id, f"Unknown tool: {name}", is_error=True)
        try:
            result = await executor(**arguments)
            result.tool_call_id = tool_call_id
            return result
        except Exception as e:
            logger.exception("tool %s failed", name)
            return ToolResult(tool_call_id, str(e), is_error=True)

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        for at in self._agent_tools:
            at.bind_context(context)

        initial_message = self._build_initial_message(context)
        system_prompt = self.get_system_prompt()
        messages = [{"role": "user", "content": initial_message}]
        tools = [d.to_openai_schema() for d in self._tool_definitions] or None
        final_text = ""
        total_tokens = 0

        logger.info("=== [%s] SYSTEM PROMPT ===\n%s", self.name, system_prompt)
        logger.info("=== [%s] USER INPUT ===\n%s", self.name, initial_message)

        MAX_TURNS = 10
        turn = 0
        while turn < MAX_TURNS:
            turn += 1
            kwargs = dict(
                model=self.config.model_for_agent(self.name),
                messages=[{"role": "system", "content": system_prompt}, *messages],
                max_tokens=self.config.max_tokens,
            )
            if tools:
                kwargs["tools"] = tools

            response = await self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            msg = choice.message

            if response.usage:
                total_tokens += response.usage.total_tokens or 0

            logger.info(
                "=== [%s] LLM RESPONSE (turn=%d, finish=%s, tokens=%s) ===\n%s",
                self.name, turn, choice.finish_reason,
                response.usage.total_tokens if response.usage else "?",
                msg.content or "(no text — tool call only)",
            )

            assistant_msg: dict = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(assistant_msg)

            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    result = await self._execute_tool(tc.function.name, tc.id, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": result.tool_call_id,
                        "content": result.content,
                    })
            else:
                final_text = msg.content or ""
                break
        else:
            logger.warning("agent=%s hit max_turns=%d, stopping", self.name, MAX_TURNS)

        findings = self._parse_findings(final_text)
        logger.info("agent=%s findings=%d tokens=%d", self.name, len(findings), total_tokens)

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            summary=final_text,
            duration_seconds=round(time.monotonic() - start, 2),
            tokens_used=total_tokens,
        )

    def _parse_findings(self, text: str) -> list[Finding]:
        import re
        sentinel = "---FINDINGS---"

        # Try sentinel-delimited block first
        if sentinel in text:
            json_part = text.split(sentinel, 1)[1].strip()
            if json_part.startswith("```"):
                json_part = json_part.split("\n", 1)[1].rsplit("```", 1)[0]
            result = self._try_parse_findings_json(json_part)
            if result is not None:
                return result

        # Fall back: last fenced JSON array block (model omitted sentinel)
        for block in reversed(re.findall(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)):
            result = self._try_parse_findings_json(block)
            if result is not None:
                logger.debug("agent=%s: parsed findings from fenced JSON block", self.name)
                return result

        # Last resort: find any bare JSON array in the text
        for match in reversed(list(re.finditer(r"\[[\s\S]*?\]", text))):
            result = self._try_parse_findings_json(match.group())
            if result is not None:
                logger.debug("agent=%s: parsed findings from bare JSON array", self.name)
                return result

        return []

    def _try_parse_findings_json(self, text: str) -> list[Finding] | None:
        try:
            data = json.loads(text.strip())
            if isinstance(data, dict):
                for key in ("findings", "results", "issues", "vulnerabilities"):
                    if isinstance(data.get(key), list):
                        data = data[key]
                        break
                else:
                    return None
            if not isinstance(data, list):
                return None
            return [Finding(**f) for f in data]
        except Exception:
            return None
