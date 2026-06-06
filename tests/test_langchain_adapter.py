import asyncio

from tools.base import ToolDefinition, ToolParameter, ToolResult
from tools.langchain_adapter import to_langchain_tool


async def _executor(repo_url: str, ref: str, max_commits: int = 10) -> ToolResult:
    return ToolResult("", f"{repo_url}@{ref} n={max_commits}")


async def _failing_executor(repo_url: str) -> ToolResult:
    return ToolResult("", "boom", is_error=True)


DEFN = ToolDefinition(
    name="sample_tool",
    description="A sample tool",
    parameters=[
        ToolParameter("repo_url", "string", "Repo path"),
        ToolParameter("ref", "string", "Git ref"),
        ToolParameter("max_commits", "integer", "How many", required=False),
    ],
)


def test_schema_matches_definition():
    tool = to_langchain_tool(DEFN, _executor)
    assert tool.name == "sample_tool"
    assert tool.description == "A sample tool"

    args = tool.args  # JSON-schema properties
    assert set(args) == {"repo_url", "ref", "max_commits"}
    assert args["repo_url"]["description"] == "Repo path"

    # Required params are required; optional ones carry the executor's real default.
    required = tool.get_input_schema().model_json_schema().get("required", [])
    assert "repo_url" in required and "ref" in required
    assert "max_commits" not in required


def test_invocation_routes_to_executor():
    tool = to_langchain_tool(DEFN, _executor)
    out = asyncio.run(tool.ainvoke({"repo_url": "/tmp/repo", "ref": "HEAD"}))
    # Optional max_commits defaults to the executor's signature default (10).
    assert out == "/tmp/repo@HEAD n=10"


def test_arg_override_forces_value():
    tool = to_langchain_tool(DEFN, _executor, arg_overrides={"repo_url": "/real/path"})
    # Model passes a bogus repo_url; the override wins.
    out = asyncio.run(tool.ainvoke({"repo_url": "wrong-name", "ref": "HEAD"}))
    assert out == "/real/path@HEAD n=10"


def test_error_surfaced_as_text():
    defn = ToolDefinition(
        name="failing", description="x",
        parameters=[ToolParameter("repo_url", "string", "Repo path")],
    )
    tool = to_langchain_tool(defn, _failing_executor)
    out = asyncio.run(tool.ainvoke({"repo_url": "/tmp/repo"}))
    assert out.startswith("ERROR:")
    assert "boom" in out
