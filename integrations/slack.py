"""Slack webhook notifications for workflow findings."""

import logging

import httpx

from config import Settings
from models.results import WorkflowResult

logger = logging.getLogger(__name__)


async def post_slack_notification(
    workflow_result: WorkflowResult,
    settings: Settings,
) -> bool:
    """Post a Slack message summarizing workflow findings. Returns True if posted successfully."""
    if not settings.slack_webhook_url:
        return False

    total_findings = sum(len(r.findings) for r in workflow_result.agent_results)
    if total_findings == 0:
        return False  # Skip notification if no findings

    # Build findings summary by severity
    findings_by_severity = {}
    for agent_result in workflow_result.agent_results:
        for finding in agent_result.findings:
            severity = finding.severity.value.upper()
            if severity not in findings_by_severity:
                findings_by_severity[severity] = []
            findings_by_severity[severity].append(
                f"• *{finding.title}* (by {agent_result.agent_name})"
            )

    # Build message blocks
    findings_text = ""
    severity_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    for severity in severity_order:
        if severity in findings_by_severity:
            emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵", "INFO": "⚪"}.get(
                severity, ""
            )
            findings_text += f"\n{emoji} *{severity}* ({len(findings_by_severity[severity])})\n"
            findings_text += "\n".join(findings_by_severity[severity][:3])
            if len(findings_by_severity[severity]) > 3:
                findings_text += f"\n  ... and {len(findings_by_severity[severity]) - 3} more\n"

    message = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📊 Analysis Complete: {workflow_result.repo_name} ({workflow_result.branch})",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Workflow:* {workflow_result.workflow_name}\n*Total Findings:* {total_findings}\n*Duration:* {(workflow_result.completed_at - workflow_result.started_at).total_seconds():.1f}s",
                },
            },
        ]
    }

    if findings_text:
        message["blocks"].append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": findings_text,
                },
            }
        )

    # Add mentions if configured
    if settings.slack_mention_channels:
        mentions = " ".join(f"<{m.strip()}>" for m in settings.slack_mention_channels.split(","))
        message["text"] = mentions

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                settings.slack_webhook_url,
                json=message,
                timeout=5.0,
            )
            if r.status_code == 200:
                logger.info(
                    "posted slack notification repo=%s findings=%d",
                    workflow_result.repo_name,
                    total_findings,
                )
                return True
            else:
                logger.warning("slack post failed status=%d", r.status_code)
                return False
    except Exception as e:
        logger.warning("slack notification error: %s", e)
        return False
