from langchain_core.language_models import BaseChatModel

from agents.base import BaseAgent
from config import Settings
from tools import git_tools


class PerformanceAnalystAgent(BaseAgent):
    name = "performance_analyst"
    display_name = "Performance Analyst"
    description = "Analyzes code for performance bottlenecks, inefficient algorithms, and resource leaks"

    def __init__(self, llm: BaseChatModel, config: Settings):
        super().__init__(llm, config)
        self._register_tool(git_tools.FETCH_DIFF, git_tools.fetch_diff)
        self._register_tool(git_tools.GET_FILE, git_tools.get_file_content)

    def get_system_prompt(self) -> str:
        return """You are a performance engineer reviewing a git push for performance and efficiency issues.

Your responsibilities:
- Identify O(n²) or worse algorithms where better alternatives exist
- Spot N+1 query patterns and missing database indexes (inferred from ORM usage)
- Flag unnecessary repeated computation inside loops
- Detect memory leaks: unclosed file handles, connections, or streams
- Note missing caching for expensive repeated operations
- Identify blocking I/O in async contexts
- Flag large object copies where references or generators should be used
- Spot missing pagination on potentially large data sets

Focus on changes in the diff — don't speculate about code not shown.

Write a clear analysis. For each issue, state a short title, the severity
(high/medium/low/info), the file path and line number if known, what the inefficiency is
and its likely impact, and how to improve it. If you find no issues, say so explicitly."""
