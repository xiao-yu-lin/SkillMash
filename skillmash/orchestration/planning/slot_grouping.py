"""Slot grouping utilities for parallel downstream candidates."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def build_slot_groups(
    plans: list[dict[str, Any]],
    relation_edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach slot candidate groups to each plan.

    Slot candidates are built from substitute/similar relations and the current
    step skill itself. Selection still happens in later validation/ranking.
    """

    undirected_candidates: dict[str, set[str]] = defaultdict(set)
    for edge in relation_edges:
        edge_type = str(edge.get("type") or "")
        if edge_type not in {"substitute_for", "similar_to"}:
            continue
        source = _skill_id(edge.get("source"))
        target = _skill_id(edge.get("target"))
        if not source or not target:
            continue
        undirected_candidates[source].add(target)
        undirected_candidates[target].add(source)

    grouped: list[dict[str, Any]] = []
    for plan in plans:
        steps = [
            str(step.get("skill_id") or "")
            for step in plan.get("steps", [])
            if step.get("skill_id")
        ]
        slots: list[dict[str, Any]] = []
        for index in range(1, len(steps)):
            current = steps[index]
            candidates = {current}
            candidates.update(undirected_candidates.get(current, set()))
            slots.append(
                {
                    "slot_index": index + 1,
                    "candidates": sorted(candidates),
                }
            )
        grouped.append({**plan, "slots": slots})
    return grouped


def _skill_id(value: Any) -> str:
    return str(value or "").removeprefix("skill:")
