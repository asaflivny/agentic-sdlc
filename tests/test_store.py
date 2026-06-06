"""Tests for WorkflowStore — SQLite round-trip via tmp_path fixture."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from models.results import AgentResult, Finding, Severity, WorkflowResult
from store import WorkflowStore


def _make_result(
    workflow: str = "quick_review",
    repo: str = "test-repo",
    branch: str = "main",
    findings: list[Finding] | None = None,
) -> WorkflowResult:
    now = datetime.now(timezone.utc)
    agent_findings = findings or []
    return WorkflowResult(
        workflow_name=workflow,
        repo_name=repo,
        branch=branch,
        started_at=now,
        completed_at=now,
        agent_results=[
            AgentResult(
                agent_name="code_reviewer",
                status="success",
                findings=agent_findings,
                summary="test summary",
                duration_seconds=1.5,
            )
        ],
        overall_summary="ok",
    )


def _finding(title: str = "SQL Injection", sev: Severity = Severity.HIGH) -> Finding:
    return Finding(
        title=title,
        description="desc",
        severity=sev,
        file_path="src/db.py",
        line_number=42,
        recommendation="fix it",
    )


@pytest.fixture
def store(tmp_path: Path):
    db = str(tmp_path / "test.db")
    s = WorkflowStore(db)
    asyncio.run(s.setup())
    return s


def test_save_and_get_run(store):
    result = _make_result()
    asyncio.run(store.save("run-1", result))
    fetched = asyncio.run(store.get_run("run-1"))
    assert fetched is not None
    assert fetched["workflow_name"] == "quick_review"
    assert fetched["repo_name"] == "test-repo"


def test_get_run_not_found(store):
    assert asyncio.run(store.get_run("nonexistent")) is None


def test_list_runs_returns_saved(store):
    asyncio.run(store.save("run-a", _make_result(repo="repo1")))
    asyncio.run(store.save("run-b", _make_result(repo="repo2")))
    runs = asyncio.run(store.list_runs())
    run_ids = {r["run_id"] for r in runs}
    assert "run-a" in run_ids
    assert "run-b" in run_ids


def test_list_runs_filter_by_repo(store):
    asyncio.run(store.save("run-a", _make_result(repo="alpha")))
    asyncio.run(store.save("run-b", _make_result(repo="beta")))
    runs = asyncio.run(store.list_runs(repo="alpha"))
    assert all(r["repo"] == "alpha" for r in runs)
    assert len(runs) == 1


def test_list_runs_filter_by_branch(store):
    asyncio.run(store.save("run-a", _make_result(branch="main")))
    asyncio.run(store.save("run-b", _make_result(branch="dev")))
    runs = asyncio.run(store.list_runs(branch="main"))
    assert all(r["branch"] == "main" for r in runs)


def test_severity_counts_in_list(store):
    findings = [_finding("bug1", Severity.CRITICAL), _finding("bug2", Severity.HIGH)]
    asyncio.run(store.save("run-x", _make_result(findings=findings)))
    runs = asyncio.run(store.list_runs())
    row = next(r for r in runs if r["run_id"] == "run-x")
    assert row["critical_count"] == 1
    assert row["high_count"] == 1
    assert row["medium_count"] == 0


def test_get_stats_counts_runs(store):
    asyncio.run(store.save("run-1", _make_result(findings=[_finding()])))
    asyncio.run(store.save("run-2", _make_result(findings=[_finding(), _finding("b")])))
    stats = asyncio.run(store.get_stats())
    assert stats["total_runs"] == 2
    assert stats["total_findings"] == 3


def test_get_stats_empty(store):
    stats = asyncio.run(store.get_stats())
    assert stats["total_runs"] == 0
    assert stats["total_findings"] == 0


def test_findings_trend_returns_rows(store):
    asyncio.run(store.save("run-1", _make_result(repo="myrepo", findings=[_finding()])))
    trend = asyncio.run(store.get_findings_trend("myrepo", days=30))
    assert isinstance(trend, list)
    assert len(trend) >= 1
    row = trend[0]
    assert "date" in row
    assert "severity" in row
    assert "count" in row


def test_findings_trend_empty_for_unknown_repo(store):
    asyncio.run(store.save("run-1", _make_result(repo="known")))
    trend = asyncio.run(store.get_findings_trend("unknown", days=30))
    assert trend == []
