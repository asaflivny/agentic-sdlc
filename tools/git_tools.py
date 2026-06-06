import asyncio
import logging
from pathlib import Path

from tools.base import ToolDefinition, ToolParameter, ToolResult

logger = logging.getLogger(__name__)
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


async def clone_or_verify_repos(repos_root: str, clone_sources: str) -> dict[str, str]:
    """Clone repos on startup from local sources. Returns {repo_name: repo_path}."""
    result = {}

    if not clone_sources or not repos_root:
        logger.info("repos_init skipped (no repos configured)")
        return result

    repos_root_path = Path(repos_root)
    repos_root_path.mkdir(parents=True, exist_ok=True)
    logger.info("repos_root ready path=%s", repos_root)

    # Parse clone sources: "repo1:source1,repo2:source2"
    for item in clone_sources.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue

        repo_name, source_path = item.split(":", 1)
        repo_name = repo_name.strip()
        source_path = source_path.strip()
        repo_path = repos_root_path / repo_name

        try:
            # Check if already cloned
            if repo_path.exists() and (repo_path / ".git").exists():
                logger.info("repo_init action=verify repo=%s path=%s", repo_name, repo_path)
                result[repo_name] = str(repo_path)
                continue

            # Clone from source
            logger.info("repo_init action=clone repo=%s source=%s dest=%s", repo_name, source_path, repo_path)

            # Create destination directory
            repo_path.mkdir(parents=True, exist_ok=True)

            # Clone from HTTP URL or local path
            source = Path(source_path)
            if source_path.startswith(("http://", "https://")):
                # HTTP URL - clone directly
                await _git(["clone", source_path, str(repo_path)])
            elif source.exists():
                if (source / "HEAD").exists() and (source / "objects").exists():
                    # Source is a bare repo
                    await _git(["clone", source_path, str(repo_path)])
                else:
                    # Source is a regular repo, clone with --reference to optimize
                    await _git(["clone", str(source), str(repo_path)])
            else:
                logger.error("repo_init clone_failed repo=%s source_not_found path=%s", repo_name, source_path)
                continue

            logger.info("repo_init clone_complete repo=%s", repo_name)
            result[repo_name] = str(repo_path)
        except RuntimeError as e:
            logger.error("repo_init clone_failed repo=%s error=%s", repo_name, e)

    logger.info("repos_init complete count=%d", len(result))
    return result


async def sync_repo(repo_path: str, branch: str) -> ToolResult:
    """Fetch latest changes from remote and checkout branch. Assumes repo is already cloned."""
    import logging
    logger = logging.getLogger(__name__)

    repo_name = Path(repo_path).name

    # Verify repo exists
    if not Path(repo_path).exists():
        logger.error("sync_repo error=repo_not_found path=%s", repo_path)
        return ToolResult("", f"Repository not found at {repo_path}", is_error=True)

    try:
        # Fetch from all remotes
        logger.info("sync_repo action=fetch repo=%s", repo_name)
        await _git(["fetch", "--all", "--tags"], cwd=repo_path)

        # Checkout branch
        logger.info("sync_repo action=checkout branch=%s repo=%s", branch, repo_name)
        await _git(["checkout", branch], cwd=repo_path)

        # Get current HEAD
        head = await _git(["rev-parse", "HEAD"], cwd=repo_path)
        head = head.strip()[:8]
        logger.info("sync_repo action=complete repo=%s branch=%s head=%s", repo_name, branch, head)
        return ToolResult("", f"Synced {repo_name} to {branch} ({head})")
    except RuntimeError as e:
        logger.error("sync_repo failed repo=%s error=%s", repo_name, e)
        return ToolResult("", str(e), is_error=True)


async def fetch_diff(repo_url: str, before_sha: str, after_sha: str) -> ToolResult:
    import logging
    logger = logging.getLogger(__name__)
    git_dir = _resolve_git_dir(repo_url)
    if not git_dir:
        logger.warning("tool_git_error tool=fetch_diff error=repo_not_found repo_url=%s", repo_url)
        return ToolResult("", f"Cannot resolve repo at: {repo_url}", is_error=True)
    zeros = "0" * 40
    try:
        repo_name = repo_url.split('/')[-1]
        if before_sha == zeros:
            logger.debug("tool_fetch_diff mode=initial_commit sha=%s repo=%s", after_sha[:8], repo_name)
            output = await _git(["show", "--stat", after_sha], git_dir=git_dir)
        else:
            logger.debug(
                "tool_fetch_diff mode=range before=%s after=%s repo=%s",
                before_sha[:8],
                after_sha[:8],
                repo_name,
            )
            output = await _git(["diff", f"{before_sha}..{after_sha}"], git_dir=git_dir)
        diff_size = len(output)
        if diff_size > 30000:
            logger.warning(
                "tool_fetch_diff truncated repo=%s size_bytes=%d→30000",
                repo_name,
                diff_size,
            )
        else:
            logger.debug("tool_fetch_diff success repo=%s size_bytes=%d", repo_name, diff_size)
        return ToolResult("", output[:30000])
    except RuntimeError as e:
        logger.error("tool_fetch_diff failed repo=%s error=%s", repo_url.split('/')[-1], e)
        return ToolResult("", str(e), is_error=True)


async def get_file_content(repo_url: str, file_path: str, ref: str) -> ToolResult:
    import logging
    logger = logging.getLogger(__name__)
    git_dir = _resolve_git_dir(repo_url)
    if not git_dir:
        logger.warning("tool_git_error tool=get_file_content error=repo_not_found repo_url=%s", repo_url)
        return ToolResult("", f"Cannot resolve repo at: {repo_url}", is_error=True)
    try:
        logger.debug("tool_get_file_content ref=%s file=%s repo=%s", ref[:8], file_path, repo_url.split('/')[-1])
        output = await _git(["show", f"{ref}:{file_path}"], git_dir=git_dir)
        logger.debug("tool_get_file_content success size_bytes=%d", len(output))
        return ToolResult("", output[:20000])
    except RuntimeError as e:
        logger.error("tool_get_file_content failed file=%s error=%s", file_path, e)
        return ToolResult("", str(e), is_error=True)


async def list_changed_files(repo_url: str, before_sha: str, after_sha: str) -> ToolResult:
    import logging
    logger = logging.getLogger(__name__)
    git_dir = _resolve_git_dir(repo_url)
    if not git_dir:
        logger.warning("tool_git_error tool=list_changed_files error=repo_not_found repo_url=%s", repo_url)
        return ToolResult("", f"Cannot resolve repo at: {repo_url}", is_error=True)
    zeros = "0" * 40
    try:
        repo_name = repo_url.split('/')[-1]
        if before_sha == zeros:
            logger.debug("tool_list_changed_files mode=initial_commit sha=%s repo=%s", after_sha[:8], repo_name)
            output = await _git(["show", "--name-status", "--format=", after_sha], git_dir=git_dir)
        else:
            logger.debug(
                "tool_list_changed_files mode=range before=%s after=%s repo=%s",
                before_sha[:8],
                after_sha[:8],
                repo_name,
            )
            output = await _git(["diff", "--name-status", f"{before_sha}..{after_sha}"], git_dir=git_dir)
        file_count = len([line for line in output.split('\n') if line.strip()])
        logger.debug("tool_list_changed_files success file_count=%d", file_count)
        return ToolResult("", output)
    except RuntimeError as e:
        logger.error("tool_list_changed_files failed error=%s", e)
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

async def analyze_file_history(repo_url: str, file_path: str, max_commits: int = 10) -> ToolResult:
    git_dir = _resolve_git_dir(repo_url)
    if not git_dir:
        return ToolResult("", f"Cannot resolve repo at: {repo_url}", is_error=True)
    try:
        n = max(1, min(max_commits, 30))
        output = await _git(
            ["log", f"-{n}", "--follow", "-p", "--", file_path],
            git_dir=git_dir,
        )
        return ToolResult("", output[:25000])
    except RuntimeError as e:
        return ToolResult("", str(e), is_error=True)


FILE_HISTORY = ToolDefinition(
    name="analyze_file_history",
    description=(
        "Get the recent git commit history (with diffs) for a specific file. "
        "Useful for understanding why code was written a certain way or spotting regression patterns."
    ),
    parameters=[
        ToolParameter("repo_url", "string", "Local path or URL to the git repository"),
        ToolParameter("file_path", "string", "Path to the file within the repository"),
        ToolParameter("max_commits", "integer", "Maximum number of commits to return (1-30, default 10)", required=False),
    ],
)

EXECUTORS = {
    FETCH_DIFF.name: fetch_diff,
    GET_FILE.name: get_file_content,
    LIST_FILES.name: list_changed_files,
    FILE_HISTORY.name: analyze_file_history,
}
