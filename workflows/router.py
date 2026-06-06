import logging
import re
from abc import ABC, abstractmethod

from models.events import PushEvent
from workflows.base import WorkflowDefinition
from workflows.definitions.full_review import FULL_REVIEW
from workflows.definitions.security_focus import SECURITY_FOCUS
from workflows.definitions.quick_review import QUICK_REVIEW

logger = logging.getLogger(__name__)


class RoutingRule(ABC):
    priority: int
    name: str

    @abstractmethod
    def matches(self, event: PushEvent) -> bool: ...

    @property
    @abstractmethod
    def workflow(self) -> WorkflowDefinition: ...


class BranchPatternRule(RoutingRule):
    def __init__(self, priority: int, name: str, pattern: str, workflow: WorkflowDefinition):
        self.priority = priority
        self.name = name
        self._pattern = re.compile(pattern)
        self._workflow = workflow

    def matches(self, event: PushEvent) -> bool:
        return bool(self._pattern.match(event.branch))

    @property
    def workflow(self) -> WorkflowDefinition:
        return self._workflow


class FilePatternRule(RoutingRule):
    def __init__(self, priority: int, name: str, pattern: str, workflow: WorkflowDefinition):
        self.priority = priority
        self.name = name
        self._pattern = re.compile(pattern, re.IGNORECASE)
        self._workflow = workflow

    def matches(self, event: PushEvent) -> bool:
        all_files = [
            f for commit in event.commits for f in (commit.added + commit.modified + commit.removed)
        ]
        return any(self._pattern.search(f) for f in all_files)

    @property
    def workflow(self) -> WorkflowDefinition:
        return self._workflow


class DefaultRule(RoutingRule):
    priority = 99
    name = "default"

    def matches(self, _: PushEvent) -> bool:
        return True

    @property
    def workflow(self) -> WorkflowDefinition:
        return QUICK_REVIEW


class WorkflowRouter:
    def __init__(self):
        self._rules: list[RoutingRule] = sorted(
            [
                BranchPatternRule(
                    priority=10,
                    name="main_branch",
                    pattern=r"^(main|master|release/.+)$",
                    workflow=FULL_REVIEW,
                ),
                FilePatternRule(
                    priority=20,
                    name="sensitive_files",
                    pattern=r"\.(env|pem|key|cert|p12|pfx)$|"
                    r"(secret|credential|password|token|auth|oauth|jwt)",
                    workflow=SECURITY_FOCUS,
                ),
                DefaultRule(),
            ],
            key=lambda r: r.priority,
        )

    def route(self, event: PushEvent) -> WorkflowDefinition:
        all_files = [
            f for commit in event.commits for f in (commit.added + commit.modified + commit.removed)
        ]
        logger.debug(
            "routing decision repo=%s branch=%s files_changed=%d",
            event.repository.name,
            event.branch,
            len(all_files),
        )
        for rule in self._rules:
            if rule.matches(event):
                logger.info(
                    "routed repo=%s branch=%s → workflow=%s (rule=%s)",
                    event.repository.name,
                    event.branch,
                    rule.workflow.name,
                    rule.name,
                )
                logger.debug(
                    "rule_details rule=%s agents=%d mode=%s",
                    rule.name,
                    len(rule.workflow.agent_specs),
                    rule.workflow.mode,
                )
                return rule.workflow
        return QUICK_REVIEW
