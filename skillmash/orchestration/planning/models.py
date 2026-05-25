"""Data models for orchestration planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ArtifactRef:
    """A normalized runtime artifact available to the orchestrator."""

    name: str
    type: str = "unknown"
    source: str = "user_query"

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.type)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "source": self.source}


@dataclass(frozen=True)
class GroundedQuery:
    """User query grounded into known artifacts and goal terms."""

    query: str
    query_terms: set[str]
    available_artifacts: list[ArtifactRef]
    goal_terms: set[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "query_terms": sorted(self.query_terms),
            "available_artifacts": [
                artifact.to_dict() for artifact in self.available_artifacts
            ],
            "goal_terms": sorted(self.goal_terms),
        }


@dataclass(frozen=True)
class PlanStep:
    """One Skill call in a candidate orchestration plan."""

    skill_id: str
    name: str
    tasks: list[str]
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    missing_inputs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "tasks": self.tasks,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "missing_inputs": self.missing_inputs,
        }


@dataclass(frozen=True)
class OrchestrationPlan:
    """A candidate Skill orchestration plan."""

    steps: list[PlanStep]
    produced_artifacts: list[ArtifactRef]
    missing_inputs: list[dict[str, Any]]
    can_feed_edges: list[dict[str, Any]]
    goal_score: float
    edge_confidence: float
    consumed_user_artifacts: int
    status: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        from skillmash.orchestration.planning.search import plan_stages

        return {
            "status": self.status,
            "goal_score": round(self.goal_score, 3),
            "edge_confidence": round(self.edge_confidence, 3),
            "consumed_user_artifacts": self.consumed_user_artifacts,
            "stages": plan_stages(self.steps, self.can_feed_edges),
            "steps": [
                {"step": index + 1, **step.to_dict()}
                for index, step in enumerate(self.steps)
            ],
            "produced_artifacts": [
                artifact.to_dict() for artifact in self.produced_artifacts
            ],
            "missing_inputs": self.missing_inputs,
            "can_feed_edges": self.can_feed_edges,
            "reasons": self.reasons,
        }


class GroundingClient(Protocol):
    """Minimal JSON completion interface used by Skill orchestration."""

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        timeout: int | None = None,
        error_context: str = "LLM",
    ) -> str:
        ...


@dataclass(frozen=True)
class PlanningConfig:
    """Runtime planning and ranking knobs.

    Override precedence should be applied by callers as:
    request > runtime service config > manifest defaults.
    """

    min_edge_confidence: float = 0.7
    max_depth: int = 4
    max_plans: int = 20
    max_branch: int = 8
    max_entry_skills: int = 40
    top_m: int = 12
    top_k: int = 3
    include_candidates: bool = True
    conservative_reject: bool = True
    hard_fail_missing_inputs: bool = False
    allow_similar_slot_substitute: bool = False
    relation_feedback_path: str = ".skillmash/runtime/relation_feedback.jsonl"
    relation_feedback_window_days: int = 30


@dataclass(frozen=True)
class SearchState:
    """Internal forward-search state."""

    skill_ids: tuple[str, ...]
    available: frozenset[tuple[str, str]]
    edges: tuple[int, ...]
