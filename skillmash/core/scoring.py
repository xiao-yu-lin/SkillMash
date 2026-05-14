from __future__ import annotations

from skillmash.core.models import ExecutionPlan
from skillmash.core.registry import SkillRegistry


class PlanScorer:
    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def score(
        self,
        plan: ExecutionPlan,
        required_capabilities: set[str],
        constraints: dict[str, bool | str | int | float] | None = None,
    ) -> float:
        if plan.missing_requirements:
            plan.score = 0.0
            plan.reason = "Plan is incomplete because some requirements are missing."
            return plan.score

        skills = [self.registry.get(step.skill_id) for step in plan.steps]
        produced = set(plan.produced_artifacts)
        required_outputs = set(plan.required_outputs)
        capabilities = set().union(*(skill.capability_tags for skill in skills)) if skills else set()

        output_match = len(required_outputs & produced) / max(len(required_outputs), 1)
        capability_match = len(required_capabilities & capabilities) / max(
            len(required_capabilities), 1
        )
        reliability = sum(skill.quality.get("reliability", 0.75) for skill in skills) / max(
            len(skills), 1
        )
        freshness = sum(skill.quality.get("freshness", 0.75) for skill in skills) / max(
            len(skills), 1
        )
        latency = sum(skill.cost.get("latency", 1.0) for skill in skills)
        complexity = len(skills)
        explainability = min(1.0, 0.45 + 0.12 * complexity)
        freshness_weight = 0.08 if (constraints or {}).get("fresh_information") else 0.0

        score = (
            output_match * 0.35
            + capability_match * 0.25
            + reliability * 0.2
            + explainability * 0.1
            + freshness * freshness_weight
            - min(latency / 100.0, 0.08)
            - min(complexity / 100.0, 0.06)
        )
        plan.score = max(0.0, min(1.0, score))
        plan.reason = self._reason(plan, output_match, capability_match, reliability)
        return plan.score

    @staticmethod
    def _reason(
        plan: ExecutionPlan,
        output_match: float,
        capability_match: float,
        reliability: float,
    ) -> str:
        return (
            f"Matched {output_match:.0%} of required outputs and "
            f"{capability_match:.0%} of required capabilities; "
            f"average reliability is {reliability:.0%}."
        )
