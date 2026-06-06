from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel

from models.events import PushEvent


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    title: str
    description: str
    severity: Severity
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    recommendation: str


class FindingList(BaseModel):
    """Container the LLM fills directly via structured output (replaces sentinel parsing)."""
    findings: list[Finding] = []


class AgentResult(BaseModel):
    agent_name: str
    status: Literal["success", "error", "timeout"]
    findings: list[Finding] = []
    summary: str = ""
    duration_seconds: float = 0.0
    tokens_used: int = 0
    knowledge_used: list[dict] = []  # RAG documents retrieved (title, source, relevance)


class WorkflowResult(BaseModel):
    workflow_name: str
    repo_name: str
    branch: str
    started_at: datetime
    completed_at: datetime
    agent_results: list[AgentResult]
    overall_summary: str


class AgentContext(BaseModel):
    push_event: PushEvent
    git_diff: str = ""
    additional_context: str = ""
