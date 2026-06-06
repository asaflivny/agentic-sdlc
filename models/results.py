import uuid
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


class TraceContext(BaseModel):
    """Trace context for correlating agent, tool, and delegation calls."""

    run_id: str  # Workflow run ID
    workflow_trace_id: str  # Unique ID for this workflow execution
    agent_trace_id: str  # Unique ID for this agent invocation
    parent_agent_name: Optional[str] = None  # Parent agent if delegated
    parent_agent_trace_id: Optional[str] = None  # Parent's trace ID

    @classmethod
    def from_workflow(cls, run_id: str) -> "TraceContext":
        workflow_trace_id = str(uuid.uuid4())
        return cls(
            run_id=run_id,
            workflow_trace_id=workflow_trace_id,
            agent_trace_id=workflow_trace_id,
        )

    def for_agent(self, agent_name: str, parent_agent_name: Optional[str] = None) -> "TraceContext":
        """Create a child trace context for an agent."""
        return TraceContext(
            run_id=self.run_id,
            workflow_trace_id=self.workflow_trace_id,
            agent_trace_id=str(uuid.uuid4()),
            parent_agent_name=parent_agent_name,
            parent_agent_trace_id=self.agent_trace_id,
        )


class AgentResult(BaseModel):
    agent_name: str
    status: Literal["success", "error", "timeout"]
    findings: list[Finding] = []
    summary: str = ""
    duration_seconds: float = 0.0
    tokens_used: int = 0
    knowledge_used: list[dict] = []  # RAG documents retrieved (title, source, relevance)
    tool_calls_made: list[str] = []  # Names of tools called during execution
    llm_call_count: int = 0  # Number of LLM invocations in the agentic loop
    context_received_bytes: int = 0  # Size of additional_context passed to agent
    context_truncated: bool = False  # Whether additional_context was truncated


class WorkflowResult(BaseModel):
    workflow_name: str
    repo_name: str
    branch: str
    commit_sha: str = ""
    run_id: str = ""
    started_at: datetime
    completed_at: datetime
    agent_results: list[AgentResult]
    overall_summary: str


class AgentContext(BaseModel):
    push_event: PushEvent
    git_diff: str = ""
    additional_context: str = ""
    trace: Optional["TraceContext"] = None
