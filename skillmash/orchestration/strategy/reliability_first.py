"""Default reliability-first strategy implementation."""

from __future__ import annotations

from skillmash.orchestration.strategy.interfaces import FilterResult, PlanStrategy, PruneContext


class ReliabilityFirstStrategy(PlanStrategy):
    name = "reliability_first"

    def hard_filter(self, plan: dict[str, object], ctx: PruneContext) -> FilterResult:
        missing = bool(plan.get("missing_inputs"))
        return FilterResult(
            passed=not missing,
            hard_fail_codes=["missing_required_input"] if missing else [],
        )

    def rank_score(self, plan: dict[str, object], ctx: PruneContext) -> float:
        edge_conf = float(plan.get("edge_confidence") or 0.0)
        min_edge = edge_conf
        edges = list(plan.get("can_feed_edges") or [])
        if edges:
            min_edge = min(float(edge.get("confidence") or 0.0) for edge in edges)
        step_penalty = len(plan.get("steps") or []) * 0.01
        missing_penalty = len(plan.get("missing_inputs") or []) * 1.0
        return min_edge * 2.0 + edge_conf - step_penalty - missing_penalty
