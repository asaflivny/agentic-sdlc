"""Parse per-repo .asdlc.yml and apply overrides to a resolved workflow + agent list."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from models.events import PushEvent
    from workflows.base import WorkflowDefinition

logger = logging.getLogger(__name__)


class RoutingRule(BaseModel):
    """Custom routing rule from .asdlc.yml."""

    pattern: str
    workflow: str


class AgentsConfig(BaseModel):
    """Agent configuration from .asdlc.yml."""

    exclude: list[str] = Field(default_factory=list)


class RepoConfig(BaseModel):
    """Validated schema for .asdlc.yml."""

    workflow: Optional[str] = None
    agents: Optional[AgentsConfig] = None
    routing: Optional[list[RoutingRule]] = None

    class Config:
        extra = "ignore"  # Ignore unknown fields


def _load_yaml(raw: str) -> dict:
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            logger.warning("repo_config: .asdlc.yml is not a dict, ignoring")
            return {}
        # Validate schema
        RepoConfig(**data)
        logger.info("repo_config: loaded and validated .asdlc.yml")
        # Return the validated dict representation (dropping Pydantic internals)
        return {k: v for k, v in data.items() if k in {"workflow", "agents", "routing"}}
    except ValidationError as e:
        logger.warning("repo_config: schema validation failed: %s", e)
        return {}
    except Exception as e:
        logger.warning("repo_config: failed to parse .asdlc.yml: %s", e)
        return {}


async def load_repo_overrides(event: "PushEvent") -> dict:
    """Fetch .asdlc.yml from the repo at HEAD and return the parsed dict.

    Returns {} if the file is absent, malformed, or the repo is not accessible.
    Result is intentionally not cached — the caller caches per run_id if needed.
    """
    from tools.git_tools import get_file_content

    repo_url = event.repository.clone_url
    if not repo_url:
        return {}

    result = await get_file_content(repo_url, ".asdlc.yml", event.after)
    if result.is_error:
        return {}
    return _load_yaml(result.content)


def apply_overrides(
    workflow: "WorkflowDefinition",
    agent_names: list[str],
    overrides: dict,
) -> tuple["WorkflowDefinition", list[str]]:
    """Apply repo-level overrides to (workflow, agent_names).

    Supported keys:
        workflow: str          — switch to a named workflow
        agents.exclude: list   — drop agents by name
    Returns (workflow, agent_names) with overrides applied.
    """
    from workflows.definitions.full_review import FULL_REVIEW
    from workflows.definitions.security_focus import SECURITY_FOCUS
    from workflows.definitions.quick_review import QUICK_REVIEW

    _WORKFLOW_MAP = {
        "full_review": FULL_REVIEW,
        "security_focus": SECURITY_FOCUS,
        "quick_review": QUICK_REVIEW,
    }

    if not overrides:
        return workflow, agent_names

    # Workflow override
    wf_name = overrides.get("workflow", "")
    if wf_name and wf_name in _WORKFLOW_MAP:
        new_wf = _WORKFLOW_MAP[wf_name]
        if new_wf.name != workflow.name:
            logger.info("repo_config: switching workflow %s → %s", workflow.name, wf_name)
            workflow = new_wf
            # Rebuild agent_names from the new workflow's specs
            agent_names = [spec.agent_class.name for spec in new_wf.agent_specs]

    # Agent exclusions
    agents_cfg = overrides.get("agents", {})
    if isinstance(agents_cfg, dict):
        exclude = agents_cfg.get("exclude", [])
        if isinstance(exclude, list) and exclude:
            before = set(agent_names)
            agent_names = [n for n in agent_names if n not in exclude]
            removed = before - set(agent_names)
            if removed:
                logger.info("repo_config: excluded agents=%s", sorted(removed))

    return workflow, agent_names
