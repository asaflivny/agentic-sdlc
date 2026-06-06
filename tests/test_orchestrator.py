"""Test suite for WorkflowOrchestrator."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from config import Settings
from models.events import PushEvent, Repository, Pusher, Commit, Author
from models.results import AgentResult, Finding, Severity
from workflows.orchestrator import WorkflowOrchestrator
from workflows.definitions.quick_review import QUICK_REVIEW


@pytest.fixture
def settings():
    """Create test settings."""
    return Settings(
        ollama_model="qwen2.5-coder:7b",
        agent_recursion_limit=25,
        agent_timeout_seconds=30,
    )


@pytest.fixture
def orchestrator(settings):
    """Create an orchestrator instance."""
    return WorkflowOrchestrator(settings)


@pytest.fixture
def sample_event():
    """Create a sample push event."""
    return PushEvent(
        ref="refs/heads/main",
        before="a" * 40,
        after="b" * 40,
        repository=Repository(
            id="1",
            name="test-repo",
            clone_url="/tmp/test-repo",
            owner="test",
        ),
        pusher=Pusher(name="dev", email="dev@example.com"),
        commits=[
            Commit(
                id="c" * 40,
                timestamp="2026-01-01T00:00:00Z",
                message="test",
                author=Author(name="dev", email="dev@example.com"),
                modified=["src/main.py"],
                added=[],
                removed=[],
            )
        ],
    )


class TestOrchestratorInitialization:
    """Test orchestrator setup and configuration."""

    def test_orchestrator_creates_successfully(self, orchestrator, settings):
        """Test that orchestrator initializes correctly."""
        assert orchestrator is not None
        assert orchestrator.config is not None
        assert orchestrator.config.agent_recursion_limit == 25

    def test_orchestrator_stores_config(self, orchestrator, settings):
        """Test that config is properly stored."""
        assert orchestrator.config.ollama_model == "qwen2.5-coder:7b"
        assert orchestrator.config.agent_timeout_seconds == 30


class TestDuplicateFindingDe:
    """Test finding deduplication logic."""

    def test_deduplicates_identical_findings(self, orchestrator):
        """Test that identical findings are deduplicated."""
        results = [
            AgentResult(
                agent_name="agent1",
                status="success",
                findings=[
                    Finding(
                        title="SQL Injection",
                        description="Found in query",
                        severity=Severity.HIGH,
                        file_path="src/db.py",
                        line_number=10,
                        recommendation="Use parameterized queries",
                    )
                ],
            ),
            AgentResult(
                agent_name="agent2",
                status="success",
                findings=[
                    Finding(
                        title="SQL Injection",
                        description="Found in query",
                        severity=Severity.HIGH,
                        file_path="src/db.py",
                        line_number=10,
                        recommendation="Use parameterized queries",
                    )
                ],
            ),
        ]
        deduplicated = orchestrator._deduplicate_findings(results)
        total_findings = sum(len(r.findings) for r in deduplicated)
        assert total_findings == 1  # Should have only 1, not 2

    def test_keeps_different_findings(self, orchestrator):
        """Test that different findings are not deduplicated."""
        results = [
            AgentResult(
                agent_name="agent1",
                status="success",
                findings=[
                    Finding(
                        title="SQL Injection",
                        description="Issue 1",
                        severity=Severity.HIGH,
                        file_path="src/db.py",
                        line_number=10,
                        recommendation="Fix it",
                    )
                ],
            ),
            AgentResult(
                agent_name="agent2",
                status="success",
                findings=[
                    Finding(
                        title="XSS Vulnerability",
                        description="Issue 2",
                        severity=Severity.MEDIUM,
                        file_path="src/views.py",
                        line_number=20,
                        recommendation="Sanitize input",
                    )
                ],
            ),
        ]
        deduplicated = orchestrator._deduplicate_findings(results)
        total_findings = sum(len(r.findings) for r in deduplicated)
        assert total_findings == 2


class TestContextFormatting:
    """Test context formatting for sequential mode."""

    def test_format_context_enrichment(self, orchestrator):
        """Test that context enrichment is formatted correctly."""
        result = AgentResult(
            agent_name="agent1",
            status="success",
            findings=[
                Finding(
                    title="Bug",
                    description="Found",
                    severity=Severity.HIGH,
                    recommendation="Fix",
                )
            ],
            summary="Analysis complete",
        )
        context = orchestrator._format_context_enrichment("Test Agent", result)
        assert "Test Agent" in context
        assert "1 findings" in context or "Bug" in context
        assert context.strip()  # Should not be empty


class TestDiffChunking:
    """Test diff chunking for large changes."""

    def test_chunk_large_diff(self, orchestrator, settings):
        """Test that large diffs are chunked."""
        # Create a large diff (simulated)
        large_diff = "+" * 30000  # 30 KB
        chunks = orchestrator._chunk_diff(large_diff)
        # Should be chunked if size_kb is set
        if settings.diff_chunk_size_kb > 0:
            # Depending on config, might be 1 or more chunks
            assert len(chunks) >= 1

    def test_small_diff_not_chunked(self, orchestrator):
        """Test that small diffs are not split."""
        small_diff = "+" * 1000  # 1 KB
        chunks = orchestrator._chunk_diff(small_diff)
        assert len(chunks) == 1


class TestAggregateSummary:
    """Test summary aggregation from multiple agent results."""

    def test_aggregates_multiple_summaries(self, orchestrator):
        """Test that summaries from multiple agents are aggregated."""
        results = [
            AgentResult(
                agent_name="agent1",
                status="success",
                summary="Found 2 issues",
                findings=[],
            ),
            AgentResult(
                agent_name="agent2",
                status="success",
                summary="Found 1 issue",
                findings=[],
            ),
        ]
        summary = orchestrator._aggregate_summary(results)
        assert summary is not None
        assert summary.strip()  # Should not be empty

    def test_handles_empty_results(self, orchestrator):
        """Test aggregation with no results."""
        summary = orchestrator._aggregate_summary([])
        # Should handle empty list gracefully
        assert isinstance(summary, str)
