import asyncio
import os
from pathlib import Path

from tools.base import ToolDefinition, ToolParameter, ToolResult

_repo_cache: dict[str, Path] = {}


async def _git(args: list[str], cwd: str | None = None, git_dir: str | None = None) -> str:
    cmd = ["git"]
    if git_dir:
        cmd += ["--git-dir", git_dir]
    cmd += args
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode().strip() or f"git {args[0]} failed")
    return stdout.decode()


def _resolve_git_dir(repo_url: str) -> str | None:
    if not repo_url:
        return None
    p = Path(repo_url)
    if p.exists():
        if (p / "HEAD").exists():
            return str(p)
        if (p / ".git").exists():
            return str(p / ".git")
    return None


async def fetch_diff(repo_url: str, before_sha: str, after_sha: str) -> ToolResult:
    git_dir = _resolve_git_dir(repo_url)
    if not git_dir:
        return ToolResult("", f"Cannot resolve repo at: {repo_url}", is_error=True)
    zeros = "0" * 40
    try:
        if before_sha == zeros:
            output = await _git(["show", "--stat", after_sha], git_dir=git_dir)
        else:
            output = await _git(["diff", f"{before_sha}..{after_sha}"], git_dir=git_dir)
        return ToolResult("", output[:30000])
    except RuntimeError as e:
        return ToolResult("", str(e), is_error=True)


async def get_file_content(repo_url: str, file_path: str, ref: str) -> ToolResult:
    git_dir = _resolve_git_dir(repo_url)
    if not git_dir:
        return ToolResult("", f"Cannot resolve repo at: {repo_url}", is_error=True)
    try:
        output = await _git(["show", f"{ref}:{file_path}"], git_dir=git_dir)
        return ToolResult("", output[:20000])
    except RuntimeError as e:
        return ToolResult("", str(e), is_error=True)


async def list_changed_files(repo_url: str, before_sha: str, after_sha: str) -> ToolResult:
    git_dir = _resolve_git_dir(repo_url)
    if not git_dir:
        return ToolResult("", f"Cannot resolve repo at: {repo_url}", is_error=True)
    zeros = "0" * 40
    try:
        if before_sha == zeros:
            output = await _git(["show", "--name-status", "--format=", after_sha], git_dir=git_dir)
        else:
            output = await _git(["diff", "--name-status", f"{before_sha}..{after_sha}"], git_dir=git_dir)
        return ToolResult("", output)
    except RuntimeError as e:
        return ToolResult("", str(e), is_error=True)


FETCH_DIFF = ToolDefinition(
    name="fetch_git_diff",
    description="Get the full git diff between two commits in a repository",
    parameters=[
        ToolParameter("repo_url", "string", "Local path or URL to the git repository"),
        ToolParameter("before_sha", "string", "The commit SHA before the change"),
        ToolParameter("after_sha", "string", "The commit SHA after the change"),
    ],
)

GET_FILE = ToolDefinition(
    name="get_file_content",
    description="Get the content of a specific file at a given git ref",
    parameters=[
        ToolParameter("repo_url", "string", "Local path or URL to the git repository"),
        ToolParameter("file_path", "string", "Path to the file within the repository"),
        ToolParameter("ref", "string", "Git ref (commit SHA, branch, or tag)"),
    ],
)

LIST_FILES = ToolDefinition(
    name="list_changed_files",
    description="List all files changed between two commits with their change status (A/M/D)",
    parameters=[
        ToolParameter("repo_url", "string", "Local path or URL to the git repository"),
        ToolParameter("before_sha", "string", "The commit SHA before the change"),
        ToolParameter("after_sha", "string", "The commit SHA after the change"),
    ],
)

EXECUTORS = {
    FETCH_DIFF.name: fetch_diff,
    GET_FILE.name: get_file_content,
    LIST_FILES.name: list_changed_files,
}
