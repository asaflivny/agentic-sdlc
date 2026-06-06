from langchain_core.language_models import BaseChatModel

from agents.base import BaseAgent
from config import Settings
from tools import git_tools


class TestCoverageAgent(BaseAgent):
    name = "test_coverage"
    display_name = "Test Coverage Checker"
    description = "Checks whether changed Python files have corresponding test coverage"

    def __init__(self, llm: BaseChatModel, config: Settings, rag_store=None):
        super().__init__(llm, config, rag_store)
        self._register_tool(git_tools.LIST_FILES, git_tools.list_changed_files)
        self._register_tool(git_tools.GET_FILE, git_tools.get_file_content)

    def get_system_prompt(self) -> str:
        return """You are a test coverage reviewer checking a git push.

Your job:
- Identify Python source files (.py) that were added or significantly modified in this push
- For each such file, check whether a corresponding test file exists
  (e.g. `src/foo.py` → `tests/test_foo.py`, `test_foo.py`, or `tests/foo_test.py`)
- Use list_changed_files to see what changed, then get_file_content to verify whether a
  test file exists at the expected path (if get_file_content returns an error, the file doesn't exist)
- Flag source files that introduce new functions, classes, or branching logic with no test counterpart

Do NOT flag:
- __init__.py, configuration files, migration files, scripts, or data files
- Changes that are purely comments, docstrings, or trivial formatting
- Files that already have a test file at any reasonable path
- Non-Python files

Write a clear analysis. For each source file lacking a test, state a short title (e.g.
"No test file for path/to/module.py"), severity "low", the file path, and a recommendation
(e.g. "Add tests/test_module.py covering the new functionality"). If all changed files
have tests or do not require them, say so explicitly."""
