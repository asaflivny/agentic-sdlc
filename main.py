import asyncio
import collections
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Optional

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import APIKeyHeader
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config import get_settings
from models.events import PushEvent, Repository, Pusher
from models.results import TraceContext
from rag import RAGStore
from security import verify_webhook_signature
from store import WorkflowStore
from workflows.orchestrator import WorkflowOrchestrator
from workflows.router import WorkflowRouter
from workflows.definitions.full_review import FULL_REVIEW


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        # Absorb any extra kwargs passed to the log call (e.g. run_id=, repo=)
        skip = logging.LogRecord.__dict__.keys() | {
            "message",
            "asctime",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
            "stack_info",
        }
        for k, v in record.__dict__.items():
            if k not in skip and not k.startswith("_"):
                obj[k] = v
        return json.dumps(obj, default=str)


def _configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


logger = logging.getLogger(__name__)

router = WorkflowRouter()
orchestrator: WorkflowOrchestrator | None = None
store: WorkflowStore | None = None
rag_store: RAGStore | None = None
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
    global orchestrator, store, rag_store, _run_semaphore
    settings = get_settings()
    _configure_logging(settings.log_level)

    # Clone repos on startup
    from tools.git_tools import clone_or_verify_repos

    cloned_repos = await clone_or_verify_repos(settings.repos_root, settings.git_clone_sources)
    if cloned_repos:
        logger.info(
            "repos initialized count=%d: %s", len(cloned_repos), ", ".join(cloned_repos.keys())
        )

    orchestrator = WorkflowOrchestrator(settings)
    store = WorkflowStore(settings.db_path)
    await store.setup()

    # Initialize RAG store if enabled
    if settings.rag_enabled:
        try:
            rag_store = RAGStore(settings.rag_db_path, settings.rag_embedding_model)
            await rag_store.setup()
            orchestrator.set_rag_store(rag_store)
            logger.info("RAG store initialized at %s", settings.rag_db_path)
        except Exception as e:
            logger.error("Failed to initialize RAG store, disabling RAG: %s", e)
            rag_store = None

    _run_semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
    logger.info(
        "asdlc ready model=%s concurrency=%d db=%s repos_root=%s rag=%s",
        settings.ollama_model,
        settings.max_concurrent_runs,
        settings.db_path,
        settings.repos_root,
        "enabled" if rag_store else "disabled",
    )
    yield
    # Cleanup
    await store.cleanup()
    if rag_store:
        await rag_store.cleanup()
    logger.info("asdlc shutdown complete")


app = FastAPI(title="Agentic SDLC", lifespan=lifespan)


@app.middleware("http")
async def cache_request_body(request: Request, call_next):
    """Cache request body in request.state so it can be read multiple times.

    This enables webhook signature verification to read the body without
    preventing the route handler from parsing it as JSON.
    """
    body = await request.body()
    request.state.body = body

    async def receive():
        """Return the cached body for re-reading."""
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive
    response = await call_next(request)
    return response


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
        logger.warning("rate_limit_exceeded repo=%s limit=%d window_size=%d", repo_name, limit, len(window))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {limit} pushes/minute for repo '{repo_name}'",
        )
    window.append(now)


# ---------------------------------------------------------------------------
# Webhook ingestion
# ---------------------------------------------------------------------------


@app.post("/git/push", status_code=202, dependencies=[Depends(verify_webhook_signature)])
async def git_push(event: PushEvent, background_tasks: BackgroundTasks, request: Request):
    _check_rate_limit(event.repository.name)
    run_id = str(uuid.uuid4())
    trace = TraceContext.from_workflow(run_id)

    logger.info(
        "push_received run_id=%s workflow_trace=%s repo=%s branch=%s pusher=%s commits=%d head=%s",
        run_id,
        trace.workflow_trace_id,
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

    # Extract Jenkins params from headers
    jenkins_callback_url = request.headers.get("X-Jenkins-Callback-URL", "")
    jenkins_job_name = request.headers.get("X-Jenkins-Job-Name", "")
    jenkins_build_number = request.headers.get("X-Jenkins-Build-Number", "0")
    jenkins_api_token = request.headers.get("X-Jenkins-API-Token", "")

    background_tasks.add_task(
        _run_workflow,
        run_id,
        trace,
        workflow,
        event,
        jenkins_callback_url=jenkins_callback_url,
        jenkins_job_name=jenkins_job_name,
        jenkins_build_number=int(jenkins_build_number) if jenkins_build_number.isdigit() else 0,
        jenkins_api_token=jenkins_api_token,
    )
    return {
        "status": "accepted",
        "run_id": run_id,
        "workflow": workflow.name,
        "repo": event.repository.name,
        "branch": event.branch,
        "commits": len(event.commits),
    }


async def _run_workflow(
    run_id: str,
    trace: TraceContext,
    workflow,
    event: PushEvent,
    jenkins_callback_url: str = "",
    jenkins_job_name: str = "",
    jenkins_build_number: int = 0,
    jenkins_api_token: str = "",
):
    async with _run_semaphore:
        try:
            # Sync repository before analysis
            from tools.git_tools import sync_repo

            settings = get_settings()

            # Determine repo path: either from event.repository.clone_url or repos_root/{repo_name}
            repo_name = event.repository.name
            if event.repository.clone_url and Path(event.repository.clone_url).exists():
                repo_path = event.repository.clone_url
            else:
                repo_path = str(Path(settings.repos_root) / repo_name)

            if Path(repo_path).exists():
                sync_result = await sync_repo(repo_path, event.branch)
                if sync_result.is_error:
                    logger.warning(
                        "repo_sync failed run_id=%s repo=%s error=%s",
                        run_id,
                        repo_name,
                        sync_result.content,
                    )
                else:
                    logger.info(
                        "repo_sync complete run_id=%s repo=%s branch=%s",
                        run_id,
                        repo_name,
                        event.branch,
                    )
            else:
                logger.error(
                    "repo_sync skipped run_id=%s repo=%s reason=not_found path=%s",
                    run_id,
                    repo_name,
                    repo_path,
                )

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

            if settings.result_webhook_url:
                await _notify_result(settings.result_webhook_url, run_id, result)
            if settings.slack_webhook_url:
                await _notify_slack(settings.slack_webhook_url, run_id, result)
            if settings.email_webhook_url and settings.email_recipients:
                await _notify_email(settings.email_webhook_url, settings.email_recipients, result)
            if jenkins_callback_url:
                from integrations.jenkins import post_jenkins_callback, set_jenkins_build_status

                await post_jenkins_callback(jenkins_callback_url, run_id, result)
                if jenkins_job_name and jenkins_build_number and jenkins_api_token:
                    jenkins_url = jenkins_callback_url.rsplit("/", 2)[0]  # Extract base URL
                    await set_jenkins_build_status(
                        jenkins_url,
                        jenkins_job_name,
                        jenkins_build_number,
                        jenkins_api_token,
                        result,
                    )
            if settings.github_token:
                from integrations.github import post_pr_findings

                if settings.github_repo:
                    gh_repo = settings.github_repo
                elif event.repository.owner:
                    gh_repo = f"{event.repository.owner}/{event.repository.name}"
                else:
                    gh_repo = ""
                if gh_repo:
                    await post_pr_findings(
                        settings.github_token,
                        gh_repo,
                        event.branch,
                        result,
                        post_inline_comments=settings.github_inline_comments,
                    )
                else:
                    logger.warning(
                        "github: cannot determine repo owner; set GITHUB_REPO=owner/repo"
                    )
        except Exception:
            logger.exception("workflow failed run_id=%s repo=%s", run_id, event.repository.name)


async def _notify_result(url: str, run_id: str, result) -> None:
    payload = {"run_id": run_id, **result.model_dump(mode="json")}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=10.0)
            logger.info(
                "result_webhook_sent run_id=%s status=%d response_bytes=%d",
                run_id,
                r.status_code,
                len(r.content) if r.status_code < 400 else 0,
            )
    except Exception as e:
        logger.warning("result_webhook_failed run_id=%s error=%s", run_id, e)


async def _notify_slack(url: str, run_id: str, result) -> None:
    all_findings = [
        f for r in result.agent_results for f in r.findings if f.severity in ("critical", "high")
    ]
    if not all_findings:
        logger.debug("slack_notify skipped run_id=%s reason=no_critical_high_findings", run_id)
        return

    total_findings = sum(len(r.findings) for r in result.agent_results)
    severity_icon = {"critical": ":red_circle:", "high": ":orange_circle:"}
    lines = [
        f"*asdlc* — `{result.repo_name}` / `{result.branch}` — "
        f"{total_findings} finding(s) ({len(all_findings)} critical/high)",
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
            logger.info(
                "slack_webhook_sent run_id=%s status=%d critical_high=%d",
                run_id,
                r.status_code,
                len(all_findings),
            )
    except Exception as e:
        logger.warning("slack_webhook_failed run_id=%s error=%s", run_id, e)


async def _notify_email(url: str, recipients_str: str, result) -> None:
    """Send email notification with findings summary."""
    recipients = [r.strip() for r in recipients_str.split(",")]
    total_findings = sum(len(r.findings) for r in result.agent_results)
    if total_findings == 0:
        logger.debug("email_notify skipped reason=no_findings recipients=%d", len(recipients))
        return

    # Build HTML body
    html_body = f"""<html><body style="font-family: sans-serif; line-height: 1.6;">
    <h2>📊 Analysis Complete: {html_escape(result.repo_name)} ({html_escape(result.branch)})</h2>
    <p><strong>Workflow:</strong> {html_escape(result.workflow_name)}<br/>
    <strong>Total Findings:</strong> {total_findings}<br/>
    <strong>Duration:</strong> {(result.completed_at - result.started_at).total_seconds():.1f}s</p>
    <h3>Findings by Agent</h3><ul>
    """

    for agent_result in result.agent_results:
        if agent_result.findings:
            html_body += f"<li><strong>{html_escape(agent_result.agent_name)}</strong>: {len(agent_result.findings)} finding(s)<ul>"
            for finding in agent_result.findings[:5]:
                html_body += f"<li>[{finding.severity.upper()}] <strong>{html_escape(finding.title)}</strong>"
                if finding.description:
                    html_body += f"<br/>{html_escape(finding.description[:100])}"
                html_body += "</li>"
            if len(agent_result.findings) > 5:
                html_body += f"<li>... and {len(agent_result.findings) - 5} more</li>"
            html_body += "</ul></li>"

    html_body += "</ul></body></html>"

    payload = {
        "to": recipients,
        "subject": f"[asdlc] {result.repo_name} analysis: {total_findings} finding(s)",
        "html": html_body,
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=10.0)
            logger.info(
                "email_webhook_sent recipients=%d status=%d total_findings=%d",
                len(recipients),
                r.status_code,
                total_findings,
            )
    except Exception as e:
        logger.warning("email_webhook_failed recipients=%d error=%s", len(recipients), e)


# ---------------------------------------------------------------------------
# Manual scan
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    repo_path: str
    branch: str = "main"
    before_sha: str = "0" * 40
    after_sha: str = "HEAD"
    jenkins_callback_url: str = ""
    jenkins_job_name: str = ""
    jenkins_build_number: int = 0
    jenkins_api_token: str = ""


@app.post("/scan", status_code=202, dependencies=[Depends(_require_api_key)])
async def scan(req: ScanRequest, background_tasks: BackgroundTasks):
    """Trigger a full review on a local repo without a git push event."""
    from pathlib import Path

    repo_path = Path(req.repo_path).resolve()
    if not repo_path.exists():
        raise HTTPException(
            status_code=400, detail=f"Repository path does not exist: {req.repo_path}"
        )

    after = req.after_sha
    if after == "HEAD":
        from tools.git_tools import _resolve_git_dir, _git

        git_dir = _resolve_git_dir(str(repo_path))
        if not git_dir:
            raise HTTPException(status_code=400, detail=f"Cannot resolve git repo at: {repo_path}")
        try:
            after = (await _git(["rev-parse", "HEAD"], cwd=str(repo_path))).strip()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"git rev-parse failed: {e}")

    repo_name = repo_path.name
    event = PushEvent(
        ref=f"refs/heads/{req.branch}",
        before=req.before_sha,
        after=after,
        repository=Repository(name=repo_name, clone_url=str(repo_path)),
        pusher=Pusher(name="scan", email="scan@asdlc"),
        commits=[],
    )
    run_id = str(uuid.uuid4())
    background_tasks.add_task(
        _run_workflow,
        run_id,
        FULL_REVIEW,
        event,
        jenkins_callback_url=req.jenkins_callback_url,
        jenkins_job_name=req.jenkins_job_name,
        jenkins_build_number=req.jenkins_build_number,
        jenkins_api_token=req.jenkins_api_token,
    )
    return {
        "status": "accepted",
        "run_id": run_id,
        "workflow": "full_review",
        "repo": event.repository.name,
    }


# ---------------------------------------------------------------------------
# RAG Ingestion
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    collection: (
        str  # business_knowledge, best_practices, known_issues, findings_shared, code_patterns
    )
    repo: str = "global"
    documents: list[dict]  # list of {content: str, metadata: dict (optional)}


@app.post("/ingest", status_code=202, dependencies=[Depends(_require_api_key)])
async def ingest(req: IngestRequest):
    """Index documents into the RAG knowledge base."""
    settings = get_settings()
    if not settings.rag_enabled or not rag_store:
        raise HTTPException(status_code=503, detail="RAG is not enabled")

    if req.collection not in RAGStore.COLLECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown collection '{req.collection}'. Valid: {', '.join(RAGStore.COLLECTIONS.keys())}",
        )

    if not req.documents:
        raise HTTPException(status_code=400, detail="No documents provided")

    try:
        # Add repo metadata to all documents
        for doc in req.documents:
            if "metadata" not in doc:
                doc["metadata"] = {}
            if "repo" not in doc["metadata"]:
                doc["metadata"]["repo"] = req.repo

        await rag_store.index_documents(req.collection, req.documents)
        logger.info(
            "ingest_completed collection=%s repo=%s count=%d",
            req.collection,
            req.repo,
            len(req.documents),
        )

        return {
            "status": "success",
            "indexed": len(req.documents),
            "collection": req.collection,
            "repo": req.repo,
        }
    except Exception as e:
        logger.error("ingest_failed collection=%s: %s", req.collection, e)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


# ---------------------------------------------------------------------------
# Results API
# ---------------------------------------------------------------------------


@app.get("/results", dependencies=[Depends(_require_api_key)])
async def list_results(
    repo: Optional[str] = None,
    branch: Optional[str] = None,
    severity: Optional[str] = None,
    agent: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
):
    """List workflow results with optional filtering by repo, branch, severity, agent, and search text."""
    runs = await store.list_runs(
        repo=repo, branch=branch, severity=severity, agent=agent, limit=limit
    )

    # Apply search text filter (still in-app since it spans title + description)
    if search:
        import json as json_lib

        search_lower = search.lower()
        filtered = []
        for run in runs:
            try:
                data = json_lib.loads(run.get("result_json", "{}"))
                for ar in data.get("agent_results", []):
                    for f in ar.get("findings", []):
                        if search_lower in f.get("title", "").lower() or search_lower in f.get(
                            "description", ""
                        ).lower():
                            filtered.append(run)
                            break
                    if run in filtered:
                        break
            except Exception:
                pass
        return filtered
    return runs


@app.get("/results/export/json", dependencies=[Depends(_require_api_key)])
async def export_results_json(
    repo: Optional[str] = None,
    branch: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 500,
):
    """Export findings as JSON (streamed for memory efficiency)."""
    import json as json_lib

    async def generate():
        yield "["
        first = True
        offset = 0
        page_size = 50
        while offset < limit:
            runs = await store.list_runs(repo=repo, branch=branch, limit=min(page_size, limit - offset))
            if not runs:
                break
            for run in runs:
                try:
                    data = json_lib.loads(run.get("result_json", "{}"))
                    for ar in data.get("agent_results", []):
                        for finding in ar.get("findings", []):
                            if severity and finding.get("severity") != severity.lower():
                                continue
                            item = {
                                "run_id": run.get("run_id"),
                                "repo": run.get("repo"),
                                "branch": run.get("branch"),
                                "agent": ar.get("agent_name"),
                                "timestamp": run.get("completed_at"),
                                **finding,
                            }
                            if not first:
                                yield ","
                            yield json_lib.dumps(item)
                            first = False
                except Exception:
                    pass
            offset += len(runs)
            if len(runs) < page_size:
                break
        yield "]"

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        generate(),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=findings.json"},
    )


@app.get("/results/export/csv", dependencies=[Depends(_require_api_key)])
async def export_results_csv(
    repo: Optional[str] = None,
    branch: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 500,
):
    """Export findings as CSV (streamed for memory efficiency)."""
    import csv
    import io
    import json as json_lib

    async def generate():
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "run_id",
                "repo",
                "branch",
                "agent",
                "severity",
                "title",
                "description",
                "file_path",
                "line_number",
                "recommendation",
                "timestamp",
            ],
        )
        writer.writeheader()
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        offset = 0
        page_size = 50
        while offset < limit:
            runs = await store.list_runs(repo=repo, branch=branch, limit=min(page_size, limit - offset))
            if not runs:
                break
            for run in runs:
                try:
                    data = json_lib.loads(run.get("result_json", "{}"))
                    for ar in data.get("agent_results", []):
                        for finding in ar.get("findings", []):
                            if severity and finding.get("severity") != severity.lower():
                                continue
                            writer.writerow(
                                {
                                    "run_id": run.get("run_id"),
                                    "repo": run.get("repo"),
                                    "branch": run.get("branch"),
                                    "agent": ar.get("agent_name"),
                                    "severity": finding.get("severity", ""),
                                    "title": finding.get("title", ""),
                                    "description": finding.get("description", ""),
                                    "file_path": finding.get("file_path", ""),
                                    "line_number": finding.get("line_number", ""),
                                    "recommendation": finding.get("recommendation", ""),
                                    "timestamp": run.get("completed_at", ""),
                                }
                            )
                except Exception:
                    pass
            chunk = output.getvalue()
            if chunk:
                yield chunk
            output.truncate(0)
            output.seek(0)
            offset += len(runs)
            if len(runs) < page_size:
                break

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=findings.csv"},
    )


@app.get("/results/{run_id}", dependencies=[Depends(_require_api_key)])
async def get_result(run_id: str):
    data = await store.get_run(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="run not found")
    return data


@app.get("/repos/{repo_name}/findings/trend", dependencies=[Depends(_require_api_key)])
async def findings_trend(repo_name: str, days: int = 30):
    """Return daily finding counts grouped by severity for the given repo."""
    rows = await store.get_findings_trend(repo_name, days=days)
    return {"repo": repo_name, "days": days, "trend": rows}


# ---------------------------------------------------------------------------
# RAG Console
# ---------------------------------------------------------------------------


@app.get("/rag/console", response_class=HTMLResponse)
async def rag_console():
    """Serve the RAG console UI."""
    if not rag_store:
        return "<h1>RAG is not enabled</h1>"
    with open("templates/rag-console.html") as f:
        return f.read()


@app.get("/rag/collections")
async def rag_collections():
    """Get all RAG collections and their document counts."""
    if not rag_store:
        raise HTTPException(status_code=503, detail="RAG is not enabled")
    stats = await rag_store.list_collections()
    return {"collections": stats}


@app.get("/rag/search")
async def rag_search(query: str, collection: str = "best_practices", limit: int = 20):
    """Search the RAG knowledge base."""
    if not rag_store:
        raise HTTPException(status_code=503, detail="RAG is not enabled")

    if collection not in rag_store.COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown collection: {collection}")

    try:
        results = await rag_store.search(collection, query, limit=limit)
        return {
            "query": query,
            "collection": collection,
            "results": results,
        }
    except Exception as e:
        logger.error("RAG search failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.get("/rag/browse")
async def rag_browse(collection: str = "best_practices", limit: int = 100):
    """Browse all documents in a collection."""
    if not rag_store:
        raise HTTPException(status_code=503, detail="RAG is not enabled")

    if collection not in rag_store.COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown collection: {collection}")

    try:
        coll = rag_store.collections.get(collection)
        if not coll:
            raise HTTPException(status_code=400, detail=f"Collection not initialized: {collection}")

        results = coll.get(limit=limit)

        documents = []
        if results and results.get("documents"):
            for i, doc in enumerate(results["documents"]):
                metadata = results["metadatas"][i] if results.get("metadatas") else {}
                documents.append(
                    {
                        "content": doc,
                        "metadata": metadata,
                    }
                )

        return {
            "collection": collection,
            "documents": documents,
        }
    except Exception as e:
        logger.error("RAG browse failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Browse failed: {str(e)}")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    runs, stats, agent_stats = await asyncio.gather(
        store.list_runs(limit=50),
        store.get_stats(),
        store.get_agent_stats()
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"runs": runs, "stats": stats, "agent_stats": agent_stats}
    )


@app.get("/run/{run_id}", response_class=HTMLResponse)
async def run_detail_page(request: Request, run_id: str):
    data = await store.get_run(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="run not found")
    total_findings = sum(len(a.get("findings", [])) for a in data.get("agent_results", []))
    try:
        start = datetime.fromisoformat(data["started_at"])
        end = datetime.fromisoformat(data["completed_at"])
        duration_seconds = round((end - start).total_seconds(), 1)
    except Exception:
        duration_seconds = None
    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={
            "run_id": run_id,
            "result": data,
            "total_findings": total_findings,
            "duration_seconds": duration_seconds,
        },
    )


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


@app.get("/metrics/agents", dependencies=[Depends(_require_api_key)])
async def agent_metrics():
    """Return findings count and severity distribution for each agent."""
    if not store:
        return []
    agent_stats = await store.get_agent_stats()
    # Add effectiveness score (findings per agent, normalized)
    if agent_stats:
        max_findings = max(s["total_findings"] for s in agent_stats)
        for s in agent_stats:
            s["effectiveness_score"] = round(
                (s["critical_count"] * 5 + s["high_count"] * 3 + s["medium_count"] * 1) / max(1, s["total_findings"]),
                2
            )
    return agent_stats


@app.get("/compare/{run_id_1}/{run_id_2}", dependencies=[Depends(_require_api_key)])
async def compare_runs(run_id_1: str, run_id_2: str):
    """Compare findings between two runs. Returns added, removed, and unchanged findings."""
    if not store:
        return {"error": "store not initialized"}
    data1 = await store.get_run(run_id_1)
    data2 = await store.get_run(run_id_2)
    if not data1 or not data2:
        raise HTTPException(status_code=404, detail="one or both runs not found")

    # Extract findings from both runs
    findings_1 = set()
    findings_2 = set()
    findings_by_key_1 = {}
    findings_by_key_2 = {}

    for ar in data1.get("agent_results", []):
        for f in ar.get("findings", []):
            key = (f["title"], f.get("file_path", ""), f.get("line_number", 0))
            findings_1.add(key)
            findings_by_key_1[key] = f

    for ar in data2.get("agent_results", []):
        for f in ar.get("findings", []):
            key = (f["title"], f.get("file_path", ""), f.get("line_number", 0))
            findings_2.add(key)
            findings_by_key_2[key] = f

    added = findings_2 - findings_1
    removed = findings_1 - findings_2
    unchanged = findings_1 & findings_2

    return {
        "run_1": run_id_1,
        "run_2": run_id_2,
        "added": [findings_by_key_2[k] for k in sorted(added)],
        "removed": [findings_by_key_1[k] for k in sorted(removed)],
        "unchanged": [findings_by_key_1[k] for k in sorted(unchanged)],
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "unchanged_count": len(unchanged),
        }
    }
