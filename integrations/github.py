"""Post workflow findings as a GitHub PR comment."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from models.results import WorkflowResult

logger = logging.getLogger(__name__)

_SEVERITY_BADGE = {
    "critical": "🔴 **critical**",
    "high": "🟠 **high**",
    "medium": "🟡 **medium**",
    "low": "🟢 **low**",
    "info": "⚪ **info**",
}
_GH_API = "https://api.github.com"


async def post_pr_findings(
    token: str,
    repo: str,
    branch: str,
    result: "WorkflowResult",
    post_inline_comments: bool = True,
) -> None:
    """Find the open PR for *branch* in *repo* and post findings as review comments.

    Posts a summary comment plus optional inline comments on specific lines with findings.
    Silently skips if no matching PR is found or if there are no findings.
    *repo* must be in ``owner/name`` form.
    *post_inline_comments* controls whether to post inline review comments (default True).
    """
    total = sum(len(r.findings) for r in result.agent_results)
    if total == 0:
        logger.debug("github_post_findings skipped repo=%s branch=%s reason=no_findings", repo, branch)
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
        pr_number = await _find_pr(client, repo, branch)
        if pr_number is None:
            logger.info(
                "github_pr_not_found repo=%s branch=%s skipping_comments",
                repo,
                branch,
            )
            return

        # Post summary comment
        body = _build_comment(result, total)
        url = f"{_GH_API}/repos/{repo}/issues/{pr_number}/comments"
        r = await client.post(url, json={"body": body})
        if r.status_code in (200, 201):
            logger.info(
                "github_summary_comment_posted pr=%d repo=%s total_findings=%d",
                pr_number,
                repo,
                total,
            )
        else:
            logger.warning(
                "github_summary_comment_failed pr=%d repo=%s status=%d",
                pr_number,
                repo,
                r.status_code,
            )

        # Post inline review comments for findings with file paths and line numbers
        if post_inline_comments:
            inline_count = await _post_inline_reviews(client, repo, pr_number, result)
            if inline_count > 0:
                logger.info(
                    "github_inline_comments_posted pr=%d repo=%s count=%d",
                    pr_number,
                    repo,
                    inline_count,
                )


async def _find_pr(client: httpx.AsyncClient, repo: str, branch: str) -> int | None:
    url = f"{_GH_API}/repos/{repo}/pulls"
    r = await client.get(url, params={"state": "open", "head": branch, "per_page": 5})
    if r.status_code != 200:
        logger.warning("github: PR lookup failed status=%d", r.status_code)
        return None
    prs = r.json()
    # GitHub's head filter format is "owner:branch"; try both
    for pr in prs:
        if pr.get("head", {}).get("ref") == branch:
            return pr["number"]
    return None


async def _post_inline_reviews(
    client: httpx.AsyncClient, repo: str, pr_number: int, result: "WorkflowResult"
) -> int:
    """Post inline review comments for findings with file paths and line numbers.

    Returns the count of inline comments posted. Skips findings without file paths or line numbers.
    """
    count = 0
    comments_list = []

    for agent_result in result.agent_results:
        for finding in agent_result.findings:
            # Only add inline comments if we have both file path and line number
            if not finding.file_path or not finding.line_number:
                continue

            badge = _SEVERITY_BADGE.get(finding.severity, finding.severity)
            comment_body = f"{badge} — **{finding.title}**\n\n{finding.description}"
            if finding.recommendation:
                comment_body += f"\n\n*Recommendation:* {finding.recommendation}"
            comment_body += f"\n\n_by {agent_result.agent_name}_"

            comments_list.append(
                {
                    "path": finding.file_path,
                    "line": finding.line_number,
                    "body": comment_body,
                }
            )

    if not comments_list:
        return 0

    # Post all comments as a single review
    url = f"{_GH_API}/repos/{repo}/pulls/{pr_number}/reviews"
    review_body = {
        "commit_id": result.commit_sha,
        "body": "asdlc code analysis findings (inline comments below)",
        "comments": comments_list,
        "event": "COMMENT",
    }

    try:
        r = await client.post(url, json=review_body)
        if r.status_code in (200, 201):
            count = len(comments_list)
            logger.info("github_inline_review_posted pr=%d repo=%s comments=%d", pr_number, repo, count)
        else:
            logger.warning(
                "github_inline_review_failed pr=%d repo=%s status=%d",
                pr_number,
                repo,
                r.status_code,
            )
    except Exception as e:
        logger.warning("github_inline_review_error pr=%d repo=%s error=%s", pr_number, repo, e)

    return count


def _build_comment(result: "WorkflowResult", total: int) -> str:
    lines = [
        f"## ⚡ asdlc — {result.workflow_name}",
        f"**{total} finding(s)** on `{result.branch}` · workflow `{result.workflow_name}`",
        "",
    ]
    for agent_result in result.agent_results:
        if not agent_result.findings:
            continue
        lines.append(
            f"<details><summary><strong>{agent_result.agent_name}</strong> — {len(agent_result.findings)} finding(s)</summary>"
        )
        lines.append("")
        for f in agent_result.findings:
            badge = _SEVERITY_BADGE.get(f.severity, f.severity)
            loc = (
                f" · `{f.file_path}`" + (f":{f.line_number}" if f.line_number else "")
                if f.file_path
                else ""
            )
            lines.append(f"**{badge} — {f.title}**{loc}")
            lines.append(f"> {f.description}")
            if f.recommendation:
                lines.append(f"*Recommendation:* {f.recommendation}")
            lines.append("")
        lines.append("</details>")
        lines.append("")
    if result.run_id:
        lines.append(f"<sub>run_id: `{result.run_id}`</sub>")
    return "\n".join(lines)
