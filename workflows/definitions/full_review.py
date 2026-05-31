from agents.code_reviewer import CodeReviewAgent
from agents.dep_auditor import DepAuditAgent
from agents.performance_analyst import PerformanceAnalystAgent
from agents.security_analyst import SecurityAnalystAgent
from agents.test_coverage import TestCoverageAgent
from workflows.base import AgentSpec, ExecutionMode, WorkflowDefinition

FULL_REVIEW = WorkflowDefinition(
    name="full_review",
    description="Sequential full review: code quality → security → performance → deps → test coverage. "
                "Each agent sees prior findings. Used for main/release branches.",
    mode=ExecutionMode.SEQUENTIAL,
    agent_specs=[
        AgentSpec(agent_class=CodeReviewAgent),
        AgentSpec(agent_class=SecurityAnalystAgent),
        AgentSpec(agent_class=PerformanceAnalystAgent),
        AgentSpec(agent_class=DepAuditAgent),
        AgentSpec(agent_class=TestCoverageAgent),
    ],
)
