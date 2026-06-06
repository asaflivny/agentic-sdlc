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
) -> None:
    """Find the open PR for *branch* in *repo* and post findings as a comment.

    Silently skips if no matching PR is found or if there are no findings.
    *repo* must be in ``owner/name`` form.
    """
    total = sum(len(r.findings) for r in result.agent_results)
    if total == 0:
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
                "github: no open PR found for branch=%s repo=%s, skipping comment", branch, repo
            )
            return

        body = _build_comment(result, total)
        url = f"{_GH_API}/repos/{repo}/issues/{pr_number}/comments"
        r = await client.post(url, json={"body": body})
        if r.status_code in (200, 201):
            logger.info(
                "github: posted PR comment pr=%d repo=%s findings=%d", pr_number, repo, total
            )
        else:
            logger.warning(
                "github: comment post failed pr=%d status=%d body=%s",
                pr_number,
                r.status_code,
                r.text[:200],
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
    lines.append(f"<sub>run_id: `{result.repo_name}`</sub>")
    return "\n".join(lines)
