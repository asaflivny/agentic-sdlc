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


def test_subgraph_handles_no_findings():
    llm = FakeChatModel(
        AIMessage(content="No issues found."),
        FindingList(findings=[]),
    )
    agent = CodeReviewAgent(llm, Settings())

    result = asyncio.run(agent.run(_context()))

    assert result.status == "success"
    assert result.findings == []
