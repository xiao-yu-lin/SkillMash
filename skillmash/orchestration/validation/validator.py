"""Deterministic hard-gate validation for candidate orchestration plans."""

from __future__ import annotations

from collections import Counter
from typing import Any

from skillmash.orchestration.validation.policy import (
    HARD_FAIL_LOW_CONFIDENCE_EDGE,
    HARD_FAIL_MISSING_REQUIRED_INPUT,
    HARD_FAIL_NO_EXPLICIT_CAN_FEED,
)


def hard_filter_plans(
    plans: list[dict[str, Any]],
    *,
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, list[str]]]:
    passed: list[dict[str, Any]] = []
    fail_counts: Counter[str] = Counter()
    plan_fail_reasons: dict[str, list[str]] = {}

    min_conf = float(policy.get("min_edge_confidence", 0.7))
    require_explicit = bool(policy.get("require_explicit_adjacency", True))

    for index, plan in enumerate(plans, start=1):
        plan_id = f"plan_{index}"
        reasons: set[str] = set()

        if plan.get("missing_inputs"):
            reasons.add(HARD_FAIL_MISSING_REQUIRED_INPUT)

        edges = list(plan.get("can_feed_edges") or [])
        for edge in edges:
            confidence = float(edge.get("confidence") or 0.0)
            if confidence < min_conf:
                reasons.add(HARD_FAIL_LOW_CONFIDENCE_EDGE)

        if require_explicit and (plan.get("steps") and len(plan.get("steps", [])) > 1):
            adjacency = {
                (
                    str(edge.get("source_id") or ""),
                    str(edge.get("target_id") or ""),
                )
                for edge in edges
                if edge.get("source_id") and edge.get("target_id")
            }
            steps = [
                str(step.get("skill_id") or "")
                for step in plan.get("steps", [])
                if step.get("skill_id")
            ]
            if not adjacency:
                reasons.add(HARD_FAIL_NO_EXPLICIT_CAN_FEED)
            else:
                incoming = {skill_id: 0 for skill_id in steps}
                for source, target in adjacency:
                    if source in incoming and target in incoming:
                        incoming[target] += 1
                roots = [skill_id for skill_id, count in incoming.items() if count == 0]
                if len(roots) != 1:
                    reasons.add(HARD_FAIL_NO_EXPLICIT_CAN_FEED)

        if reasons:
            sorted_reasons = sorted(reasons)
            plan_fail_reasons[plan_id] = sorted_reasons
            for reason in sorted_reasons:
                fail_counts[reason] += 1
            continue

        passed.append(plan)

    return passed, dict(fail_counts), plan_fail_reasons
