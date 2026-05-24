"""Orchestration facade built on planning helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.orchestration.planning.grounding import ground_query
from skillmash.orchestration.planning.models import GroundedQuery, GroundingClient, PlanningConfig
from skillmash.orchestration.planning.search import (
    artifact_matches,
    build_outgoing_edges,
    missing_inputs,
    output_keys,
    plan_stages,
    search_plans,
    skill_goal_score,
)
from skillmash.orchestration.planning.slot_grouping import build_slot_groups
from skillmash.orchestration.planning.utils import clamp
from skillmash.orchestration.strategy import ReliabilityFirstStrategy
from skillmash.orchestration.strategy.interfaces import PruneContext
from skillmash.orchestration.validation import default_policy, hard_filter_plans
from skillmash.reranking import PlanReranker
from skillmash.common.llm import LLMConfig, create_llm_client

logger = logging.getLogger(__name__)


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
        max_entry_skills: int | None = None,
        top_m: int | None = None,
        top_k: int | None = None,
        include_candidates: bool | None = None,
        conservative_reject: bool | None = None,
        hard_fail_missing_inputs: bool | None = None,
        allow_similar_slot_substitute: bool | None = None,
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
            max_entry_skills=max_entry_skills,
            top_m=top_m,
            top_k=top_k,
            include_candidates=include_candidates,
            conservative_reject=conservative_reject,
            hard_fail_missing_inputs=hard_fail_missing_inputs,
            allow_similar_slot_substitute=allow_similar_slot_substitute,
        )

        self.min_edge_confidence = clamp(self.config.min_edge_confidence)
        self.max_depth = max(1, self.config.max_depth)
        self.max_plans = max(1, self.config.max_plans)
        self.max_branch = max(1, self.config.max_branch)
        self.max_entry_skills = max(1, self.config.max_entry_skills)
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
            max_entry_skills=planning_config.max_entry_skills
            if planning_config
            else None,
            top_m=planning_config.top_m if planning_config else None,
            top_k=planning_config.top_k if planning_config else None,
            include_candidates=planning_config.include_candidates
            if planning_config
            else None,
            conservative_reject=planning_config.conservative_reject
            if planning_config
            else None,
            hard_fail_missing_inputs=planning_config.hard_fail_missing_inputs
            if planning_config
            else None,
            allow_similar_slot_substitute=planning_config.allow_similar_slot_substitute
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
        logger.debug(
            "plan start: query=%r min_edge_confidence=%.3f can_feed_edges=%s",
            query,
            clamp(config.min_edge_confidence),
            len(can_feed_edges),
        )
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
            max_entry_skills=max(1, config.max_entry_skills),
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
        logger.debug(
            "search plans: raw=%s sliced=%s",
            len(plans),
            len(candidate_plans),
        )
        candidate_plans = _append_mixed_graph_candidates(
            candidate_plans,
            artifacts=self.artifacts,
            skill_by_id=self.skill_by_id,
            grounded=grounded,
            max_depth=max(1, config.max_depth),
            max_plans=max(1, config.max_plans),
        )

        candidate_plans = build_slot_groups(
            candidate_plans,
            self.all_relation_edges,
            allow_similar=config.allow_similar_slot_substitute,
        )
        candidate_plans = [
            _optimize_plan_slots(
                plan,
                skill_by_id=self.skill_by_id,
                graph_edges=self.all_relation_edges,
                can_feed_edges=self.all_can_feed_edges,
                grounded=grounded,
                feedback_path=config.relation_feedback_path,
                min_edge_confidence=clamp(config.min_edge_confidence),
                allow_similar=config.allow_similar_slot_substitute,
            )
            for plan in candidate_plans
        ]
        logger.debug("post optimization plans=%s", len(candidate_plans))
        candidate_plans = [
            _annotate_plan_execution_feasibility(
                plan,
                slot_contracts=self.artifacts.slot_contracts or {},
            )
            for plan in candidate_plans
        ]

        policy = default_policy()
        policy["min_edge_confidence"] = clamp(config.min_edge_confidence)
        policy["hard_fail_missing_required_input"] = bool(
            config.hard_fail_missing_inputs
        )
        validated_plans, fail_counts, plan_fail_reasons = hard_filter_plans(
            candidate_plans,
            policy=policy,
        )
        ranking_pool = _augment_with_structural_incomplete_plans(
            validated_plans,
            candidate_plans,
            query=query,
            grounded_query=grounded.to_dict(),
            top_k=max(1, config.top_k),
        )
        logger.debug(
            "hard filter: candidate=%s validated=%s ranking_pool=%s fail_counts=%s",
            len(candidate_plans),
            len(validated_plans),
            len(ranking_pool),
            fail_counts,
        )

        if not ranking_pool and config.conservative_reject:
            return {
                "query": query,
                "build_dir": str(self.artifacts.build_dir),
                "grounded_query": grounded.to_dict(),
                "plans": candidate_plans if config.include_candidates else [],
                "recommended_plans": [],
                "ranking_mode": "conservative_reject",
                "decision": {
                    "mode": "conservative_reject",
                    "strategy": "reliability_first",
                    "fail_code_counts": fail_counts,
                    "plan_fail_reasons": plan_fail_reasons,
                    "validated_count": 0,
                },
            }

        strategy = ReliabilityFirstStrategy()
        context = PruneContext(
            query=query,
            grounded_query=grounded.to_dict(),
            policy=policy,
            runtime_constraints={},
        )
        ranked_validated = sorted(
            ranking_pool,
            key=lambda plan: (
                -strategy.rank_score(plan, context),
                len(plan.get("steps") or []),
                -float(plan.get("goal_score") or 0.0),
            ),
        )[: max(1, config.max_plans)]

        for plan in ranked_validated:
            plan["strategy_score"] = round(strategy.rank_score(plan, context), 6)

        result = {
            "query": query,
            "build_dir": str(self.artifacts.build_dir),
            "grounded_query": grounded.to_dict(),
            "plans": ranked_validated,
        }
        reranked = self.ranker.rerank(
            result,
            top_k=max(1, config.top_k),
            top_m=max(1, config.top_m),
            include_candidates=bool(config.include_candidates),
        )
        reranked["decision"] = {
            "mode": "validated",
            "strategy": strategy.name,
            "validated_count": len(validated_plans),
            "ranking_pool_count": len(ranked_validated),
            "candidate_count": len(candidate_plans),
            "fail_code_counts": fail_counts,
        }
        logger.debug(
            "rerank result: mode=%s recommended=%s",
            reranked.get("ranking_mode"),
            len(reranked.get("recommended_plans", [])),
        )
        return reranked

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
    max_entry_skills: int | None,
    top_m: int | None,
    top_k: int | None,
    include_candidates: bool | None,
    conservative_reject: bool | None,
    hard_fail_missing_inputs: bool | None,
    allow_similar_slot_substitute: bool | None,
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
        max_entry_skills=int(max_entry_skills)
        if max_entry_skills is not None
        else base_config.max_entry_skills,
        top_m=int(top_m) if top_m is not None else base_config.top_m,
        top_k=int(top_k) if top_k is not None else base_config.top_k,
        include_candidates=(
            bool(include_candidates)
            if include_candidates is not None
            else base_config.include_candidates
        ),
        conservative_reject=(
            bool(conservative_reject)
            if conservative_reject is not None
            else base_config.conservative_reject
        ),
        hard_fail_missing_inputs=(
            bool(hard_fail_missing_inputs)
            if hard_fail_missing_inputs is not None
            else base_config.hard_fail_missing_inputs
        ),
        allow_similar_slot_substitute=(
            bool(allow_similar_slot_substitute)
            if allow_similar_slot_substitute is not None
            else base_config.allow_similar_slot_substitute
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
        max_entry_skills=int(
            defaults.get("max_entry_skills", PlanningConfig.max_entry_skills)
        ),
        top_m=int(defaults.get("top_m", PlanningConfig.top_m)),
        top_k=int(defaults.get("top_k", PlanningConfig.top_k)),
        include_candidates=bool(
            defaults.get("include_candidates", PlanningConfig.include_candidates)
        ),
        conservative_reject=bool(
            defaults.get("conservative_reject", PlanningConfig.conservative_reject)
        ),
        hard_fail_missing_inputs=bool(
            defaults.get(
                "hard_fail_missing_inputs",
                PlanningConfig.hard_fail_missing_inputs,
            )
        ),
        allow_similar_slot_substitute=bool(
            defaults.get(
                "allow_similar_slot_substitute",
                PlanningConfig.allow_similar_slot_substitute,
            )
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
    can_feed_edges: list[dict[str, Any]],
    grounded: GroundedQuery,
    feedback_path: str,
    min_edge_confidence: float,
    allow_similar: bool,
) -> dict[str, Any]:
    steps = [dict(step) for step in plan.get("steps", [])]
    if not steps:
        return plan
    relation_views = _relation_views(graph_edges, allow_similar=allow_similar)
    available = {artifact.key for artifact in grounded.available_artifacts}
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
            if not _plan_chain_has_explicit_edges(
                tentative_ids,
                can_feed_edges=can_feed_edges,
                min_edge_confidence=min_edge_confidence,
            ):
                _record_relation_feedback(
                    feedback_path,
                    source_skill=candidate_id,
                    target_skill=original_id,
                    relation_type=relation_type,
                    slot_io_signature=contract["io_signature"],
                    reason_code="slot_no_explicit_adjacency",
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
        [replace_step_for_stage(step) for step in remapped_steps],
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


def _relation_views(
    graph_edges: list[dict[str, Any]],
    *,
    allow_similar: bool,
) -> dict[str, Any]:
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
        elif allow_similar and edge_type == "similar_to":
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


def _append_mixed_graph_candidates(
    candidate_plans: list[dict[str, Any]],
    *,
    artifacts: BuildArtifacts,
    skill_by_id: dict[str, dict[str, Any]],
    grounded: GroundedQuery,
    max_depth: int,
    max_plans: int,
) -> list[dict[str, Any]]:
    mixed_plans = _build_mixed_graph_plans(
        artifacts=artifacts,
        skill_by_id=skill_by_id,
        grounded=grounded,
        max_depth=max_depth,
        max_plans=max_plans,
    )
    by_signature: dict[tuple[str, ...], dict[str, Any]] = {}
    for plan in candidate_plans + mixed_plans:
        signature = tuple(
            str(step.get("skill_id") or "")
            for step in plan.get("steps", [])
            if step.get("skill_id")
        )
        if not signature:
            continue
        existing = by_signature.get(signature)
        if existing is None or float(plan.get("goal_score") or 0.0) > float(
            existing.get("goal_score") or 0.0
        ):
            by_signature[signature] = plan
    merged = sorted(
        by_signature.values(),
        key=lambda plan: (
            plan.get("status") != "ready",
            len(plan.get("missing_inputs") or []),
            -int(plan.get("consumed_user_artifacts") or 0),
            len(plan.get("steps") or []),
            -float(plan.get("goal_score") or 0.0),
            -float(plan.get("edge_confidence") or 0.0),
        ),
    )
    return merged[:max_plans]


def _build_mixed_graph_plans(
    *,
    artifacts: BuildArtifacts,
    skill_by_id: dict[str, dict[str, Any]],
    grounded: GroundedQuery,
    max_depth: int,
    max_plans: int,
) -> list[dict[str, Any]]:
    relation_edges = list(artifacts.graph.get("edges", []))
    slot_producers: dict[str, set[str]] = {}
    slot_consumers: dict[str, set[str]] = {}
    slot_aggregators: dict[str, set[str]] = {}
    artifact_producers: dict[tuple[str, str], set[str]] = {}
    depends_on_pairs: set[tuple[str, str]] = set()

    for edge in relation_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        edge_type = str(edge.get("type") or "")
        if edge_type == "produces" and source.startswith("skill:") and target.startswith("artifact:"):
            producer_id = source.removeprefix("skill:")
            artifact_key = _artifact_key_from_node_id(target)
            if artifact_key is not None:
                artifact_producers.setdefault(artifact_key, set()).add(producer_id)
        if edge_type == "produces" and source.startswith("skill:") and target.startswith("slot:"):
            slot_name = target.removeprefix("slot:")
            slot_producers.setdefault(slot_name, set()).add(source.removeprefix("skill:"))
        elif edge_type == "consumes" and source.startswith("slot:") and target.startswith("skill:"):
            slot_name = source.removeprefix("slot:")
            slot_consumers.setdefault(slot_name, set()).add(target.removeprefix("skill:"))
        elif edge_type == "aggregates" and source.startswith("slot:") and target.startswith("skill:"):
            slot_name = source.removeprefix("slot:")
            slot_aggregators.setdefault(slot_name, set()).add(target.removeprefix("skill:"))
        elif edge_type == "depends_on" and source.startswith("skill:") and target.startswith("skill:"):
            depends_on_pairs.add((source.removeprefix("skill:"), target.removeprefix("skill:")))

    sink_skills = set()
    for skill_ids in slot_consumers.values():
        sink_skills.update(skill_ids)
    for skill_ids in slot_aggregators.values():
        sink_skills.update(skill_ids)

    available_artifacts = {artifact.key for artifact in grounded.available_artifacts}
    output_map = {skill_id: output_keys(skill) for skill_id, skill in skill_by_id.items()}
    mixed_plans: list[dict[str, Any]] = []

    for sink_skill_id in sorted(sink_skills):
        if sink_skill_id not in skill_by_id:
            continue
        required_slots = {
            slot_name
            for slot_name, skill_ids in slot_consumers.items()
            if sink_skill_id in skill_ids
        } | {
            slot_name
            for slot_name, skill_ids in slot_aggregators.items()
            if sink_skill_id in skill_ids
        }
        if not required_slots:
            continue
        producer_skill_ids = {
            producer_id
            for slot_name in required_slots
            for producer_id in slot_producers.get(slot_name, set())
            if producer_id in skill_by_id
        }
        if not producer_skill_ids:
            continue

        selected_skill_ids = set(producer_skill_ids)
        selected_skill_ids.add(sink_skill_id)
        _expand_depends_on_dependencies(selected_skill_ids, depends_on_pairs)
        _expand_artifact_providers(
            selected_skill_ids,
            skill_by_id=skill_by_id,
            available_artifacts=available_artifacts,
            output_map=output_map,
            artifact_producers=artifact_producers,
        )
        _expand_depends_on_dependencies(selected_skill_ids, depends_on_pairs)
        slot_pairs = {
            (producer_id, sink_skill_id)
            for slot_name in required_slots
            for producer_id in slot_producers.get(slot_name, set())
            if producer_id in selected_skill_ids and producer_id != sink_skill_id
        }
        artifact_pairs = _artifact_dependency_pairs(
            selected_skill_ids,
            skill_by_id=skill_by_id,
            artifact_producers=artifact_producers,
        )
        ordering_pairs = set(depends_on_pairs) | artifact_pairs | slot_pairs
        ordered_skill_ids = _topological_order(selected_skill_ids, depends_on_pairs)
        if ordering_pairs:
            ordered_skill_ids = _topological_order(selected_skill_ids, ordering_pairs)
        if not ordered_skill_ids or len(ordered_skill_ids) > max_depth:
            continue

        steps = []
        produced_artifacts = set()
        missing = []
        runtime_available = set(available_artifacts)
        for skill_id in ordered_skill_ids:
            skill = skill_by_id[skill_id]
            step_missing = missing_inputs(skill, runtime_available)
            missing.extend({**item, "skill_id": skill_id} for item in step_missing)
            steps.append(
                {
                    "step": len(steps) + 1,
                    "skill_id": skill_id,
                    "name": skill.get("name", skill_id),
                    "tasks": list(skill.get("tasks", [])),
                    "inputs": [
                        {"name": item.get("name"), "type": item.get("type")}
                        for item in skill.get("inputs", [])
                    ],
                    "outputs": [
                        {"name": item.get("name"), "type": item.get("type")}
                        for item in skill.get("outputs", [])
                    ],
                    "missing_inputs": step_missing,
                }
            )
            produced = output_map.get(skill_id, set())
            produced_artifacts.update(produced)
            runtime_available.update(produced)

        synthetic_edges = []
        for source_id, target_id in sorted(artifact_pairs):
            synthetic_edges.append(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "confidence": 1.0,
                    "method": "artifact_link",
                    "relation_type": "consumes",
                    "source_outputs": [],
                    "target_inputs": [],
                    "reasons": ["artifact produces/consumes bridge"],
                }
            )
        for slot_name in sorted(required_slots):
            for producer_id in sorted(slot_producers.get(slot_name, set())):
                if producer_id not in selected_skill_ids:
                    continue
                relation_type = (
                    "aggregates"
                    if sink_skill_id in slot_aggregators.get(slot_name, set())
                    else "consumes"
                )
                synthetic_edges.append(
                    {
                        "source_id": producer_id,
                        "target_id": sink_skill_id,
                        "confidence": 1.0,
                        "method": "slot_link",
                        "relation_type": relation_type,
                        "slot_name": slot_name,
                        "source_outputs": [],
                        "target_inputs": [],
                        "reasons": [f"{slot_name} {relation_type} link"],
                    }
                )
        for source_id, target_id in sorted(depends_on_pairs):
            if source_id in selected_skill_ids and target_id in selected_skill_ids:
                synthetic_edges.append(
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "confidence": 1.0,
                        "method": "depends_on_link",
                        "relation_type": "depends_on",
                        "source_outputs": [],
                        "target_inputs": [],
                        "reasons": ["explicit dependency edge"],
                    }
                )

        goal_score = sum(
            skill_goal_score(skill_by_id[skill_id], grounded.goal_terms)
            for skill_id in ordered_skill_ids
            if skill_id in skill_by_id
        )
        mixed_plans.append(
            {
                "status": "ready" if not missing else "needs_input",
                "goal_score": round(goal_score, 3),
                "edge_confidence": 1.0,
                "consumed_user_artifacts": _count_consumed_artifacts(
                    steps,
                    grounded.available_artifacts,
                ),
                "stages": plan_stages(
                    [replace_step_for_stage(step) for step in steps],
                    synthetic_edges,
                ),
                "steps": steps,
                "produced_artifacts": [
                    {"name": name, "type": type_, "source": "skill_output"}
                    for name, type_ in sorted(produced_artifacts)
                ],
                "missing_inputs": missing,
                "can_feed_edges": synthetic_edges,
                "reasons": ["mixed_graph_slot_routing"],
            }
        )
        if len(mixed_plans) >= max_plans:
            break
    return mixed_plans


def _expand_depends_on_dependencies(
    selected_skill_ids: set[str],
    depends_on_pairs: set[tuple[str, str]],
) -> None:
    changed = True
    while changed:
        changed = False
        for source_id, target_id in depends_on_pairs:
            if target_id in selected_skill_ids and source_id not in selected_skill_ids:
                selected_skill_ids.add(source_id)
                changed = True


def _expand_artifact_providers(
    selected_skill_ids: set[str],
    *,
    skill_by_id: dict[str, dict[str, Any]],
    available_artifacts: set[tuple[str, str]],
    output_map: dict[str, set[tuple[str, str]]],
    artifact_producers: dict[tuple[str, str], set[str]],
) -> None:
    changed = True
    while changed:
        changed = False
        produced = set()
        for skill_id in selected_skill_ids:
            produced.update(output_map.get(skill_id, set()))
        available = produced | set(available_artifacts)
        pending_additions: set[str] = set()
        for skill_id in sorted(selected_skill_ids):
            skill = skill_by_id.get(skill_id)
            if skill is None:
                continue
            for item in skill.get("inputs", []):
                if not item.get("required", True):
                    continue
                expected = (str(item.get("name") or ""), str(item.get("type") or "unknown"))
                if not expected[0]:
                    continue
                if artifact_matches(expected, available):
                    continue
                for producer_id in sorted(artifact_producers.get(expected, set())):
                    if producer_id in selected_skill_ids or producer_id == skill_id:
                        continue
                    pending_additions.add(producer_id)
        if pending_additions:
            selected_skill_ids.update(pending_additions)
            changed = True


def _artifact_dependency_pairs(
    selected_skill_ids: set[str],
    *,
    skill_by_id: dict[str, dict[str, Any]],
    artifact_producers: dict[tuple[str, str], set[str]],
) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for target_id in selected_skill_ids:
        skill = skill_by_id.get(target_id)
        if skill is None:
            continue
        for item in skill.get("inputs", []):
            if not item.get("required", True):
                continue
            expected = (str(item.get("name") or ""), str(item.get("type") or "unknown"))
            if not expected[0]:
                continue
            for source_id in artifact_producers.get(expected, set()):
                if source_id in selected_skill_ids and source_id != target_id:
                    pairs.add((source_id, target_id))
    return pairs


def _artifact_key_from_node_id(node_id: str) -> tuple[str, str] | None:
    if not node_id.startswith("artifact:"):
        return None
    payload = node_id.removeprefix("artifact:")
    if ":" not in payload:
        return None
    name, type_ = payload.rsplit(":", 1)
    if not name:
        return None
    return (name, type_ or "unknown")


def _topological_order(
    skill_ids: set[str],
    depends_on_pairs: set[tuple[str, str]],
) -> list[str]:
    incoming = {skill_id: 0 for skill_id in skill_ids}
    outgoing: dict[str, set[str]] = {skill_id: set() for skill_id in skill_ids}
    for source_id, target_id in depends_on_pairs:
        if source_id in skill_ids and target_id in skill_ids:
            outgoing[source_id].add(target_id)
            incoming[target_id] += 1

    ordered = []
    queue = sorted(skill_id for skill_id, count in incoming.items() if count == 0)
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        for target_id in sorted(outgoing[current]):
            incoming[target_id] -= 1
            if incoming[target_id] == 0:
                queue.append(target_id)
    if len(ordered) != len(skill_ids):
        ordered.extend(sorted(skill_ids - set(ordered)))
    return ordered


def _count_consumed_artifacts(
    steps: list[dict[str, Any]],
    available_artifacts: list[Any],
) -> int:
    explicit = {
        artifact.key
        for artifact in available_artifacts
        if getattr(artifact, "source", "") != "implicit_query"
    }
    if not steps or not explicit:
        return 0
    consumed = 0
    for item in steps[0].get("inputs", []):
        expected = (str(item.get("name")), str(item.get("type") or "unknown"))
        if artifact_matches(expected, explicit):
            consumed += 1
    return consumed


def _annotate_plan_execution_feasibility(
    plan: dict[str, Any],
    *,
    slot_contracts: dict[str, Any],
) -> dict[str, Any]:
    plan_copy = dict(plan)
    required_contracts = (
        (slot_contracts.get("contracts") or {})
        if isinstance(slot_contracts, dict)
        else {}
    )
    steps = list(plan_copy.get("steps", []))
    edges = list(plan_copy.get("can_feed_edges", []))
    output_names_by_skill = {
        str(step.get("skill_id") or ""): {
            str(output.get("name") or "")
            for output in step.get("outputs", [])
            if output.get("name")
        }
        for step in steps
    }
    missing_contracts = []
    for edge in edges:
        slot_name = str(edge.get("slot_name") or "")
        if not slot_name:
            continue
        required = required_contracts.get(slot_name, {}).get("required_fields", [])
        if not required:
            continue
        producer_id = str(edge.get("source_id") or "")
        output_names = output_names_by_skill.get(producer_id, set())
        missing_fields = [field for field in required if field not in output_names]
        if missing_fields:
            missing_contracts.append(
                {
                    "slot_name": slot_name,
                    "producer_skill_id": producer_id,
                    "missing_fields": missing_fields,
                }
            )
    connectivity_trace = sorted(
        {
            str(edge.get("relation_type") or edge.get("method") or "unknown")
            for edge in edges
        }
    )
    strong_edge_types = {"can_feed", "consumes", "aggregates", "produces"}
    has_strong_data_edge = any(
        str(edge.get("relation_type") or "").strip() in strong_edge_types
        or str(edge.get("relation_type") or "").strip() == ""
        for edge in edges
    )
    plan_copy["missing_contracts"] = missing_contracts
    plan_copy["connectivity_trace"] = connectivity_trace
    if not steps:
        plan_copy["plan_classification"] = "invalid"
    elif len(steps) > 1 and not has_strong_data_edge:
        plan_copy["plan_classification"] = "structurally_valid_but_incomplete"
    elif plan_copy.get("missing_inputs") or missing_contracts:
        plan_copy["plan_classification"] = "structurally_valid_but_incomplete"
    else:
        plan_copy["plan_classification"] = "executable"
    return plan_copy


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


def _plan_chain_has_explicit_edges(
    skill_ids: list[str],
    *,
    can_feed_edges: list[dict[str, Any]],
    min_edge_confidence: float,
) -> bool:
    if len(skill_ids) <= 1:
        return True
    for source_id, target_id in zip(skill_ids, skill_ids[1:]):
        if not _has_explicit_adjacency(
            source_id,
            target_id,
            can_feed_edges=can_feed_edges,
            min_edge_confidence=min_edge_confidence,
        ):
            return False
    return True


def _has_explicit_adjacency(
    source_id: str,
    target_id: str,
    *,
    can_feed_edges: list[dict[str, Any]],
    min_edge_confidence: float,
) -> bool:
    for edge in can_feed_edges:
        source = _edge_skill_id(edge.get("source"))
        target = _edge_skill_id(edge.get("target"))
        confidence = float(edge.get("confidence") or 0.0)
        if source == source_id and target == target_id and confidence >= min_edge_confidence:
            return True
    return False


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
    return remapped


def _augment_with_structural_incomplete_plans(
    validated_plans: list[dict[str, Any]],
    candidate_plans: list[dict[str, Any]],
    *,
    query: str,
    grounded_query: dict[str, Any],
    top_k: int,
) -> list[dict[str, Any]]:
    if not _prefer_multi_step_for_complex_query(query, grounded_query):
        return validated_plans
    target = max(top_k * 3, 12)
    if len(validated_plans) >= target:
        return validated_plans

    existing_signatures = {
        tuple(
            str(step.get("skill_id") or "")
            for step in plan.get("steps", [])
            if step.get("skill_id")
        )
        for plan in validated_plans
    }
    augment_candidates: list[dict[str, Any]] = []
    for plan in candidate_plans:
        signature = tuple(
            str(step.get("skill_id") or "")
            for step in plan.get("steps", [])
            if step.get("skill_id")
        )
        if not signature or signature in existing_signatures:
            continue
        if len(signature) < 2:
            continue
        if plan.get("status") != "needs_input":
            continue
        if plan.get("plan_classification") != "structurally_valid_but_incomplete":
            continue
        augment_candidates.append(plan)

    augment_candidates.sort(
        key=lambda plan: (
            len(plan.get("missing_inputs") or []),
            -float(plan.get("goal_score") or 0.0),
            -float(plan.get("edge_confidence") or 0.0),
            -len(plan.get("steps") or []),
        )
    )
    output = list(validated_plans)
    for plan in augment_candidates:
        output.append(plan)
        if len(output) >= target:
            break
    return output


def _prefer_multi_step_for_complex_query(
    query: str,
    grounded_query: dict[str, Any],
) -> bool:
    query_text = str(query or "").lower()
    goal_terms = {
        str(item).lower()
        for item in grounded_query.get("goal_terms", [])
        if str(item).strip()
    }
    if len(goal_terms) >= 6:
        return True

    artifact_terms = {
        "prd",
        "api",
        "ui",
        "原型",
    }
    risk_terms = {
        "risk",
        "security",
        "test",
        "design",
        "technical",
        "product",
        "上线",
        "建议",
        "风险",
        "安全",
        "测试",
        "设计",
        "技术",
        "产品",
    }
    has_artifact_dim = any(token in query_text for token in artifact_terms)
    matched_risk_dims = sum(1 for token in risk_terms if token in query_text)
    return has_artifact_dim and matched_risk_dims >= 2


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
