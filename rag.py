import json
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from pydantic import BaseModel

from models.results import Finding, WorkflowResult

logger = logging.getLogger(__name__)


class RAGDocument(BaseModel):
    """Document stored in RAG knowledge base."""
    id: str
    content: str
    metadata: dict = {}
    source: str  # "file", "finding", "api_ingest"
    created_at: datetime


class RAGStore:
    """Manages RAG knowledge base using Chroma for vector storage."""

    COLLECTIONS = {
        "business_knowledge": "Business docs, architecture guides, service agreements",
        "best_practices": "Security guidelines, performance patterns, coding standards",
        "known_issues": "Bugs, caveats, customer-specific constraints",
        "findings_shared": "Historical findings across all repos",
        "code_patterns": "Anti-patterns, refactoring examples, common mistakes",
    }

    def __init__(self, db_path: str, embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        """Initialize RAGStore with Chroma client.

        Args:
            db_path: Path to persistent Chroma database
            embedding_model: HuggingFace embedding model identifier
        """
        self.db_path = db_path
        self.embedding_model = embedding_model
        self.client = None
        self.collections = {}

    async def setup(self):
        """Initialize Chroma client and collections."""
        try:
            # Ensure db_path directory exists
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

            # Initialize Chroma client with persistent storage
            settings = ChromaSettings(
                is_persistent=True,
                persist_directory=self.db_path,
                allow_reset=False,
            )
            self.client = chromadb.Client(settings)

            # Create or get collections
            for collection_name in self.COLLECTIONS.keys():
                self.collections[collection_name] = self.client.get_or_create_collection(
                    name=collection_name,
                    metadata={"hnsw:space": "cosine"},
                )

            logger.info(f"RAG store initialized with {len(self.collections)} collections at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize RAG store: {e}")
            raise

    async def cleanup(self):
        """Clean up Chroma client."""
        if self.client:
            try:
                # Chroma persists automatically; no explicit cleanup needed
                logger.info("RAG store cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up RAG store: {e}")

    async def index_documents(self, collection: str, documents: list[dict]):
        """Index documents into a collection.

        Args:
            collection: Collection name
            documents: List of dicts with keys: content, metadata (optional), source (optional)
        """
        if collection not in self.collections:
            logger.warning(f"Collection {collection} not found")
            return

        coll = self.collections[collection]

        ids = []
        texts = []
        metadatas = []

        for doc in documents:
            content = doc.get("content", "")
            metadata = doc.get("metadata", {})
            source = doc.get("source", "api_ingest")

            # Generate unique ID from content hash + metadata to avoid overwrites
            hash_input = content + json.dumps(metadata, sort_keys=True, default=str)
            doc_id = hashlib.md5(hash_input.encode()).hexdigest()

            ids.append(doc_id)
            texts.append(content)
            metadatas.append({
                **metadata,
                "source": source,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        try:
            coll.add(ids=ids, documents=texts, metadatas=metadatas)
            logger.info(f"Indexed {len(documents)} documents into {collection}")
        except Exception as e:
            logger.error(f"Failed to index documents into {collection}: {e}")
            raise

    async def index_findings(self, run_id: str, result: WorkflowResult):
        """Auto-index findings from a workflow result.

        Args:
            run_id: Workflow run ID
            result: WorkflowResult containing findings
        """
        documents = []

        for agent_result in result.agent_results:
            for finding in agent_result.findings:
                # Build searchable document from finding
                content = self._format_finding_for_search(finding, result.repo_name, agent_result.agent_name)

                documents.append({
                    "content": content,
                    "metadata": {
                        "repo": result.repo_name,
                        "branch": result.branch,
                        "agent": agent_result.agent_name,
                        "severity": finding.severity.value,
                        "title": finding.title,
                        "file_path": finding.file_path or "",
                        "run_id": run_id,
                    },
                    "source": "finding",
                })

        if documents:
            try:
                await self.index_documents("findings_shared", documents)
            except Exception as e:
                logger.error(f"Failed to index findings from run {run_id}: {e}")

    async def index_file(self, collection: str, file_path: str, chunk_size: int = 500, repo: str = "global"):
        """Index a markdown or text file, chunking if necessary.

        Args:
            collection: Target collection
            file_path: Path to file to index
            chunk_size: Characters per chunk
            repo: Repository scope for metadata
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Failed to read file {file_path}: {e}")
            return

        # Split into chunks with overlap
        chunks = self._chunk_text(content, chunk_size=chunk_size)

        documents = []
        for i, chunk in enumerate(chunks):
            documents.append({
                "content": chunk,
                "metadata": {
                    "repo": repo,
                    "source_file": Path(file_path).name,
                    "chunk": i,
                },
                "source": "file",
            })

        await self.index_documents(collection, documents)

    async def search(self, collection: str, query: str, limit: int = 5, where: Optional[dict] = None) -> list[dict]:
        """Search for documents in a collection.

        Args:
            collection: Collection name
            query: Search query string
            limit: Max results to return
            where: Optional Chroma where filter (e.g., {"repo": {"$eq": "my-repo"}})

        Returns:
            List of search results with content and metadata
        """
        if collection not in self.collections:
            logger.warning(f"Collection {collection} not found")
            return []

        try:
            coll = self.collections[collection]
            results = coll.query(query_texts=[query], n_results=limit, where=where)

            # Format results
            formatted = []
            if results and results["documents"] and results["documents"][0]:
                for i, doc in enumerate(results["documents"][0]):
                    formatted.append({
                        "content": doc,
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else 0,
                    })

            return formatted
        except Exception as e:
            logger.error(f"Search failed in {collection}: {e}")
            return []

    async def search_findings_by_repo(self, repo: str, query: str = "", limit: int = 5) -> list[Finding]:
        """Search for past findings for a specific repository.

        Args:
            repo: Repository name
            query: Optional search query (if empty, returns recent findings)
            limit: Max results

        Returns:
            List of Finding objects
        """
        where = {"repo": {"$eq": repo}} if repo else None

        # If no query, search for wildcard or just get recent findings
        search_query = query if query else repo

        results = await self.search("findings_shared", search_query, limit=limit, where=where)

        # Reconstruct Finding objects from search results
        findings = []
        for result in results:
            meta = result.get("metadata", {})
            finding = Finding(
                title=meta.get("title", ""),
                description=result.get("content", ""),
                severity=meta.get("severity", "info"),
                file_path=meta.get("file_path") or None,
                recommendation="(Retrieved from knowledge base)",
            )
            findings.append(finding)

        return findings

    async def find_similar_findings(self, finding: Finding, threshold: float = 0.7, limit: int = 5) -> list[dict]:
        """Find findings similar to the given one.

        Args:
            finding: Finding to search for
            threshold: Minimum similarity score (0-1, cosine distance)
            limit: Max results

        Returns:
            List of similar findings (dict format)
        """
        # Search using finding title + description
        query = f"{finding.title} {finding.description}"
        results = await self.search("findings_shared", query, limit=limit)

        # Filter by threshold
        filtered = [r for r in results if r.get("distance", 1) <= (1 - threshold)]

        return filtered

    async def list_collections(self) -> dict[str, int]:
        """List all collections and their document counts.

        Returns:
            Dict of collection names to document counts
        """
        stats = {}
        for name, coll in self.collections.items():
            try:
                count = coll.count()
                stats[name] = count
            except Exception as e:
                logger.error(f"Failed to count documents in {name}: {e}")
                stats[name] = 0

        return stats

    async def delete_collection(self, collection: str):
        """Delete a collection.

        Args:
            collection: Collection name
        """
        if collection not in self.collections:
            logger.warning(f"Collection {collection} not found")
            return

        try:
            self.client.delete_collection(name=collection)
            del self.collections[collection]
            logger.info(f"Deleted collection {collection}")
        except Exception as e:
            logger.error(f"Failed to delete collection {collection}: {e}")
            raise

    async def clear_collection(self, collection: str):
        """Clear all documents from a collection (keep the collection).

        Args:
            collection: Collection name
        """
        if collection not in self.collections:
            logger.warning(f"Collection {collection} not found")
            return

        try:
            # Delete all documents by deleting and recreating collection
            self.client.delete_collection(name=collection)
            self.collections[collection] = self.client.get_or_create_collection(
                name=collection,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"Cleared collection {collection}")
        except Exception as e:
            logger.error(f"Failed to clear collection {collection}: {e}")
            raise

    def _chunk_text(self, text: str, chunk_size: int = 500) -> list[str]:
        """Split text into overlapping chunks.

        Args:
            text: Text to chunk
            chunk_size: Characters per chunk

        Returns:
            List of chunks with 10% overlap
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        overlap = int(chunk_size * 0.1)  # 10% overlap
        step = chunk_size - overlap

        for i in range(0, len(text), step):
            chunk = text[i : i + chunk_size]
            if chunk.strip():
                chunks.append(chunk)

        return chunks

    def _format_finding_for_search(self, finding: Finding, repo: str, agent: str) -> str:
        """Format a finding as searchable text.

        Args:
            finding: Finding object
            repo: Repository name
            agent: Agent name

        Returns:
            Formatted string for indexing
        """
        parts = [
            f"Finding: {finding.title}",
            f"Severity: {finding.severity.value}",
            f"Agent: {agent}",
            f"Repository: {repo}",
        ]

        if finding.description:
            parts.append(f"Description: {finding.description}")

        if finding.file_path:
            parts.append(f"File: {finding.file_path}")

        if finding.recommendation:
            parts.append(f"Recommendation: {finding.recommendation}")

        return "\n".join(parts)
