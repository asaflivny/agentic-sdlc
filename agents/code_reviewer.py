from langchain_core.language_models import BaseChatModel

from agents.base import BaseAgent
from config import Settings
from tools import git_tools


class CodeReviewAgent(BaseAgent):
    name = "code_reviewer"
    display_name = "Code Reviewer"
    description = "Reviews code for quality, correctness, maintainability, and best practices"

    def __init__(self, llm: BaseChatModel, config: Settings):
        super().__init__(llm, config)
        self._register_tool(git_tools.FETCH_DIFF, git_tools.fetch_diff)
        self._register_tool(git_tools.GET_FILE, git_tools.get_file_content)
        self._register_tool(git_tools.LIST_FILES, git_tools.list_changed_files)
        self._register_tool(git_tools.FILE_HISTORY, git_tools.analyze_file_history)

    def get_system_prompt(self) -> str:
        return """You are a senior software engineer performing a code review on a git push.

Your responsibilities:
- Identify bugs, logic errors, and incorrect behavior
- Flag violations of coding standards and best practices
- Spot missing error handling, edge cases, and null/undefined risks
- Note overly complex code that should be simplified
- Point out security anti-patterns (hardcoded secrets, SQL injection, XSS risks)
- Suggest missing tests for critical logic

Be precise: reference file names and line numbers when possible.
Focus on what matters — skip trivial style nits unless they indicate a deeper problem.

Write a clear analysis. For each issue you find, state a short title, the severity
(critical/high/medium/low/info), the file path and line number if known, what the problem
is, and how to fix it. If you find no issues, say so explicitly."""
