import json
import logging

from tools.base import ToolDefinition, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


SEARCH_KNOWLEDGE = ToolDefinition(
    name="search_knowledge",
    description="Search business knowledge base for best practices, known issues, customer context, and past findings. Use this to find relevant information during code analysis.",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Search query (e.g., 'security guidelines', 'known issues with authentication', 'performance optimization')",
            required=True,
        ),
        ToolParameter(
            name="collection",
            type="string",
            description="Collection to search: business_knowledge, best_practices, known_issues, findings_shared, or code_patterns",
            required=True,
            enum=[
                "business_knowledge",
                "best_practices",
                "known_issues",
                "findings_shared",
                "code_patterns",
            ],
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Maximum number of results to return",
            required=False,
        ),
    ],
)


async def search_knowledge(
    query: str,
    collection: str,
    limit: int = 5,
    rag_store=None,
) -> ToolResult:
    """Search the RAG knowledge base.

    Args:
        query: Search query
        collection: Collection to search
        limit: Max results
        rag_store: RAGStore instance (injected via tool executor)

    Returns:
        ToolResult with search results
    """
    if not rag_store:
        return ToolResult(
            tool_call_id="search_knowledge",
            content="ERROR: RAG store not available",
            is_error=True,
        )

    try:
        results = await rag_store.search(collection, query, limit=limit)

        if not results:
            return ToolResult(
                tool_call_id="search_knowledge",
                content=json.dumps(
                    {"results": [], "message": f"No results found for '{query}' in {collection}"}
                ),
                is_error=False,
            )

        # Format results for display
        formatted_results = []
        for r in results:
            formatted_results.append(
                {
                    "content": r.get("content", "")[:500],  # Truncate long content
                    "metadata": r.get("metadata", {}),
                    "relevance": 1 - r.get("distance", 0),  # Convert distance to relevance
                }
            )

        return ToolResult(
            tool_call_id="search_knowledge",
            content=json.dumps(
                {
                    "query": query,
                    "collection": collection,
                    "results_count": len(formatted_results),
                    "results": formatted_results,
                }
            ),
            is_error=False,
        )

    except Exception as e:
        logger.error(f"Error searching knowledge base: {e}")
        return ToolResult(
            tool_call_id="search_knowledge",
            content=f"ERROR: Search failed: {str(e)}",
            is_error=True,
        )
