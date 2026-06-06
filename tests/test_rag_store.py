"""Unit tests for RAGStore."""

import asyncio
import tempfile


from rag import RAGStore
from models.results import Finding, WorkflowResult, AgentResult


def test_rag_store_setup_cleanup():
    """Test that RAGStore initializes and cleans up properly."""

    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RAGStore(tmpdir, "sentence-transformers/all-MiniLM-L6-v2")
            await store.setup()
            assert store.client is not None
            assert len(store.collections) == len(RAGStore.COLLECTIONS)
            await store.cleanup()

    asyncio.run(_test())


def test_index_documents():
    """Test indexing documents into a collection."""

    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            rag_store = RAGStore(tmpdir, "sentence-transformers/all-MiniLM-L6-v2")
            await rag_store.setup()

            documents = [
                {
                    "content": "Security best practice: always validate user input",
                    "metadata": {"repo": "global", "type": "security"},
                    "source": "api_ingest",
                },
                {
                    "content": "Performance tip: use database indexes for frequently queried columns",
                    "metadata": {"repo": "global", "type": "performance"},
                    "source": "api_ingest",
                },
            ]

            await rag_store.index_documents("best_practices", documents)

            # Verify documents were indexed
            stats = await rag_store.list_collections()
            assert stats["best_practices"] == 2
            await rag_store.cleanup()

    asyncio.run(_test())


def test_search_documents():
    """Test searching for documents."""

    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            rag_store = RAGStore(tmpdir, "sentence-transformers/all-MiniLM-L6-v2")
            await rag_store.setup()

            documents = [
                {
                    "content": "SQL injection is a critical security vulnerability. Always use parameterized queries.",
                    "metadata": {"repo": "global", "severity": "critical"},
                },
                {
                    "content": "Cross-site scripting (XSS) attacks can be prevented by escaping user input.",
                    "metadata": {"repo": "global", "severity": "high"},
                },
            ]

            await rag_store.index_documents("known_issues", documents)

            # Search for security-related documents
            results = await rag_store.search("known_issues", "SQL injection vulnerability", limit=5)

            assert len(results) > 0
            assert isinstance(results, list)
            await rag_store.cleanup()

    asyncio.run(_test())


def test_index_findings():
    """Test auto-indexing findings from a workflow result."""

    async def _test():
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as tmpdir:
            rag_store = RAGStore(tmpdir, "sentence-transformers/all-MiniLM-L6-v2")
            await rag_store.setup()

            finding = Finding(
                title="Missing input validation",
                description="User input is not validated before processing",
                severity="high",
                file_path="app/api.py",
                line_number=42,
                recommendation="Add validation using a schema validator",
            )

            result = WorkflowResult(
                workflow_name="full_review",
                repo_name="test-repo",
                branch="main",
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                agent_results=[
                    AgentResult(
                        agent_name="code_reviewer",
                        status="success",
                        findings=[finding],
                        summary="Found 1 issue",
                    )
                ],
                overall_summary="Analysis complete",
            )

            await rag_store.index_findings("run_123", result)

            # Verify findings were indexed
            stats = await rag_store.list_collections()
            assert stats["findings_shared"] >= 1
            await rag_store.cleanup()

    asyncio.run(_test())


def test_clear_collection():
    """Test clearing a collection."""

    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            rag_store = RAGStore(tmpdir, "sentence-transformers/all-MiniLM-L6-v2")
            await rag_store.setup()

            documents = [
                {"content": "Document 1", "metadata": {"repo": "global"}},
                {"content": "Document 2", "metadata": {"repo": "global"}},
            ]

            await rag_store.index_documents("best_practices", documents)
            stats_before = await rag_store.list_collections()
            assert stats_before["best_practices"] == 2

            await rag_store.clear_collection("best_practices")
            stats_after = await rag_store.list_collections()
            assert stats_after["best_practices"] == 0
            await rag_store.cleanup()

    asyncio.run(_test())


def test_chunk_text():
    """Test text chunking with overlap."""
    store = RAGStore("/tmp/dummy")

    text = "A" * 1000  # 1000 character string
    chunks = store._chunk_text(text, chunk_size=500)

    assert len(chunks) > 1  # Should be split into multiple chunks
    assert all(len(c) <= 550 for c in chunks)  # Each chunk should be ~500 with some overlap


def test_list_collections():
    """Test listing collections and their document counts."""

    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            rag_store = RAGStore(tmpdir, "sentence-transformers/all-MiniLM-L6-v2")
            await rag_store.setup()

            # Index some documents
            await rag_store.index_documents(
                "best_practices",
                [
                    {"content": "Doc 1", "metadata": {"repo": "global"}},
                    {"content": "Doc 2", "metadata": {"repo": "global"}},
                ],
            )

            await rag_store.index_documents(
                "known_issues",
                [{"content": "Issue 1", "metadata": {"repo": "global"}}],
            )

            stats = await rag_store.list_collections()

            assert "best_practices" in stats
            assert "known_issues" in stats
            assert stats["best_practices"] == 2
            assert stats["known_issues"] == 1
            await rag_store.cleanup()

    asyncio.run(_test())
