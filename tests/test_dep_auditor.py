"""Tests for DepAuditAgent — mocks httpx OSV responses."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from agents.dep_auditor import DepAuditAgent, _check_osv, _extract_added_packages
from config import Settings
from models.events import Author, Commit, PushEvent, Pusher, Repository
from models.results import AgentContext


def _make_context(diff: str = "", files: list[str] | None = None) -> AgentContext:
    modified = files or []
    commits = []
    if modified:
        commits = [
            Commit(
                id="a" * 40,
                timestamp="2026-01-01T00:00:00Z",
                message="add deps",
                author=Author(name="dev", email="dev@test.com"),
                added=modified,
                removed=[],
                modified=[],
            )
        ]
    event = PushEvent(
        ref="refs/heads/main",
        before="0" * 40,
        after="b" * 40,
        repository=Repository(name="repo", clone_url="/tmp/repo"),
        pusher=Pusher(name="dev", email="dev@test.com"),
        commits=commits,
    )
    return AgentContext(push_event=event, git_diff=diff)


# ---------------------------------------------------------------------------
# _extract_added_packages
# ---------------------------------------------------------------------------

def test_extract_pinned_requirement():
    diff = "+requests==2.31.0\n"
    pkgs = _extract_added_packages(diff)
    assert any(p["name"] == "requests" and p["version"] == "2.31.0" for p in pkgs)


def test_extract_requirement_with_gte():
    diff = "+flask>=2.0.0\n"
    pkgs = _extract_added_packages(diff)
    assert any(p["name"] == "flask" for p in pkgs)


def test_extract_ignores_removed_lines():
    diff = "-requests==1.0.0\n"
    pkgs = _extract_added_packages(diff)
    assert pkgs == []


def test_extract_ignores_comments():
    diff = "+# this is a comment\n"
    pkgs = _extract_added_packages(diff)
    assert pkgs == []


def test_extract_deduplicates_same_package():
    diff = "+requests==2.31.0\n+requests>=2.0\n"
    pkgs = _extract_added_packages(diff)
    names = [p["name"] for p in pkgs]
    assert names.count("requests") == 1


# ---------------------------------------------------------------------------
# _check_osv (mocked HTTP)
# ---------------------------------------------------------------------------

_OSV_RESPONSE_WITH_VULN = {
    "results": [
        {
            "vulns": [
                {"id": "GHSA-xxxx-yyyy-zzzz", "summary": "Remote code execution in requests"},
            ]
        }
    ]
}

_OSV_RESPONSE_NO_VULN = {"results": [{"vulns": []}]}


def test_check_osv_returns_finding_for_vulnerable_package():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _OSV_RESPONSE_WITH_VULN

    with patch("agents.dep_auditor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        findings = asyncio.run(_check_osv([{"name": "requests", "version": "2.5.0", "ecosystem": "PyPI"}]))

    assert len(findings) == 1
    assert "requests" in findings[0].title
    assert findings[0].severity in ("medium", "high", "critical")


def test_check_osv_no_findings_for_clean_package():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _OSV_RESPONSE_NO_VULN

    with patch("agents.dep_auditor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        findings = asyncio.run(_check_osv([{"name": "safe-pkg", "version": "1.0.0", "ecosystem": "PyPI"}]))

    assert findings == []


def test_check_osv_api_error_returns_empty():
    with patch("agents.dep_auditor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("network error"))
        mock_client_cls.return_value = mock_client

        findings = asyncio.run(_check_osv([{"name": "pkg", "version": "1.0", "ecosystem": "PyPI"}]))

    assert findings == []


# ---------------------------------------------------------------------------
# DepAuditAgent.run
# ---------------------------------------------------------------------------

def test_agent_skips_when_no_dep_files():
    settings = Settings()
    agent = DepAuditAgent(llm=MagicMock(), config=settings)
    ctx = _make_context(diff="+some/other/file.py", files=["src/main.py"])
    result = asyncio.run(agent.run(ctx))
    assert result.status == "success"
    assert result.findings == []
    assert "No dependency file changes" in result.summary


def test_agent_skips_when_no_packages_in_diff():
    settings = Settings()
    agent = DepAuditAgent(llm=MagicMock(), config=settings)
    # dep file changed but diff has no +<package> lines
    ctx = _make_context(diff=" # just a comment\n", files=["requirements.txt"])
    result = asyncio.run(agent.run(ctx))
    assert result.status == "success"
    assert "no new packages detected" in result.summary
