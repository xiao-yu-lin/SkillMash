"""Orchestration facade built on planning helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.orchestration.planning.grounding import ground_query
from skillmash.orchestration.planning.models import GroundedQuery, GroundingClient, PlanningConfig
from skillmash.orchestration.planning.search import (
    artifact_matches,
    build_outgoing_edges,
    plan_stages,
    search_plans,
)
from skillmash.orchestration.planning.utils import clamp
from skillmash.reranking import PlanReranker
from skillmash.representation.llm import LLMConfig, create_llm_client


class PlanRanker(Protocol):
    """Minimal ranking interface used by orchestration."""

    def rerank(
        self,
        planning_result: dict[str, Any],
        *,
        top_k: int = 3,
        top_m: int = 12,
        include_candidates: bool = True,
    ) -> dict[str, Any]:
        ...


class SkillOrchestrator:
    """Build candidate plans from user inputs, intent, and can_feed edges."""

    def __init__(
        self,
        artifacts: BuildArtifacts,
        *,
        planning_config: PlanningConfig | None = None,
        llm_config: LLMConfig | None = None,
        llm_client: GroundingClient | None = None,
        ranker: PlanRanker | None = None,
        min_edge_confidence: float | None = None,
        max_depth: int | None = None,
        max_plans: int | None = None,
        max_branch: int | None = None,
        top_m: int | None = None,
        top_k: int | None = None,
        include_candidates: bool | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.manifest_defaults = _planning_defaults_from_manifest(artifacts.manifest)
        base_config = planning_config or self.manifest_defaults
        self.config = _resolve_config(
            base_config,
            min_edge_confidence=min_edge_confidence,
            max_depth=max_depth,
            max_plans=max_plans,
            max_branch=max_branch,
            top_m=top_m,
            top_k=top_k,
            include_candidates=include_candidates,
        )

        self.min_edge_confidence = clamp(self.config.min_edge_confidence)
        self.max_depth = max(1, self.config.max_depth)
        self.max_plans = max(1, self.config.max_plans)
        self.max_branch = max(1, self.config.max_branch)
        self.skill_by_id = artifacts.skill_by_id
        self.llm_config = llm_config
        if llm_client is not None:
            self.llm_client = llm_client
        elif llm_config is not None:
            self.llm_client = create_llm_client(llm_config)
        else:
            raise ValueError("SkillOrchestrator requires llm_config or llm_client.")
        if ranker is not None:
            self.ranker = ranker
        elif llm_config is not None:
            self.ranker = PlanReranker(llm_config=llm_config)
        else:
            self.ranker = PlanReranker(llm_client=self.llm_client)

        self.all_can_feed_edges = [
            edge
            for edge in artifacts.graph.get("edges", [])
            if edge.get("type") == "can_feed"
        ]
        self.all_relation_edges = list(artifacts.graph.get("edges", []))

    def plan(
        self,
        query: str,
        *,
        planning_config: PlanningConfig | None = None,
    ) -> dict[str, Any]:
        config = _resolve_config(
            self.config,
            min_edge_confidence=planning_config.min_edge_confidence
            if planning_config
            else None,
            max_depth=planning_config.max_depth if planning_config else None,
            max_plans=planning_config.max_plans if planning_config else None,
            max_branch=planning_config.max_branch if planning_config else None,
            top_m=planning_config.top_m if planning_config else None,
            top_k=planning_config.top_k if planning_config else None,
            include_candidates=planning_config.include_candidates
            if planning_config
            else None,
        )
        grounded = self.ground_query(query)
        can_feed_edges = [
            edge
            for edge in self.all_can_feed_edges
            if float(edge.get("confidence") or 0.0)
            >= clamp(config.min_edge_confidence)
        ]
        outgoing_edges = build_outgoing_edges(can_feed_edges)
        plans = search_plans(
            artifacts=self.artifacts,
            skill_by_id=self.skill_by_id,
            can_feed_edges=can_feed_edges,
            outgoing_edges=outgoing_edges,
            grounded=grounded,
            max_depth=max(1, config.max_depth),
            max_plans=max(1, config.max_plans),
            max_branch=max(1, config.max_branch),
        )
        candidate_plans = sorted(
            (plan.to_dict() for plan in plans),
            key=lambda plan: (
                plan.get("status") != "ready",
                len(plan.get("missing_inputs") or []),
                -int(plan.get("consumed_user_artifacts") or 0),
                len(plan.get("steps") or []),
                -float(plan.get("goal_score") or 0.0),
                -float(plan.get("edge_confidence") or 0.0),
            ),
        )[: max(1, config.max_plans)]
        candidate_plans = [
            _optimize_plan_slots(
                plan,
                skill_by_id=self.skill_by_id,
                graph_edges=self.all_relation_edges,
                grounded=grounded,
                feedback_path=config.relation_feedback_path,
            )
            for plan in candidate_plans
        ]
        result = {
            "query": query,
            "build_dir": str(self.artifacts.build_dir),
            "grounded_query": grounded.to_dict(),
            "plans": candidate_plans,
        }
        return self.ranker.rerank(
            result,
            top_k=max(1, config.top_k),
            top_m=max(1, config.top_m),
            include_candidates=bool(config.include_candidates),
        )

    def ground_query(self, query: str) -> GroundedQuery:
        return ground_query(
            query=query,
            artifacts=self.artifacts,
            llm_client=self.llm_client,
        )


def _resolve_config(
    base: PlanningConfig | None,
    *,
    min_edge_confidence: float | None,
    max_depth: int | None,
    max_plans: int | None,
    max_branch: int | None,
    top_m: int | None,
    top_k: int | None,
    include_candidates: bool | None,
) -> PlanningConfig:
    base_config = base or PlanningConfig()
    return PlanningConfig(
        min_edge_confidence=(
            float(min_edge_confidence)
            if min_edge_confidence is not None
            else base_config.min_edge_confidence
        ),
        max_depth=int(max_depth) if max_depth is not None else base_config.max_depth,
        max_plans=int(max_plans) if max_plans is not None else base_config.max_plans,
        max_branch=int(max_branch)
        if max_branch is not None
        else base_config.max_branch,
        top_m=int(top_m) if top_m is not None else base_config.top_m,
        top_k=int(top_k) if top_k is not None else base_config.top_k,
        include_candidates=(
            bool(include_candidates)
            if include_candidates is not None
            else base_config.include_candidates
        ),
        relation_feedback_path=base_config.relation_feedback_path,
        relation_feedback_window_days=base_config.relation_feedback_window_days,
    )


def _planning_defaults_from_manifest(manifest: dict[str, Any]) -> PlanningConfig:
    defaults = manifest.get("planning_defaults") or {}
    if not isinstance(defaults, dict):
        defaults = {}
    return PlanningConfig(
        min_edge_confidence=float(
            defaults.get("min_edge_confidence", PlanningConfig.min_edge_confidence)
        ),
        max_depth=int(defaults.get("max_depth", PlanningConfig.max_depth)),
        max_plans=int(defaults.get("max_plans", PlanningConfig.max_plans)),
        max_branch=int(defaults.get("max_branch", PlanningConfig.max_branch)),
        top_m=int(defaults.get("top_m", PlanningConfig.top_m)),
        top_k=int(defaults.get("top_k", PlanningConfig.top_k)),
        include_candidates=bool(
            defaults.get("include_candidates", PlanningConfig.include_candidates)
        ),
        relation_feedback_path=str(
            defaults.get(
                "relation_feedback_path", PlanningConfig.relation_feedback_path
            )
        ),
        relation_feedback_window_days=int(
            defaults.get(
                "relation_feedback_window_days",
                PlanningConfig.relation_feedback_window_days,
            )
        ),
    )


def _optimize_plan_slots(
    plan: dict[str, Any],
    *,
    skill_by_id: dict[str, dict[str, Any]],
    graph_edges: list[dict[str, Any]],
    grounded: GroundedQuery,
    feedback_path: str,
) -> dict[str, Any]:
    steps = [dict(step) for step in plan.get("steps", [])]
    if not steps:
        return plan
    relation_views = _relation_views(graph_edges)
    available = {
        artifact.key for artifact in grounded.available_artifacts
    }
    selected_ids = [str(step.get("skill_id") or "") for step in steps]
    slot_candidates: list[dict[str, Any]] = []
    replacement_log: list[str] = []

    for index, step in enumerate(steps):
        original_id = str(step.get("skill_id") or "")
        if not original_id or original_id not in skill_by_id:
            continue
        contract = _slot_contract(skill_by_id[original_id], index)
        substitutes = relation_views["substitute_by_target"].get(original_id, [])
        similars = relation_views["similar_by_skill"].get(original_id, [])
        seen_candidate_ids: set[str] = set()
        candidates: list[tuple[str, str]] = []
        for candidate_id, relation_type in substitutes + similars:
            if candidate_id == original_id or candidate_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate_id)
            candidates.append((candidate_id, relation_type))
        slot_candidates.append(
            {
                "slot_index": index + 1,
                "original_skill_id": original_id,
                "contract": contract,
                "substitute_candidates": substitutes,
                "similar_candidates": similars,
            }
        )
        for candidate_id, relation_type in candidates:
            candidate_skill = skill_by_id.get(candidate_id)
            if candidate_skill is None:
                continue
            if not _contract_compatible(contract, candidate_skill):
                _record_relation_feedback(
                    feedback_path,
                    source_skill=candidate_id,
                    target_skill=original_id,
                    relation_type=relation_type,
                    slot_io_signature=contract["io_signature"],
                    reason_code="slot_incompatible_signature",
                )
                continue
            tentative_ids = list(selected_ids)
            tentative_ids[index] = candidate_id
            if not _plan_chain_closed(
                tentative_ids,
                skill_by_id=skill_by_id,
                available=available,
            ):
                _record_relation_feedback(
                    feedback_path,
                    source_skill=candidate_id,
                    target_skill=original_id,
                    relation_type=relation_type,
                    slot_io_signature=contract["io_signature"],
                    reason_code="slot_no_viable_substitute",
                )
                continue
            selected_ids[index] = candidate_id
            replacement_log.append(
                f"slot {index + 1}: {original_id} -> {candidate_id} ({relation_type})"
            )
            break

    remapped_steps = [_step_from_skill_id(skill_by_id, skill_id_) for skill_id_ in selected_ids]
    missing_inputs = _plan_missing_inputs(remapped_steps, skill_by_id, available)
    remapped_edges = _remap_plan_edges(
        plan.get("can_feed_edges", []),
        old_ids=[str(step.get("skill_id") or "") for step in steps],
        new_ids=selected_ids,
        skill_by_id=skill_by_id,
    )
    updated = dict(plan)
    updated["steps"] = remapped_steps
    updated["slot_candidates"] = slot_candidates
    if replacement_log:
        updated["reasons"] = [*list(plan.get("reasons") or []), *replacement_log][:12]
    updated["missing_inputs"] = missing_inputs
    updated["status"] = "ready" if not missing_inputs else "needs_input"
    updated["can_feed_edges"] = remapped_edges
    updated["stages"] = plan_stages(
        [
            replace_step_for_stage(step)
            for step in remapped_steps
        ],
        remapped_edges,
    )
    return updated


def replace_step_for_stage(step: dict[str, Any]):
    class _Step:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.skill_id = str(payload.get("skill_id") or "")
            self.name = str(payload.get("name") or self.skill_id)
            self.tasks = list(payload.get("tasks") or [])
            self.inputs = list(payload.get("inputs") or [])
            self.outputs = list(payload.get("outputs") or [])
            self.missing_inputs = list(payload.get("missing_inputs") or [])

        def to_dict(self) -> dict[str, Any]:
            return {
                "skill_id": self.skill_id,
                "name": self.name,
                "tasks": self.tasks,
                "inputs": self.inputs,
                "outputs": self.outputs,
                "missing_inputs": self.missing_inputs,
            }

    return _Step(step)


def _relation_views(graph_edges: list[dict[str, Any]]) -> dict[str, Any]:
    substitute_by_target: dict[str, list[tuple[str, str]]] = {}
    similar_by_skill: dict[str, list[tuple[str, str]]] = {}
    for edge in graph_edges:
        source = _edge_skill_id(edge.get("source"))
        target = _edge_skill_id(edge.get("target"))
        edge_type = str(edge.get("type") or "")
        if not source or not target:
            continue
        if edge_type == "substitute_for":
            substitute_by_target.setdefault(target, []).append(
                (source, "substitute_for")
            )
        elif edge_type == "similar_to":
            similar_by_skill.setdefault(source, []).append((target, "similar_to"))
            similar_by_skill.setdefault(target, []).append((source, "similar_to"))
    for key in list(substitute_by_target):
        substitute_by_target[key] = sorted(set(substitute_by_target[key]))
    for key in list(similar_by_skill):
        similar_by_skill[key] = sorted(set(similar_by_skill[key]))
    return {
        "substitute_by_target": substitute_by_target,
        "similar_by_skill": similar_by_skill,
    }


def _slot_contract(skill: dict[str, Any], index: int) -> dict[str, Any]:
    required_inputs = sorted(
        {
            (str(item.get("name") or ""), str(item.get("type") or "unknown"))
            for item in skill.get("inputs", [])
            if item.get("required", True) and item.get("name")
        }
    )
    outputs = sorted(
        {
            (str(item.get("name") or ""), str(item.get("type") or "unknown"))
            for item in skill.get("outputs", [])
            if item.get("name")
        }
    )
    return {
        "slot_index": index + 1,
        "io_signature": {
            "required_inputs": required_inputs,
            "outputs": outputs,
        },
    }


def _contract_compatible(contract: dict[str, Any], candidate_skill: dict[str, Any]) -> bool:
    signature = contract.get("io_signature") or {}
    expected_inputs = set(
        (name, type_) for name, type_ in signature.get("required_inputs", [])
    )
    expected_outputs = set(
        (name, type_) for name, type_ in signature.get("outputs", [])
    )
    candidate_inputs = {
        (str(item.get("name") or ""), str(item.get("type") or "unknown"))
        for item in candidate_skill.get("inputs", [])
        if item.get("required", True) and item.get("name")
    }
    candidate_outputs = {
        (str(item.get("name") or ""), str(item.get("type") or "unknown"))
        for item in candidate_skill.get("outputs", [])
        if item.get("name")
    }
    return candidate_inputs == expected_inputs and expected_outputs <= candidate_outputs


def _plan_chain_closed(
    skill_ids: list[str],
    *,
    skill_by_id: dict[str, dict[str, Any]],
    available: set[tuple[str, str]],
) -> bool:
    cursor = set(available)
    for skill_id_ in skill_ids:
        skill = skill_by_id.get(skill_id_)
        if skill is None:
            return False
        for item in skill.get("inputs", []):
            if not item.get("required", True):
                continue
            expected = (str(item.get("name") or ""), str(item.get("type") or "unknown"))
            if not expected[0]:
                continue
            if not artifact_matches(expected, cursor):
                return False
        for output in skill.get("outputs", []):
            name = str(output.get("name") or "")
            if not name:
                continue
            cursor.add((name, str(output.get("type") or "unknown")))
    return True


def _step_from_skill_id(skill_by_id: dict[str, dict[str, Any]], skill_id_: str) -> dict[str, Any]:
    skill = skill_by_id.get(skill_id_, {})
    return {
        "skill_id": skill_id_,
        "name": skill.get("name", skill_id_),
        "tasks": list(skill.get("tasks", [])),
        "inputs": [
            {"name": item.get("name"), "type": item.get("type")}
            for item in skill.get("inputs", [])
        ],
        "outputs": [
            {"name": item.get("name"), "type": item.get("type")}
            for item in skill.get("outputs", [])
        ],
        "missing_inputs": [],
    }


def _plan_missing_inputs(
    steps: list[dict[str, Any]],
    skill_by_id: dict[str, dict[str, Any]],
    available: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    cursor = set(available)
    for step in steps:
        skill_id_ = str(step.get("skill_id") or "")
        skill = skill_by_id.get(skill_id_, {})
        step_missing: list[dict[str, Any]] = []
        for item in skill.get("inputs", []):
            if not item.get("required", True):
                continue
            expected = (str(item.get("name") or ""), str(item.get("type") or "unknown"))
            if not expected[0]:
                continue
            if not artifact_matches(expected, cursor):
                step_missing.append({"name": expected[0], "type": expected[1]})
                missing.append(
                    {"skill_id": skill_id_, "name": expected[0], "type": expected[1]}
                )
        step["missing_inputs"] = step_missing
        for output in skill.get("outputs", []):
            name = str(output.get("name") or "")
            if name:
                cursor.add((name, str(output.get("type") or "unknown")))
    return missing


def _remap_plan_edges(
    edges: list[dict[str, Any]],
    *,
    old_ids: list[str],
    new_ids: list[str],
    skill_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    mapping = dict(zip(old_ids, new_ids))
    remapped = []
    for edge in edges:
        source = mapping.get(str(edge.get("source_id") or ""), str(edge.get("source_id") or ""))
        target = mapping.get(str(edge.get("target_id") or ""), str(edge.get("target_id") or ""))
        item = dict(edge)
        item["source_id"] = source
        item["target_id"] = target
        remapped.append(item)
    if remapped:
        return remapped
    generated = []
    for index in range(len(new_ids) - 1):
        source = new_ids[index]
        target = new_ids[index + 1]
        generated.append(
            {
                "source_id": source,
                "target_id": target,
                "confidence": 1.0,
                "method": "slot_replacement_chain",
                "source_outputs": [
                    item.get("name")
                    for item in skill_by_id.get(source, {}).get("outputs", [])
                    if item.get("name")
                ][:3],
                "target_inputs": [
                    item.get("name")
                    for item in skill_by_id.get(target, {}).get("inputs", [])
                    if item.get("name")
                ][:3],
                "reasons": ["derived edge for replacement chain"],
            }
        )
    return generated


def _record_relation_feedback(
    feedback_path: str,
    *,
    source_skill: str,
    target_skill: str,
    relation_type: str,
    slot_io_signature: dict[str, Any],
    reason_code: str,
) -> None:
    if not feedback_path:
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_skill": source_skill,
        "target_skill": target_skill,
        "relation_type": relation_type,
        "slot_io_signature": slot_io_signature,
        "reason_code": reason_code,
        "count": 1,
        "total_count": 1,
    }
    path = Path(feedback_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _edge_skill_id(value: Any) -> str:
    text = str(value or "")
    return text.removeprefix("skill:")
