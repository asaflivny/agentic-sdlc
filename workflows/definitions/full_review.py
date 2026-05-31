from agents.code_reviewer import CodeReviewAgent
from agents.security_analyst import SecurityAnalystAgent
from agents.performance_analyst import PerformanceAnalystAgent
from workflows.base import AgentSpec, ExecutionMode, WorkflowDefinition

FULL_REVIEW = WorkflowDefinition(
    name="full_review",
    description="Sequential full review: code quality → security → performance. "
                "Each agent sees prior findings. Used for main/release branches.",
    mode=ExecutionMode.SEQUENTIAL,
    agent_specs=[
        AgentSpec(agent_class=CodeReviewAgent),
        AgentSpec(agent_class=SecurityAnalystAgent),
        AgentSpec(agent_class=PerformanceAnalystAgent),
    ],
)
