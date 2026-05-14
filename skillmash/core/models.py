"""Core data model shared by registry, graph, planner, and service layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillKind(str, Enum):
    """How a Skill should be interpreted by decomposition and planning."""

    ATOMIC = "atomic"
    COMPOSITE = "composite"
    WRAPPED = "wrapped"


class CompositionOperator(str, Enum):
    """Supported composition patterns between Skills."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CHOICE = "choice"
    ITERATIVE = "iterative"


@dataclass(frozen=True)
class ParameterSpec:
    """A required or optional input artifact for a Skill."""

    name: str
    type: str
    required: bool = True
    description: str = ""
    default: Any = None


@dataclass(frozen=True)
class ArtifactSpec:
    """An output artifact produced by a Skill."""

    name: str
    type: str
    description: str = ""


@dataclass(frozen=True)
class Condition:
    type: str
    expression: str
    description: str = ""


@dataclass(frozen=True)
class Composition:
    """Declarative composition metadata for a composite Skill."""

    operator: CompositionOperator
    steps: tuple[str, ...] = ()


@dataclass
class SkillDefinition:
    """Normalized Skill record used after registration or offline import."""

    id: str
    name: str
    kind: SkillKind
    description: str = ""
    version: str = "1.0.0"
    inputs: list[ParameterSpec] = field(default_factory=list)
    outputs: list[ArtifactSpec] = field(default_factory=list)
    preconditions: list[Condition] = field(default_factory=list)
    postconditions: list[Condition] = field(default_factory=list)
    # Historical internal field name. The external artifact schema exposes this
    # as ``skill_tags`` so product/design language can stay Skill-oriented.
    capability_tags: set[str] = field(default_factory=set)
    data_tags: set[str] = field(default_factory=set)
    contains: list[str] = field(default_factory=list)
    composition: Composition | None = None
    cost: dict[str, float] = field(default_factory=dict)
    quality: dict[str, float] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def output_types(self) -> set[str]:
        return {output.type for output in self.outputs}

    def input_types(self) -> set[str]:
        return {param.type for param in self.inputs}

    def required_input_types(self) -> set[str]:
        return {param.type for param in self.inputs if param.required}

    def required_input_names(self) -> set[str]:
        return {param.name for param in self.inputs if param.required}


@dataclass(frozen=True)
class MatchResult:
    """Result of checking whether two Skills can be composed."""

    source_skill_id: str
    target_skill_id: str
    composable: bool
    operator: CompositionOperator | None = None
    compatibility: str = "no_match"
    score: float = 0.0
    input_mapping: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanStep:
    """One executable step in a generated plan."""

    skill_id: str
    operator: CompositionOperator
    input_mapping: dict[str, str] = field(default_factory=dict)
    output_mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """A candidate execution plan returned by the online planner."""

    id: str
    task: str
    steps: list[PlanStep]
    score: float = 0.0
    reason: str = ""
    required_outputs: list[str] = field(default_factory=list)
    produced_artifacts: list[str] = field(default_factory=list)
    atomic_skills: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ready" if not self.missing_requirements else "incomplete"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "score": round(self.score, 4),
            "reason": self.reason,
            "required_outputs": self.required_outputs,
            "produced_artifacts": self.produced_artifacts,
            "atomic_skills": self.atomic_skills,
            "missing_requirements": self.missing_requirements,
            "steps": [
                {
                    "skill_id": step.skill_id,
                    "operator": step.operator.value,
                    "input_mapping": step.input_mapping,
                    "output_mapping": step.output_mapping,
                }
                for step in self.steps
            ],
        }
