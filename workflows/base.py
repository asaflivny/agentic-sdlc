from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.base import BaseAgent


class ExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass
class AgentSpec:
    agent_class: type
    can_call: list[str] = field(default_factory=list)


@dataclass
class WorkflowDefinition:
    name: str
    description: str
    mode: ExecutionMode
    agent_specs: list[AgentSpec]
