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
        AgentSpec(agent_class=CodeReviewAgent, file_filter=["*.py", "*.js", "*.ts", "*.tsx", "*.go", "*.rs", "*.java", "*.cpp", "*.c", "*.rb", "*.php"]),
        AgentSpec(agent_class=SecurityAnalystAgent, file_filter=["*.py", "*.js", "*.ts", "*.tsx", "**/auth/**", "**/crypto/**", "**/config/**"]),
        AgentSpec(agent_class=PerformanceAnalystAgent, file_filter=["*.py", "*.js", "*.ts", "*.tsx", "*.go", "*.rs", "*.java"]),
        AgentSpec(agent_class=DepAuditAgent, file_filter=["requirements.txt", "requirements*.txt", "pyproject.toml", "setup.py", "package.json", "yarn.lock", "Gemfile", "Cargo.toml", "pom.xml"]),
        AgentSpec(agent_class=TestCoverageAgent, file_filter=["*.py", "*.js", "*.ts", "*.tsx", "*.go", "*.rs", "*.java"]),
    ],
)
