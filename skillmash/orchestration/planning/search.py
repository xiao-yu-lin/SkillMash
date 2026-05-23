"""Forward plan search and plan shaping."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.orchestration.planning.models import (
    ArtifactRef,
    GroundedQuery,
    OrchestrationPlan,
    PlanStep,
    SearchState,
)
from skillmash.orchestration.planning.utils import skill_id, tokenize


def search_plans(
    *,
    artifacts: BuildArtifacts,
    skill_by_id: dict[str, dict[str, Any]],
    can_feed_edges: list[dict[str, Any]],
    outgoing_edges: dict[str, list[int]],
    grounded: GroundedQuery,
    max_depth: int,
    max_plans: int,
    max_branch: int,
) -> list[OrchestrationPlan]:
    initial_available = frozenset(artifact.key for artifact in grounded.available_artifacts)
    entry_ids = entry_skill_ids(
        artifacts=artifacts,
        available=initial_available,
        goal_terms=grounded.goal_terms,
        max_branch=max_branch,
    )
    queue = deque(
        SearchState(skill_ids=(skill_id_,), available=initial_available, edges=())
        for skill_id_ in entry_ids
    )
    plans: list[OrchestrationPlan] = []
    seen: set[tuple[tuple[str, ...], frozenset[tuple[str, str]]]] = set()

    while queue and len(plans) < max_plans * 4:
        state = queue.popleft()
        key = (state.skill_ids, state.available)
        if key in seen:
            continue
        seen.add(key)

        plan = state_to_plan(
            state=state,
            grounded=grounded,
            skill_by_id=skill_by_id,
            can_feed_edges=can_feed_edges,
        )
        if plan.goal_score > 0:
            plans.append(plan)
        if len(state.skill_ids) >= max_depth:
            continue

        last_id = state.skill_ids[-1]
        next_ids = next_skill_ids(
            last_id=last_id,
            state=state,
            goal_terms=grounded.goal_terms,
            outgoing_edges=outgoing_edges,
            can_feed_edges=can_feed_edges,
            skill_by_id=skill_by_id,
        )
        for edge_index, next_id in next_ids[:max_branch]:
            if next_id in state.skill_ids:
                continue
            next_skill = skill_by_id.get(next_id)
            last_skill = skill_by_id.get(last_id)
            if not next_skill or not last_skill:
                continue
            queue.append(
                SearchState(
                    skill_ids=(*state.skill_ids, next_id),
                    available=(
                        state.available
                        | output_keys(last_skill)
                        | output_keys(next_skill)
                    ),
                    edges=(*state.edges, edge_index),
                )
            )

    if not plans:
        return [
            state_to_plan(
                state=SearchState(skill_ids=(skill_id_,), available=initial_available, edges=()),
                grounded=grounded,
                skill_by_id=skill_by_id,
                can_feed_edges=can_feed_edges,
            )
            for skill_id_ in entry_ids[:max_plans]
        ]
    return compose_dag_plans(dedupe_plans(plans), max_plans=max_plans)


def entry_skill_ids(
    *,
    artifacts: BuildArtifacts,
    available: frozenset[tuple[str, str]],
    goal_terms: set[str],
    max_branch: int,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    for skill in artifacts.skills:
        current_skill_id = skill.get("id", "")
        if not current_skill_id:
            continue
        input_score = input_coverage_score(skill, available)
        goal_score = skill_goal_score(skill, goal_terms)
        if input_score <= 0 and goal_score <= 0:
            continue
        scored.append((input_score * 4.0 + goal_score, current_skill_id))
    return [
        current_skill_id
        for _, current_skill_id in sorted(scored, key=lambda item: (-item[0], item[1]))
    ][:max_branch]


def next_skill_ids(
    *,
    last_id: str,
    state: SearchState,
    goal_terms: set[str],
    outgoing_edges: dict[str, list[int]],
    can_feed_edges: list[dict[str, Any]],
    skill_by_id: dict[str, dict[str, Any]],
) -> list[tuple[int, str]]:
    candidates: list[tuple[float, int, str]] = []
    for edge_index in outgoing_edges.get(last_id, []):
        edge = can_feed_edges[edge_index]
        target_id = skill_id(edge.get("target"))
        if target_id in state.skill_ids:
            continue
        target = skill_by_id.get(target_id)
        if not target:
            continue
        score = (
            float(edge.get("confidence") or 0.0) * 4.0
            + input_coverage_score(target, state.available) * 2.0
            + skill_goal_score(target, goal_terms)
        )
        candidates.append((score, edge_index, target_id))
    return [
        (edge_index, target_id)
        for _, edge_index, target_id in sorted(candidates, key=lambda item: (-item[0], item[2]))
    ]


def state_to_plan(
    *,
    state: SearchState,
    grounded: GroundedQuery,
    skill_by_id: dict[str, dict[str, Any]],
    can_feed_edges: list[dict[str, Any]],
) -> OrchestrationPlan:
    available = set(state.available)
    steps: list[PlanStep] = []
    all_missing: list[dict[str, Any]] = []
    produced: set[tuple[str, str]] = set()
    reasons: list[str] = []

    for current_skill_id in state.skill_ids:
        skill = skill_by_id[current_skill_id]
        missing = missing_inputs(skill, available)
        all_missing.extend({**item, "skill_id": current_skill_id} for item in missing)
        outputs = output_keys(skill)
        produced.update(outputs)
        available.update(outputs)
        steps.append(
            PlanStep(
                skill_id=current_skill_id,
                name=skill.get("name", current_skill_id),
                tasks=list(skill.get("tasks", [])),
                inputs=[
                    {"name": item.get("name"), "type": item.get("type")}
                    for item in skill.get("inputs", [])
                ],
                outputs=[
                    {"name": item.get("name"), "type": item.get("type")}
                    for item in skill.get("outputs", [])
                ],
                missing_inputs=missing,
            )
        )
        score = skill_goal_score(skill, grounded.goal_terms)
        if score > 0:
            reasons.append(f"{current_skill_id} matches goal terms")

    edges = [can_feed_edges[index] for index in state.edges]
    edge_confidence = (
        sum(float(edge.get("confidence") or 0.0) for edge in edges) / len(edges)
        if edges
        else 1.0
    )
    goal_score = plan_goal_score(
        skill_ids=state.skill_ids,
        produced=produced,
        skill_by_id=skill_by_id,
        goal_terms=grounded.goal_terms,
    )
    status = "ready" if not all_missing else "needs_input"
    return OrchestrationPlan(
        steps=steps,
        produced_artifacts=[
            ArtifactRef(name=name, type=type_, source="skill_output")
            for name, type_ in sorted(produced)
        ],
        missing_inputs=all_missing,
        can_feed_edges=[edge_plan_item(edge) for edge in edges],
        goal_score=goal_score,
        edge_confidence=edge_confidence,
        consumed_user_artifacts=consumed_user_artifact_count(
            steps,
            grounded.available_artifacts,
        ),
        status=status,
        reasons=reasons[:8],
    )


def build_outgoing_edges(edges: list[dict[str, Any]]) -> dict[str, list[int]]:
    outgoing: dict[str, list[int]] = defaultdict(list)
    for index, edge in enumerate(edges):
        outgoing[skill_id(edge.get("source"))].append(index)
    return outgoing


def input_coverage_score(skill: dict[str, Any], available: Iterable[tuple[str, str]]) -> float:
    required = [
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("inputs", [])
        if item.get("required", True) and item.get("name")
    ]
    if not required:
        return 1.0
    available_set = set(available)
    matched = sum(1 for item in required if artifact_matches(item, available_set))
    return matched / len(required)


def consumed_user_artifact_count(
    steps: list[PlanStep],
    artifacts: list[ArtifactRef],
) -> int:
    explicit = {
        artifact.key
        for artifact in artifacts
        if artifact.source != "implicit_query"
    }
    if not steps or not explicit:
        return 0
    consumed = 0
    for item in steps[0].inputs:
        expected = (str(item.get("name")), str(item.get("type") or "unknown"))
        if artifact_matches(expected, explicit):
            consumed += 1
    return consumed


def missing_inputs(
    skill: dict[str, Any],
    available: Iterable[tuple[str, str]],
) -> list[dict[str, Any]]:
    available_set = set(available)
    missing = []
    for item in skill.get("inputs", []):
        if not item.get("required", True):
            continue
        expected = (str(item.get("name")), str(item.get("type") or "unknown"))
        if not artifact_matches(expected, available_set):
            missing.append({"name": expected[0], "type": expected[1]})
    return missing


def artifact_matches(
    expected: tuple[str, str],
    available: set[tuple[str, str]],
) -> bool:
    name, type_ = expected
    if expected in available:
        return True
    return any(
        candidate_name == name and (candidate_type == "unknown" or type_ == "unknown")
        for candidate_name, candidate_type in available
    )


def output_keys(skill: dict[str, Any]) -> frozenset[tuple[str, str]]:
    return frozenset(
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("outputs", [])
        if item.get("name")
    )


def plan_goal_score(
    *,
    skill_ids: tuple[str, ...],
    produced: set[tuple[str, str]],
    skill_by_id: dict[str, dict[str, Any]],
    goal_terms: set[str],
) -> float:
    skill_score = sum(
        skill_goal_score(skill_by_id[current_skill_id], goal_terms)
        for current_skill_id in skill_ids
        if current_skill_id in skill_by_id
    )
    output_score = sum(
        len(tokenize(name) & goal_terms) * 2.0 + len(tokenize(type_) & goal_terms)
        for name, type_ in produced
    )
    return skill_score + output_score


def skill_goal_score(skill: dict[str, Any], goal_terms: set[str]) -> float:
    terms = skill_terms(skill)
    task_terms = set()
    for task in skill.get("tasks", []):
        task_terms.update(tokenize(task))
    output_terms = set()
    for output in skill.get("outputs", []):
        output_terms.update(tokenize(output.get("name", "")))
        output_terms.update(tokenize(output.get("description", "")))
    return (
        len(terms & goal_terms)
        + len(task_terms & goal_terms) * 3.0
        + len(output_terms & goal_terms) * 2.0
    )


def skill_terms(skill: dict[str, Any]) -> set[str]:
    chunks = [
        skill.get("id", ""),
        skill.get("name", ""),
        skill.get("description", ""),
    ]
    chunks.extend(skill.get("tasks", []))
    for item in skill.get("inputs", []):
        chunks.extend(
            [item.get("name", ""), item.get("type", ""), item.get("description", "")]
        )
    for item in skill.get("outputs", []):
        chunks.extend(
            [item.get("name", ""), item.get("type", ""), item.get("description", "")]
        )
    terms: set[str] = set()
    for chunk in chunks:
        terms.update(tokenize(str(chunk)))
    return terms


def dedupe_plans(plans: list[OrchestrationPlan]) -> list[OrchestrationPlan]:
    deduped: dict[tuple[str, ...], OrchestrationPlan] = {}
    for plan in plans:
        key = tuple(step.skill_id for step in plan.steps)
        existing = deduped.get(key)
        if existing is None or plan.goal_score > existing.goal_score:
            deduped[key] = plan
    return list(deduped.values())


def compose_dag_plans(
    path_plans: list[OrchestrationPlan],
    *,
    max_plans: int,
) -> list[OrchestrationPlan]:
    composed: list[OrchestrationPlan] = []
    groups: dict[str, list[OrchestrationPlan]] = defaultdict(list)
    for plan in path_plans:
        if plan.steps:
            groups[plan.steps[0].skill_id].append(plan)

    for group in groups.values():
        branch_plans = [plan for plan in group if len(plan.steps) > 1]
        if len(branch_plans) < 2:
            continue
        composed.append(compose_plan_group(branch_plans))

    return [*composed, *path_plans][: max_plans * 2]


def compose_plan_group(plans: list[OrchestrationPlan]) -> OrchestrationPlan:
    steps_by_id: dict[str, PlanStep] = {}
    edges_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    produced_by_key: dict[tuple[str, str], ArtifactRef] = {}
    missing_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    reasons: list[str] = []

    for plan in plans:
        for step in plan.steps:
            steps_by_id.setdefault(step.skill_id, step)
        for edge in plan.can_feed_edges:
            key = (str(edge.get("source_id") or ""), str(edge.get("target_id") or ""))
            if key[0] and key[1]:
                edges_by_key.setdefault(key, edge)
        for artifact in plan.produced_artifacts:
            produced_by_key.setdefault(artifact.key, artifact)
        for item in plan.missing_inputs:
            key = (
                str(item.get("skill_id") or ""),
                str(item.get("name") or ""),
                str(item.get("type") or "unknown"),
            )
            missing_by_key.setdefault(key, dict(item))
        for reason in plan.reasons:
            if reason not in reasons:
                reasons.append(reason)

    ordered_step_ids = topological_step_ids(
        set(steps_by_id),
        list(edges_by_key.values()),
    )
    steps = [steps_by_id[current_skill_id] for current_skill_id in ordered_step_ids]
    missing_inputs_list = list(missing_by_key.values())
    edge_confidence = (
        sum(float(edge.get("confidence") or 0.0) for edge in edges_by_key.values())
        / len(edges_by_key)
        if edges_by_key
        else 1.0
    )
    return OrchestrationPlan(
        steps=steps,
        produced_artifacts=list(produced_by_key.values()),
        missing_inputs=missing_inputs_list,
        can_feed_edges=list(edges_by_key.values()),
        goal_score=sum(plan.goal_score for plan in plans),
        edge_confidence=edge_confidence,
        consumed_user_artifacts=max(plan.consumed_user_artifacts for plan in plans),
        status="ready" if not missing_inputs_list else "needs_input",
        reasons=reasons[:8],
    )


def topological_step_ids(
    skill_ids: set[str],
    edges: list[dict[str, Any]],
) -> list[str]:
    incoming = {current_skill_id: 0 for current_skill_id in skill_ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if source_id not in skill_ids or target_id not in skill_ids:
            continue
        outgoing[source_id].append(target_id)
        incoming[target_id] += 1

    queue = deque(sorted(current_skill_id for current_skill_id, count in incoming.items() if count == 0))
    ordered: list[str] = []
    while queue:
        current_skill_id = queue.popleft()
        ordered.append(current_skill_id)
        for target_id in sorted(outgoing.get(current_skill_id, [])):
            incoming[target_id] -= 1
            if incoming[target_id] == 0:
                queue.append(target_id)
    ordered.extend(sorted(skill_ids - set(ordered)))
    return ordered


def plan_stages(
    steps: list[PlanStep],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    step_by_id = {step.skill_id: step for step in steps}
    remaining = set(step_by_id)
    incoming: dict[str, set[str]] = {current_skill_id: set() for current_skill_id in remaining}
    for edge in edges:
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if source_id in remaining and target_id in remaining:
            incoming[target_id].add(source_id)

    stages = []
    completed: set[str] = set()
    while remaining:
        ready = sorted(
            current_skill_id
            for current_skill_id in remaining
            if incoming[current_skill_id] <= completed
        )
        if not ready:
            ready = sorted(remaining)
        stages.append(
            {
                "stage": len(stages) + 1,
                "skills": [step_by_id[current_skill_id].to_dict() for current_skill_id in ready],
            }
        )
        completed.update(ready)
        remaining.difference_update(ready)
    return stages


def edge_plan_item(edge: dict[str, Any]) -> dict[str, Any]:
    evidence = edge.get("evidence") or {}
    supporting_fields = evidence.get("supporting_fields") or {}
    return {
        "source_id": skill_id(edge.get("source")),
        "target_id": skill_id(edge.get("target")),
        "confidence": edge.get("confidence"),
        "method": edge.get("method"),
        "source_outputs": supporting_fields.get("source_outputs", [])[:3],
        "target_inputs": supporting_fields.get("target_inputs", [])[:3],
        "reasons": evidence.get("reasons", [])[:3],
    }
