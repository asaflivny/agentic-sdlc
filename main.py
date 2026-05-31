import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI

from config import get_settings
from models.events import PushEvent
from security import verify_webhook_signature
from workflows.orchestrator import WorkflowOrchestrator
from workflows.router import WorkflowRouter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

router = WorkflowRouter()
orchestrator: WorkflowOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    settings = get_settings()
    orchestrator = WorkflowOrchestrator(settings)
    logger.info("asdlc ready — model=%s", settings.ollama_model)
    yield


app = FastAPI(title="Agentic SDLC", lifespan=lifespan)


@app.post("/git/push", status_code=202, dependencies=[Depends(verify_webhook_signature)])
async def git_push(event: PushEvent, background_tasks: BackgroundTasks):
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
    background_tasks.add_task(_run_workflow, workflow, event)
    return {
        "status": "accepted",
        "workflow": workflow.name,
        "repo": event.repository.name,
        "branch": event.branch,
        "commits": len(event.commits),
    }


async def _run_workflow(workflow, event: PushEvent):
    try:
        result = await orchestrator.run(workflow, event)
        logger.info(
            "completed workflow=%s repo=%s findings=%d",
            result.workflow_name,
            result.repo_name,
            sum(len(r.findings) for r in result.agent_results),
        )
        logger.info("\n%s", result.overall_summary)
    except Exception:
        logger.exception("workflow failed for repo=%s", event.repository.name)
