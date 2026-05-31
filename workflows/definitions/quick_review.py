from agents.code_reviewer import CodeReviewAgent
from workflows.base import AgentSpec, ExecutionMode, WorkflowDefinition

QUICK_REVIEW = WorkflowDefinition(
    name="quick_review",
    description="Single code reviewer that can sub-delegate to security and performance agents. "
                "Default workflow for feature branches.",
    mode=ExecutionMode.SEQUENTIAL,
    agent_specs=[
        AgentSpec(
            agent_class=CodeReviewAgent,
            can_call=["security_analyst", "performance_analyst"],
        ),
    ],
)
