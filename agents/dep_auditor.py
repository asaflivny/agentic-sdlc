import logging
import re
import time

import httpx

from agents.base import BaseAgent
from models.results import AgentContext, AgentResult, Finding, Severity

logger = logging.getLogger(__name__)

OSV_API = "https://api.osv.dev/v1/querybatch"

_DEP_FILE_RE = re.compile(
    r"requirements.*\.txt|pyproject\.toml|setup\.py|setup\.cfg|"
    r"Pipfile|package\.json|package-lock\.json|yarn\.lock|"
    r"Gemfile|go\.mod|Cargo\.toml",
    re.IGNORECASE,
)


class DepAuditAgent(BaseAgent):
    name = "dep_auditor"
    display_name = "Dependency Auditor"
    description = "Checks new or changed dependencies for known CVEs via OSV.dev"

    def get_system_prompt(self) -> str:
        return ""  # Not used — this agent overrides run() directly

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()
        event = context.push_event

        all_changed = [f for commit in event.commits for f in commit.added + commit.modified]
        dep_files = [f for f in all_changed if _DEP_FILE_RE.search(f)]

        if not dep_files:
            return AgentResult(
                agent_name=self.name,
                status="success",
                summary="No dependency file changes detected.",
                duration_seconds=round(time.monotonic() - start, 2),
            )

        packages = _extract_added_packages(context.git_diff)
        logger.info("dep_auditor dep_files=%s packages=%d", dep_files, len(packages))

        if not packages:
            return AgentResult(
                agent_name=self.name,
                status="success",
                summary=(
                    f"Dependency files changed ({', '.join(dep_files)}) "
                    "but no new packages detected in diff."
                ),
                duration_seconds=round(time.monotonic() - start, 2),
            )

        findings = await _check_osv(packages)
        summary = (
            f"Checked {len(packages)} added/changed package(s) across "
            f"{len(dep_files)} dep file(s). Found {len(findings)} vulnerability finding(s)."
        )
        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            summary=summary,
            duration_seconds=round(time.monotonic() - start, 2),
        )


def _extract_added_packages(diff: str) -> list[dict]:
    packages: list[dict] = []
    seen: set[str] = set()

    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:].strip()
        if not content or content.startswith("#"):
            continue

        # requirements.txt style: package==version, package>=version, etc.
        m = re.match(r"^([A-Za-z0-9_\-\.]+)\s*[><=!~^]+\s*([A-Za-z0-9_\-\.\*]+)", content)
        if m:
            name = m.group(1)
            version = m.group(2).lstrip("=")
            if name not in seen:
                seen.add(name)
                packages.append({"name": name, "version": version, "ecosystem": "PyPI"})
            continue

        # bare package name on its own line
        m = re.match(r"^([A-Za-z][A-Za-z0-9_\-\.]{1,49})$", content)
        if m:
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                packages.append({"name": name, "ecosystem": "PyPI"})

    return packages[:30]


async def _check_osv(packages: list[dict]) -> list[Finding]:
    queries = []
    for pkg in packages:
        q: dict = {"package": {"name": pkg["name"], "ecosystem": pkg.get("ecosystem", "PyPI")}}
        if pkg.get("version"):
            q["version"] = pkg["version"]
        queries.append(q)

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(OSV_API, json={"queries": queries}, timeout=15.0)
            r.raise_for_status()
    except Exception as e:
        logger.warning("OSV API error: %s", e)
        return []

    results = r.json().get("results", [])
    findings: list[Finding] = []
    for i, result in enumerate(results):
        vulns = result.get("vulns", [])
        if not vulns:
            continue
        pkg = packages[i]
        count = len(vulns)
        vuln_ids = ", ".join(v["id"] for v in vulns[:5])
        summaries = "; ".join(v.get("summary", "") for v in vulns[:3] if v.get("summary"))
        desc = (
            f"Package {pkg['name']} ({pkg.get('version', 'unversioned')}) "
            f"has {count} known vulnerability(-ies)."
        )
        if summaries:
            desc += f" Issues: {summaries}"
        findings.append(
            Finding(
                title=f"{pkg['name']}: {count} known CVE(s)",
                description=desc,
                severity=Severity.HIGH if count >= 2 else Severity.MEDIUM,
                file_path=None,
                recommendation=(
                    f"Upgrade {pkg['name']} to a patched version. "
                    f"CVE IDs: {vuln_ids}. See https://osv.dev/"
                ),
            )
        )

    return findings
