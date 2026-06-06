"""Bridge the project's ToolDefinition/executor pairs into LangChain tools.

Keeps a single source of truth: the existing `ToolDefinition` (param descriptions) and
the async executors (which return `ToolResult`) in tools/git_tools.py and tools/agent_tool.py
are reused verbatim. We only adapt the *shape* so LangGraph's `ToolNode` / `bind_tools`
can consume them.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from tools.base import ToolDefinition, ToolResult

# JSON-schema-ish type strings (as used in ToolParameter.type) -> Python types.
_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _build_args_schema(definition: ToolDefinition, executor: Callable):
    """Construct a Pydantic model describing the tool's arguments.

    Descriptions come from the ToolDefinition; defaults for optional params are pulled
    from the executor's real signature so behaviour matches direct calls (e.g.
    analyze_file_history's max_commits=10).
    """
    sig = inspect.signature(executor)
    fields: dict[str, tuple] = {}
    for p in definition.parameters:
        py_type = _TYPE_MAP.get(p.type, str)
        extra: dict[str, Any] = {}
        if p.enum:
            extra["json_schema_extra"] = {"enum": p.enum}
        if p.required:
            fields[p.name] = (py_type, Field(description=p.description, **extra))
        else:
            sig_default = sig.parameters.get(p.name)
            default = (
                sig_default.default
                if sig_default is not None and sig_default.default is not inspect.Parameter.empty
                else None
            )
            fields[p.name] = (
                Optional[py_type],
                Field(default=default, description=p.description, **extra),
            )

    return create_model(f"{definition.name}_args", **fields)


def to_langchain_tool(
    definition: ToolDefinition,
    executor: Callable,
    arg_overrides: dict[str, Any] | None = None,
) -> StructuredTool:
    """Wrap a (ToolDefinition, async executor) pair as a LangChain StructuredTool.

    The wrapper invokes the original executor and returns `ToolResult.content` as the tool
    output. Errors are surfaced as text (prefixed with ERROR:) rather than raised, matching
    the old loop's behaviour where agents saw tool errors as tool output.

    `arg_overrides` force specific argument values regardless of what the model passes —
    used to inject the real local `repo_url` from context, since the model only sees the
    repo *name* and otherwise hallucinates an unresolvable path.
    """
    args_schema = _build_args_schema(definition, executor)
    param_names = {p.name for p in definition.parameters}
    overrides = {k: v for k, v in (arg_overrides or {}).items() if k in param_names}

    async def _run(**kwargs: Any) -> str:
        kwargs.update(overrides)
        result: ToolResult = await executor(**kwargs)
        if result.is_error:
            return f"ERROR: {result.content}"
        return result.content

    return StructuredTool.from_function(
        coroutine=_run,
        name=definition.name,
        description=definition.description,
        args_schema=args_schema,
    )
