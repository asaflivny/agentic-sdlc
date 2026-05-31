from agents.security_analyst import SecurityAnalystAgent
from agents.performance_analyst import PerformanceAnalystAgent
from workflows.base import AgentSpec, ExecutionMode, WorkflowDefinition

SECURITY_FOCUS = WorkflowDefinition(
    name="security_focus",
    description="Parallel security + performance review. Triggered when sensitive files "
                "(secrets, auth, certs) are changed.",
    mode=ExecutionMode.PARALLEL,
    agent_specs=[
        AgentSpec(agent_class=SecurityAnalystAgent),
        AgentSpec(agent_class=PerformanceAnalystAgent),
    ],
)
