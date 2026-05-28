"""Forward plan search and plan shaping."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.orchestration.planning.models import (
    ArtifactRef,
    GroundedQuery,
    InferredInput,
    OrchestrationPlan,
    PlanStep,
    SearchState,
)
from skillmash.orchestration.planning.utils import skill_id, tokenize

BEAM_MISSING_INPUT_PENALTY = 2.0


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
    max_entry_skills: int,
    beam_width: int,
) -> list[OrchestrationPlan]:
    initial_available = frozenset(artifact.key for artifact in grounded.available_artifacts)
    entry_ids = entry_skill_ids(
        artifacts=artifacts,
        available=initial_available,
        inferred_inputs=grounded.inferred_inputs,
        goal_terms=grounded.goal_terms,
        max_entry_skills=max_entry_skills,
    )
    initial_states = [
        SearchState(skill_ids=(skill_id_,), available=initial_available, edges=())
        for skill_id_ in entry_ids
    ]
    frontier = select_beam_states(
        initial_states,
        grounded=grounded,
        skill_by_id=skill_by_id,
        can_feed_edges=can_feed_edges,
        beam_width=beam_width,
    )
    plans: list[OrchestrationPlan] = []
    seen: set[tuple[tuple[str, ...], frozenset[tuple[str, str]]]] = set()

    while frontier and len(plans) < max_plans * 4:
        next_frontier: list[SearchState] = []
        for state in frontier:
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
            remaining_depth = max_depth - len(state.skill_ids)
            if plan.goal_score <= 0 and not can_reach_goal_relevant_skill(
                last_id=state.skill_ids[-1],
                outgoing_edges=outgoing_edges,
                can_feed_edges=can_feed_edges,
                skill_by_id=skill_by_id,
                goal_terms=grounded.goal_terms,
                max_hops=remaining_depth,
            ):
                continue
            if plan.goal_score > 0:
                plans.append(plan)
            if len(state.skill_ids) >= max_depth:
                continue

            last_id = state.skill_ids[-1]
            next_ids = next_skill_ids(
                last_id=last_id,
                state=state,
                inferred_inputs=grounded.inferred_inputs,
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
                next_frontier.append(
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
        frontier = select_beam_states(
            next_frontier,
            grounded=grounded,
            skill_by_id=skill_by_id,
            can_feed_edges=can_feed_edges,
            beam_width=beam_width,
        )

    if not plans:
        fallback_plans = []
        for skill_id_ in entry_ids[:max_plans]:
            plan = state_to_plan(
                state=SearchState(skill_ids=(skill_id_,), available=initial_available, edges=()),
                grounded=grounded,
                skill_by_id=skill_by_id,
                can_feed_edges=can_feed_edges,
            )
            if plan.goal_score > 0:
                fallback_plans.append(plan)
        return fallback_plans
    return compose_dag_plans(dedupe_plans(plans), max_plans=max_plans)


def search_backward_plans(
    *,
    artifacts: BuildArtifacts,
    skill_by_id: dict[str, dict[str, Any]],
    can_feed_edges: list[dict[str, Any]],
    incoming_edges: dict[str, list[int]],
    grounded: GroundedQuery,
    max_depth: int,
    max_plans: int,
    max_branch: int,
    beam_width: int,
) -> list[OrchestrationPlan]:
    initial_available = frozenset(artifact.key for artifact in grounded.available_artifacts)
    goal_ids = goal_skill_ids(
        artifacts=artifacts,
        goal_terms=grounded.goal_terms,
        max_goal_skills=max(max_plans * 2, beam_width),
    )
    frontier = select_beam_states(
        [
            SearchState(skill_ids=(skill_id_,), available=initial_available, edges=())
            for skill_id_ in goal_ids
        ],
        grounded=grounded,
        skill_by_id=skill_by_id,
        can_feed_edges=can_feed_edges,
        beam_width=beam_width,
    )
    plans: list[OrchestrationPlan] = []
    seen: set[tuple[tuple[str, ...], frozenset[tuple[str, str]]]] = set()

    while frontier and len(plans) < max_plans * 4:
        next_frontier: list[SearchState] = []
        for state in frontier:
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

            root_id = state.skill_ids[0]
            root = skill_by_id.get(root_id)
            if not root:
                continue
            root_missing = missing_inputs(
                root,
                state.available,
                inferred_inputs=grounded.inferred_inputs,
            )
            if not root_missing:
                continue

            previous_ids = previous_skill_ids(
                root_id=root_id,
                root_missing=root_missing,
                state=state,
                incoming_edges=incoming_edges,
                can_feed_edges=can_feed_edges,
                skill_by_id=skill_by_id,
                available=state.available,
                inferred_inputs=grounded.inferred_inputs,
                goal_terms=grounded.goal_terms,
            )
            for edge_index, previous_id in previous_ids[:max_branch]:
                if previous_id in state.skill_ids:
                    continue
                if previous_id not in skill_by_id:
                    continue
                next_frontier.append(
                    SearchState(
                        skill_ids=(previous_id, *state.skill_ids),
                        available=state.available,
                        edges=(edge_index, *state.edges),
                    )
                )
        frontier = select_beam_states(
            next_frontier,
            grounded=grounded,
            skill_by_id=skill_by_id,
            can_feed_edges=can_feed_edges,
            beam_width=beam_width,
        )

    return dedupe_plans(plans)[: max_plans * 2]


def can_reach_goal_relevant_skill(
    *,
    last_id: str,
    outgoing_edges: dict[str, list[int]],
    can_feed_edges: list[dict[str, Any]],
    skill_by_id: dict[str, dict[str, Any]],
    goal_terms: set[str],
    max_hops: int,
) -> bool:
    if max_hops <= 0:
        return False
    queue = deque([(last_id, 0)])
    seen = {last_id}
    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_hops:
            continue
        for edge_index in outgoing_edges.get(current_id, []):
            edge = can_feed_edges[edge_index]
            target_id = skill_id(edge.get("target"))
            if target_id in seen:
                continue
            seen.add(target_id)
            target = skill_by_id.get(target_id)
            if not target:
                continue
            if skill_goal_score(target, goal_terms) > 0:
                return True
            queue.append((target_id, depth + 1))
    return False


def select_beam_states(
    states: list[SearchState],
    *,
    grounded: GroundedQuery,
    skill_by_id: dict[str, dict[str, Any]],
    can_feed_edges: list[dict[str, Any]],
    beam_width: int,
) -> list[SearchState]:
    unique: dict[tuple[tuple[str, ...], frozenset[tuple[str, str]]], SearchState] = {}
    for state in states:
        unique.setdefault((state.skill_ids, state.available), state)
    return [
        state
        for _, state in sorted(
            (
                (
                    beam_state_sort_key(
                        state,
                        grounded=grounded,
                        skill_by_id=skill_by_id,
                        can_feed_edges=can_feed_edges,
                    ),
                    state,
                )
                for state in unique.values()
            ),
            key=lambda item: item[0],
        )[: max(1, beam_width)]
    ]


def beam_state_sort_key(
    state: SearchState,
    *,
    grounded: GroundedQuery,
    skill_by_id: dict[str, dict[str, Any]],
    can_feed_edges: list[dict[str, Any]],
) -> tuple[float, int, float, int, tuple[str, ...]]:
    plan = state_to_plan(
        state=state,
        grounded=grounded,
        skill_by_id=skill_by_id,
        can_feed_edges=can_feed_edges,
    )
    missing_count = len(plan.missing_inputs)
    beam_score = (
        float(plan.goal_score)
        + float(plan.edge_confidence)
        - missing_count * BEAM_MISSING_INPUT_PENALTY
    )
    return (
        -beam_score,
        missing_count,
        -float(plan.edge_confidence),
        len(plan.steps),
        state.skill_ids,
    )


def entry_skill_ids(
    *,
    artifacts: BuildArtifacts,
    available: frozenset[tuple[str, str]],
    inferred_inputs: list[InferredInput],
    goal_terms: set[str],
    max_entry_skills: int,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    for skill in artifacts.skills:
        current_skill_id = skill.get("id", "")
        if not current_skill_id:
            continue
        input_score = input_coverage_score(
            skill,
            available,
            inferred_inputs=inferred_inputs,
        )
        goal_score = skill_goal_score(skill, goal_terms)
        if input_score <= 0 and goal_score <= 0:
            continue
        scored.append((input_score * 4.0 + goal_score, current_skill_id))
    return [
        current_skill_id
        for _, current_skill_id in sorted(scored, key=lambda item: (-item[0], item[1]))
    ][:max_entry_skills]


def goal_skill_ids(
    *,
    artifacts: BuildArtifacts,
    goal_terms: set[str],
    max_goal_skills: int,
) -> list[str]:
    scored = []
    for skill in artifacts.skills:
        current_skill_id = str(skill.get("id") or "")
        if not current_skill_id:
            continue
        score = skill_goal_score(skill, goal_terms)
        if score <= 0:
            continue
        scored.append((score, current_skill_id))
    return [
        current_skill_id
        for _, current_skill_id in sorted(scored, key=lambda item: (-item[0], item[1]))
    ][:max_goal_skills]


def next_skill_ids(
    *,
    last_id: str,
    state: SearchState,
    inferred_inputs: list[InferredInput],
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
            + input_coverage_score(
                target,
                state.available,
                inferred_inputs=inferred_inputs,
            )
            * 2.0
            + skill_goal_score(target, goal_terms)
        )
        candidates.append((score, edge_index, target_id))
    return [
        (edge_index, target_id)
        for _, edge_index, target_id in sorted(candidates, key=lambda item: (-item[0], item[2]))
    ]


def previous_skill_ids(
    *,
    root_id: str,
    root_missing: list[dict[str, Any]],
    state: SearchState,
    incoming_edges: dict[str, list[int]],
    can_feed_edges: list[dict[str, Any]],
    skill_by_id: dict[str, dict[str, Any]],
    available: Iterable[tuple[str, str]],
    inferred_inputs: list[InferredInput],
    goal_terms: set[str],
) -> list[tuple[int, str]]:
    candidates: list[tuple[float, int, str]] = []
    missing_keys = {
        (str(item.get("name") or ""), str(item.get("type") or "unknown"))
        for item in root_missing
        if item.get("name")
    }
    for edge_index in incoming_edges.get(root_id, []):
        edge = can_feed_edges[edge_index]
        source_id = skill_id(edge.get("source"))
        if source_id in state.skill_ids:
            continue
        source = skill_by_id.get(source_id)
        target = skill_by_id.get(root_id)
        if not source or not target:
            continue
        if not edge_feeds_missing_inputs(
            edge=edge,
            source=source,
            target=target,
            missing_keys=missing_keys,
        ):
            continue
        score = (
            float(edge.get("confidence") or 0.0) * 4.0
            + input_coverage_score(
                source,
                available,
                inferred_inputs=inferred_inputs,
            )
            * 2.0
            + skill_goal_score(source, goal_terms)
        )
        candidates.append((score, edge_index, source_id))
    return [
        (edge_index, source_id)
        for _, edge_index, source_id in sorted(candidates, key=lambda item: (-item[0], item[2]))
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
        filled = filled_inputs(skill, grounded.inferred_inputs)
        missing = missing_inputs(
            skill,
            available,
            inferred_inputs=grounded.inferred_inputs,
        )
        all_missing.extend({**item, "skill_id": current_skill_id} for item in missing)
        outputs = output_keys(skill)
        produced.update(outputs)
        available.update(outputs)
        steps.append(
            PlanStep(
                skill_id=current_skill_id,
                name=skill.get("name", current_skill_id),
                inputs=[
                    {"name": item.get("name"), "type": item.get("type")}
                    for item in skill.get("inputs", [])
                ],
                outputs=[
                    {"name": item.get("name"), "type": item.get("type")}
                    for item in skill.get("outputs", [])
                ],
                missing_inputs=missing,
                filled_inputs=filled,
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


def build_incoming_edges(edges: list[dict[str, Any]]) -> dict[str, list[int]]:
    incoming: dict[str, list[int]] = defaultdict(list)
    for index, edge in enumerate(edges):
        incoming[skill_id(edge.get("target"))].append(index)
    return incoming


def input_coverage_score(
    skill: dict[str, Any],
    available: Iterable[tuple[str, str]],
    *,
    inferred_inputs: list[InferredInput] | None = None,
) -> float:
    required = [
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("inputs", [])
        if item.get("required", True) and item.get("name")
    ]
    if not required:
        return 1.0
    available_set = set(available)
    inferred_set = inferred_input_keys(str(skill.get("id") or ""), inferred_inputs or [])
    matched = sum(
        1
        for item in required
        if artifact_matches(item, available_set)
        or inferred_input_matches(item, inferred_set)
    )
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
    *,
    inferred_inputs: list[InferredInput] | None = None,
) -> list[dict[str, Any]]:
    available_set = set(available)
    inferred_set = inferred_input_keys(str(skill.get("id") or ""), inferred_inputs or [])
    missing = []
    for item in skill.get("inputs", []):
        if not item.get("required", True):
            continue
        expected = (str(item.get("name")), str(item.get("type") or "unknown"))
        if not artifact_matches(expected, available_set) and not inferred_input_matches(
            expected,
            inferred_set,
        ):
            missing.append({"name": expected[0], "type": expected[1]})
    return missing


def filled_inputs(
    skill: dict[str, Any],
    inferred_inputs: list[InferredInput],
) -> list[dict[str, Any]]:
    skill_id_ = str(skill.get("id") or "")
    input_keys = {
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("inputs", [])
        if item.get("name")
    }
    filled = []
    for inferred_input in inferred_inputs:
        if inferred_input.skill_id != skill_id_:
            continue
        if inferred_input_matches((inferred_input.name, inferred_input.type), input_keys):
            filled.append(inferred_input.to_dict())
    return filled


def inferred_input_keys(
    skill_id_: str,
    inferred_inputs: list[InferredInput],
) -> set[tuple[str, str]]:
    return {
        (item.name, item.type)
        for item in inferred_inputs
        if item.skill_id == skill_id_
    }


def inferred_input_matches(
    expected: tuple[str, str],
    inferred: set[tuple[str, str]],
) -> bool:
    name, type_ = expected
    return any(
        candidate_name == name
        and (candidate_type == type_ or candidate_type == "unknown" or type_ == "unknown")
        for candidate_name, candidate_type in inferred
    )


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


def edge_feeds_missing_inputs(
    *,
    edge: dict[str, Any],
    source: dict[str, Any],
    target: dict[str, Any],
    missing_keys: set[tuple[str, str]],
) -> bool:
    if not missing_keys:
        return False
    evidence = edge.get("evidence") or {}
    supporting_fields = evidence.get("supporting_fields") or {}
    target_input_names = {
        str(item)
        for item in supporting_fields.get("target_inputs", [])
        if str(item).strip()
    }
    target_input_names.update(
        str(mapping.get("target_input"))
        for mapping in supporting_fields.get("port_mappings", [])
        if isinstance(mapping, dict) and mapping.get("target_input")
    )
    missing_names = {name for name, _ in missing_keys}
    if target_input_names & missing_names:
        return True

    source_outputs = output_keys(source)
    target_inputs = {
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in target.get("inputs", [])
        if item.get("name")
    }
    return any(
        missing_key in target_inputs
        and artifact_matches(missing_key, set(source_outputs))
        for missing_key in missing_keys
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
    output_terms = set()
    for output in skill.get("outputs", []):
        output_terms.update(tokenize(output.get("name", "")))
        output_terms.update(tokenize(output.get("description", "")))
    return (
        len(terms & goal_terms)
        + len(output_terms & goal_terms) * 2.0
    )


def skill_terms(skill: dict[str, Any]) -> set[str]:
    chunks = [
        skill.get("id", ""),
        skill.get("name", ""),
        skill.get("description", ""),
    ]
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
        "port_mappings": supporting_fields.get("port_mappings", [])[:5],
        "source_outputs": supporting_fields.get("source_outputs", [])[:3],
        "target_inputs": supporting_fields.get("target_inputs", [])[:3],
        "reasons": evidence.get("reasons", [])[:3],
    }
