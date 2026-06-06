"""Jenkins integration for asdlc.

Handles:
- Callback notifications when scans complete
- JUnit XML report generation
- Jenkins API integration for setting build status/badges
"""

import base64
import logging
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)


def format_findings_as_junit(result) -> str:
    """Convert findings to JUnit XML format for Jenkins test reporting."""
    root = ET.Element("testsuites")
    root.set("name", "asdlc")
    root.set("tests", "0")
    root.set("failures", "0")
    root.set("errors", "0")

    total_tests = 0
    total_failures = 0

    for agent_result in result.agent_results:
        testsuite = ET.SubElement(root, "testsuite")
        testsuite.set("name", agent_result.agent_name)
        testsuite.set("package", f"asdlc.{result.repo_name}")
        testsuite.set("timestamp", result.started_at.isoformat())
        testsuite.set("time", str((result.completed_at - result.started_at).total_seconds()))

        findings = agent_result.findings
        testsuite.set("tests", str(len(findings)))

        failures = [f for f in findings if f.severity in ("critical", "high")]
        testsuite.set("failures", str(len(failures)))

        total_tests += len(findings)
        total_failures += len(failures)

        for finding in findings:
            testcase = ET.SubElement(testsuite, "testcase")
            testcase.set("classname", f"{result.repo_name}.{agent_result.agent_name}")
            testcase.set("name", finding.title)
            if finding.file_path:
                testcase.set("file", finding.file_path)
                if finding.line_number:
                    testcase.set("line", str(finding.line_number))

            if finding.severity in ("critical", "high"):
                failure = ET.SubElement(testcase, "failure")
                failure.set("type", finding.severity.upper())
                failure_text = f"{finding.title}\n"
                if finding.description:
                    failure_text += f"\n{finding.description}\n"
                if finding.recommendation:
                    failure_text += f"\nRecommendation: {finding.recommendation}"
                failure.text = failure_text

            system_out = ET.SubElement(testcase, "system-out")
            system_text = f"[{finding.severity.upper()}] {finding.title}"
            if finding.description:
                system_text += f"\n{finding.description}"
            system_out.text = system_text

    root.set("tests", str(total_tests))
    root.set("failures", str(total_failures))

    return ET.tostring(root, encoding="unicode")


def format_findings_as_json(result) -> dict:
    """Convert findings to JSON format for Jenkins reporting."""
    findings = []
    for agent_result in result.agent_results:
        for finding in agent_result.findings:
            findings.append(
                {
                    "agent": agent_result.agent_name,
                    "severity": finding.severity,
                    "title": finding.title,
                    "description": finding.description,
                    "file_path": finding.file_path,
                    "line_number": finding.line_number,
                    "recommendation": finding.recommendation,
                }
            )

    return {
        "repo": result.repo_name,
        "branch": result.branch,
        "workflow": result.workflow_name,
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "duration_seconds": (result.completed_at - result.started_at).total_seconds(),
        "total_findings": len(findings),
        "critical": len([f for f in findings if f["severity"] == "critical"]),
        "high": len([f for f in findings if f["severity"] == "high"]),
        "medium": len([f for f in findings if f["severity"] == "medium"]),
        "low": len([f for f in findings if f["severity"] == "low"]),
        "findings": findings,
    }


async def post_jenkins_callback(
    callback_url: str,
    run_id: str,
    result,
) -> None:
    """POST findings to Jenkins callback URL in multiple formats."""
    junit_xml = format_findings_as_junit(result)
    json_data = format_findings_as_json(result)

    payload = {
        "run_id": run_id,
        "repo": result.repo_name,
        "branch": result.branch,
        "workflow": result.workflow_name,
        "junit_xml": junit_xml,
        "json": json_data,
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(callback_url, json=payload, timeout=30.0)
            logger.info("jenkins callback run_id=%s status=%d", run_id, r.status_code)
    except Exception as e:
        logger.warning("jenkins callback failed run_id=%s: %s", run_id, e)


async def set_jenkins_build_status(
    jenkins_url: str,
    job_name: str,
    build_number: int,
    api_token: str,
    result,
    jenkins_user: str = "asdlc",
) -> None:
    """Set Jenkins build status and description via Jenkins API.

    Args:
        jenkins_url: Base Jenkins URL (e.g., http://jenkins.example.com)
        job_name: Jenkins job name
        build_number: Jenkins build number
        api_token: Jenkins API token for authentication
        result: WorkflowResult with findings
        jenkins_user: Jenkins username for API auth (default: asdlc)
    """
    total_findings = sum(len(ar.findings) for ar in result.agent_results)
    critical = sum(
        len([f for f in ar.findings if f.severity == "critical"]) for ar in result.agent_results
    )
    high = sum(len([f for f in ar.findings if f.severity == "high"]) for ar in result.agent_results)

    # Build description with findings summary
    description = f"""
<h3>asdlc Analysis Results</h3>
<ul>
    <li>Workflow: {result.workflow_name}</li>
    <li>Total Findings: {total_findings}</li>
    <li>Critical: {critical}</li>
    <li>High: {high}</li>
    <li>Duration: {(result.completed_at - result.started_at).total_seconds():.1f}s</li>
</ul>
""".strip()

    jenkins_url = jenkins_url.rstrip("/")
    url = f"{jenkins_url}/job/{job_name}/{build_number}/submitDescription"

    # Jenkins API auth: base64(user:token)
    credentials = base64.b64encode(f"{jenkins_user}:{api_token}".encode()).decode()

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                url,
                data={"description": description},
                headers={"Authorization": f"Basic {credentials}"},
                timeout=10.0,
            )
            logger.info(
                "jenkins set_build_status job=%s build=%d status=%d",
                job_name,
                build_number,
                r.status_code,
            )
    except Exception as e:
        logger.warning("jenkins set_build_status failed: %s", e)
