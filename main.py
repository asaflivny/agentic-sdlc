import asyncio
import collections
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import APIKeyHeader
from fastapi.templating import Jinja2Templates

from config import Settings, get_settings
from models.events import PushEvent, Repository, Pusher
from security import verify_webhook_signature
from store import WorkflowStore
from workflows.orchestrator import WorkflowOrchestrator
from workflows.router import WorkflowRouter
from workflows.definitions.full_review import FULL_REVIEW

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

router = WorkflowRouter()
orchestrator: WorkflowOrchestrator | None = None
store: WorkflowStore | None = None
_run_semaphore: asyncio.Semaphore | None = None
templates = Jinja2Templates(directory="templates")

# In-memory Prometheus-style counters
_metrics: dict[str, float] = {
    "runs_total": 0,
    "findings_total": 0,
    "agent_timeout_total": 0,
    "agent_error_total": 0,
    "agent_duration_seconds_sum": 0,
    "agent_duration_seconds_count": 0,
}

# Per-repo rate limiting: repo_name -> deque of timestamps
_rate_windows: dict[str, collections.deque] = collections.defaultdict(
    lambda: collections.deque(maxlen=1000)
)

# API key header (optional)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, store, _run_semaphore
    settings = get_settings()
    logging.getLogger().setLevel(settings.log_level.upper())
    orchestrator = WorkflowOrchestrator(settings)
    store = WorkflowStore(settings.db_path)
    await store.setup()
    _run_semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
    logger.info(
        "asdlc ready model=%s concurrency=%d db=%s",
        settings.ollama_model,
        settings.max_concurrent_runs,
        settings.db_path,
    )
    yield


app = FastAPI(title="Agentic SDLC", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _require_api_key(api_key: str = Depends(_api_key_header)) -> None:
    settings = get_settings()
    if settings.api_key and api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Rate limiting helper
# ---------------------------------------------------------------------------

def _check_rate_limit(repo_name: str) -> None:
    settings = get_settings()
    limit = settings.rate_limit_per_repo
    if limit <= 0:
        return
    now = time.monotonic()
    window = _rate_windows[repo_name]
    # Drop timestamps older than 60s
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {limit} pushes/minute for repo '{repo_name}'",
        )
    window.append(now)


# ---------------------------------------------------------------------------
# Webhook ingestion
# ---------------------------------------------------------------------------

@app.post("/git/push", status_code=202, dependencies=[Depends(verify_webhook_signature)])
async def git_push(event: PushEvent, background_tasks: BackgroundTasks):
    _check_rate_limit(event.repository.name)
    logger.info(
        "push parsed OK repo=%s branch=%s pusher=%s commits=%d head=%s",
        event.repository.name,
        event.branch,
        event.pusher.name,
        len(event.commits),
        event.after[:7],
    )
    for commit in event.commits:
        logger.info(
            "  commit %s by %s: %s (modified: %s)",
            commit.id[:7],
            commit.author.name,
            commit.message,
            ", ".join(commit.modified) or "none",
        )
    workflow = router.route(event)
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_workflow, run_id, workflow, event)
    return {
        "status": "accepted",
        "run_id": run_id,
        "workflow": workflow.name,
        "repo": event.repository.name,
        "branch": event.branch,
        "commits": len(event.commits),
    }


async def _run_workflow(run_id: str, workflow, event: PushEvent):
    async with _run_semaphore:
        try:
            result = await orchestrator.run(workflow, event, run_id=run_id)
            total_findings = sum(len(r.findings) for r in result.agent_results)
            _metrics["runs_total"] += 1
            _metrics["findings_total"] += total_findings
            for r in result.agent_results:
                if r.status == "timeout":
                    _metrics["agent_timeout_total"] += 1
                elif r.status == "error":
                    _metrics["agent_error_total"] += 1
                if r.duration_seconds:
                    _metrics["agent_duration_seconds_sum"] += r.duration_seconds
                    _metrics["agent_duration_seconds_count"] += 1

            await store.save(run_id, result)
            logger.info(
                "completed run_id=%s workflow=%s repo=%s findings=%d",
                run_id,
                result.workflow_name,
                result.repo_name,
                total_findings,
            )
            logger.info("\n%s", result.overall_summary)

            settings = get_settings()
            if settings.result_webhook_url:
                await _notify_result(settings.result_webhook_url, run_id, result)
            if settings.slack_webhook_url:
                await _notify_slack(settings.slack_webhook_url, run_id, result)
        except Exception:
            logger.exception("workflow failed run_id=%s repo=%s", run_id, event.repository.name)


async def _notify_result(url: str, run_id: str, result) -> None:
    payload = {"run_id": run_id, **result.model_dump(mode="json")}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=10.0)
            logger.info("result webhook run_id=%s status=%d", run_id, r.status_code)
    except Exception as e:
        logger.warning("result webhook failed run_id=%s: %s", run_id, e)


async def _notify_slack(url: str, run_id: str, result) -> None:
    all_findings = [
        f for r in result.agent_results for f in r.findings
        if f.severity in ("critical", "high")
    ]
    if not all_findings:
        return

    severity_icon = {"critical": ":red_circle:", "high": ":orange_circle:"}
    lines = [
        f"*asdlc* — `{result.repo_name}` / `{result.branch}` — "
        f"{sum(len(r.findings) for r in result.agent_results)} finding(s) "
        f"({len(all_findings)} critical/high)",
    ]
    for f in all_findings[:10]:
        icon = severity_icon.get(f.severity, ":white_circle:")
        loc = f" (`{f.file_path}`)" if f.file_path else ""
        lines.append(f"  {icon} *{f.title}*{loc}")
    if len(all_findings) > 10:
        lines.append(f"  _...and {len(all_findings) - 10} more_")
    lines.append(f"run_id: `{run_id}`")

    payload = {"text": "\n".join(lines)}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=10.0)
            logger.info("slack notify run_id=%s status=%d", run_id, r.status_code)
    except Exception as e:
        logger.warning("slack notify failed run_id=%s: %s", run_id, e)


# ---------------------------------------------------------------------------
# Manual scan
# ---------------------------------------------------------------------------

class ScanRequest(PushEvent.__bases__[0]):  # type: ignore[name-defined]
    repo_path: str
    branch: str = "main"
    before_sha: str = "0" * 40
    after_sha: str = "HEAD"


from pydantic import BaseModel as _BM

class ScanRequest(_BM):
    repo_path: str
    branch: str = "main"
    before_sha: str = "0" * 40
    after_sha: str = "HEAD"


@app.post("/scan", status_code=202, dependencies=[Depends(_require_api_key)])
async def scan(req: ScanRequest, background_tasks: BackgroundTasks):
    """Trigger a full review on a local repo without a git push event."""
    zeros = "0" * 40
    after = req.after_sha
    # Resolve HEAD to a real SHA
    if after == "HEAD":
        import asyncio as _asyncio
        from tools.git_tools import _resolve_git_dir, _git
        git_dir = _resolve_git_dir(req.repo_path)
        if not git_dir:
            raise HTTPException(status_code=400, detail=f"Cannot resolve repo at: {req.repo_path}")
        try:
            after = (await _git(["rev-parse", "HEAD"], git_dir=git_dir)).strip()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"git rev-parse failed: {e}")

    event = PushEvent(
        ref=f"refs/heads/{req.branch}",
        before=req.before_sha,
        after=after,
        repository=Repository(name=req.repo_path.rstrip("/").rsplit("/", 1)[-1], clone_url=req.repo_path),
        pusher=Pusher(name="scan", email="scan@asdlc"),
        commits=[],
    )
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_workflow, run_id, FULL_REVIEW, event)
    return {"status": "accepted", "run_id": run_id, "workflow": "full_review", "repo": event.repository.name}


# ---------------------------------------------------------------------------
# Results API
# ---------------------------------------------------------------------------

@app.get("/results", dependencies=[Depends(_require_api_key)])
async def list_results(repo: Optional[str] = None, branch: Optional[str] = None, limit: int = 20):
    return await store.list_runs(repo=repo, branch=branch, limit=limit)


@app.get("/results/{run_id}", dependencies=[Depends(_require_api_key)])
async def get_result(run_id: str):
    data = await store.get_run(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="run not found")
    return data


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    runs = await store.list_runs(limit=50)
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"runs": runs})


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    settings = get_settings()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                settings.ollama_base_url.rstrip("/v1").rstrip("/") + "/api/tags",
                timeout=3.0,
            )
            ok = r.status_code == 200
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=503, detail="Ollama not reachable")
    return {"status": "ready", "ollama": settings.ollama_base_url}


@app.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(_require_api_key)])
async def metrics():
    lines = [
        "# HELP asdlc_runs_total Total workflow runs completed",
        "# TYPE asdlc_runs_total counter",
        f"asdlc_runs_total {_metrics['runs_total']:.0f}",
        "# HELP asdlc_findings_total Total findings emitted across all runs",
        "# TYPE asdlc_findings_total counter",
        f"asdlc_findings_total {_metrics['findings_total']:.0f}",
        "# HELP asdlc_agent_timeout_total Agents that timed out",
        "# TYPE asdlc_agent_timeout_total counter",
        f"asdlc_agent_timeout_total {_metrics['agent_timeout_total']:.0f}",
        "# HELP asdlc_agent_error_total Agents that errored",
        "# TYPE asdlc_agent_error_total counter",
        f"asdlc_agent_error_total {_metrics['agent_error_total']:.0f}",
        "# HELP asdlc_agent_duration_seconds_sum Sum of agent durations",
        "# TYPE asdlc_agent_duration_seconds_sum counter",
        f"asdlc_agent_duration_seconds_sum {_metrics['agent_duration_seconds_sum']:.3f}",
        "# HELP asdlc_agent_duration_seconds_count Number of agent runs timed",
        "# TYPE asdlc_agent_duration_seconds_count counter",
        f"asdlc_agent_duration_seconds_count {_metrics['agent_duration_seconds_count']:.0f}",
    ]
    return "\n".join(lines) + "\n"
