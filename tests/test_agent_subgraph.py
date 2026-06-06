"""Prove the LangGraph agent subgraph returns structured Finding objects via
with_structured_output — no ---FINDINGS--- sentinel text involved."""

import asyncio

from langchain_core.messages import AIMessage

from agents.code_reviewer import CodeReviewAgent
from config import Settings
from models.events import Author, Commit, PushEvent, Pusher, Repository
from models.results import Finding, FindingList, Severity


class _FakeRunnable:
    def __init__(self, response):
        self._response = response

    async def ainvoke(self, messages, *args, **kwargs):
        return self._response


class FakeChatModel:
    """Stands in for ChatOllama: bind_tools -> prose AIMessage, with_structured_output -> FindingList."""

    def __init__(self, ai_message, finding_list):
        self._ai = ai_message
        self._fl = finding_list

    async def ainvoke(self, messages, *args, **kwargs):
        # tool-free default path calls the model directly
        return self._ai

    def bind_tools(self, tools):
        return _FakeRunnable(self._ai)

    def with_structured_output(self, schema):
        return _FakeRunnable(self._fl)


def _context():
    from models.results import AgentContext

    event = PushEvent(
        ref="refs/heads/main",
        before="0" * 40,
        after="a" * 40,
        repository=Repository(name="demo", clone_url=""),
        pusher=Pusher(name="Dev", email="dev@example.com"),
        commits=[
            Commit(
                id="a" * 40,
                timestamp="2026-01-01T00:00:00Z",
                message="change",
                author=Author(name="Dev", email="dev@example.com"),
                added=[],
                removed=[],
                modified=["app.py"],
            )
        ],
    )
    return AgentContext(push_event=event, git_diff="+ def f():\n+    return 1/0")


def test_subgraph_returns_structured_findings():
    finding = Finding(
        title="Division by zero",
        description="f() always raises ZeroDivisionError",
        severity=Severity.HIGH,
        file_path="app.py",
        line_number=2,
        recommendation="Guard the denominator",
    )
    llm = FakeChatModel(
        AIMessage(content="I reviewed the diff and found a division-by-zero bug."),
        FindingList(findings=[finding]),
    )
    agent = CodeReviewAgent(llm, Settings())

    result = asyncio.run(agent.run(_context()))

    assert result.status == "success"
    assert len(result.findings) == 1
    assert result.findings[0].title == "Division by zero"
    assert result.findings[0].severity == Severity.HIGH
    # Summary is the model's prose, with no sentinel marker.
    assert "---FINDINGS---" not in result.summary


def test_coerce_text_tool_call_promotes_to_real_tool_call():
    from agents.base import BaseAgent

    # qwen-style: tool call emitted as a fenced JSON block in content, not native tool_calls.
    msg = AIMessage(
        content='```json\n{"name": "fetch_git_diff", "arguments": {"repo_url": "r", "before_sha": "a", "after_sha": "b"}}\n```'
    )
    out = BaseAgent._coerce_text_tool_call(msg, {"fetch_git_diff", "get_file_content"})
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0]["name"] == "fetch_git_diff"
    assert out.tool_calls[0]["args"] == {"repo_url": "r", "before_sha": "a", "after_sha": "b"}


def test_coerce_leaves_plain_analysis_untouched():
    from agents.base import BaseAgent

    msg = AIMessage(content="I reviewed the code and found a SQL injection risk.")
    out = BaseAgent._coerce_text_tool_call(msg, {"fetch_git_diff"})
    assert out.tool_calls == []
    assert out is msg  # unchanged


def test_subgraph_handles_no_findings():
    llm = FakeChatModel(
        AIMessage(content="No issues found."),
        FindingList(findings=[]),
    )
    agent = CodeReviewAgent(llm, Settings())

    result = asyncio.run(agent.run(_context()))

    assert result.status == "success"
    assert result.findings == []


def test_graph_compiles_without_error():
    llm = FakeChatModel(AIMessage(content="ok"), FindingList())
    agent = CodeReviewAgent(llm, Settings())
    graph = agent._build_graph([])
    assert graph is not None


def test_extraction_retry_fires_when_first_returns_empty():
    """Retry logic: if first structured extraction → 0 findings but prose is non-empty, call again."""
    call_count = {"n": 0}
    retry_finding = Finding(
        title="Retry Found",
        description="found on second call",
        severity=Severity.LOW,
        recommendation="fix",
    )

    class _RetryFakeLLM(FakeChatModel):
        def with_structured_output(self, schema):
            class _Counter:
                async def ainvoke(self_, messages, *args, **kwargs):
                    call_count["n"] += 1
                    if call_count["n"] == 1:
                        return FindingList(findings=[])  # first call: empty
                    return FindingList(findings=[retry_finding])

            return _Counter()

    llm = _RetryFakeLLM(
        AIMessage(content="There is a problem here, a division by zero."),
        FindingList(findings=[]),
    )
    agent = CodeReviewAgent(llm, Settings(extraction_retry=True))
    result = asyncio.run(agent.run(_context()))

    assert call_count["n"] == 2
    assert len(result.findings) == 1
    assert result.findings[0].title == "Retry Found"


def test_extraction_retry_disabled_does_not_retry():
    call_count = {"n": 0}

    class _CountingLLM(FakeChatModel):
        def with_structured_output(self, schema):
            class _Counter:
                async def ainvoke(self_, messages, *args, **kwargs):
                    call_count["n"] += 1
                    return FindingList(findings=[])

            return _Counter()

    llm = _CountingLLM(AIMessage(content="There is a problem."), FindingList())
    agent = CodeReviewAgent(llm, Settings(extraction_retry=False))
    asyncio.run(agent.run(_context()))

    assert call_count["n"] == 1
