"""Orchestration facade built on planning helpers."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.orchestration.planning.grounding import ground_query
from skillmash.orchestration.planning.models import GroundedQuery, GroundingClient, PlanningConfig
from skillmash.orchestration.planning.search import (
    build_incoming_edges,
    build_outgoing_edges,
    dedupe_plans,
    search_backward_plans,
    search_plans,
)
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
        beam_width: int | None = None,
        top_m: int | None = None,
        top_k: int | None = None,
        include_candidates: bool | None = None,
        conservative_reject: bool | None = None,
        hard_fail_missing_inputs: bool | None = None,
        enable_backward_search: bool | None = None,
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
            beam_width=beam_width,
            top_m=top_m,
            top_k=top_k,
            include_candidates=include_candidates,
            conservative_reject=conservative_reject,
            hard_fail_missing_inputs=hard_fail_missing_inputs,
            enable_backward_search=enable_backward_search,
        )

        self.min_edge_confidence = clamp(self.config.min_edge_confidence)
        self.max_depth = max(1, self.config.max_depth)
        self.max_plans = max(1, self.config.max_plans)
        self.max_branch = max(1, self.config.max_branch)
        self.max_entry_skills = max(1, self.config.max_entry_skills)
        self.beam_width = max(1, self.config.beam_width)
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
        self.all_relation_edges = list(self.all_can_feed_edges)

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
            beam_width=planning_config.beam_width if planning_config else None,
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
            enable_backward_search=planning_config.enable_backward_search
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
            beam_width=max(1, config.beam_width),
        )
        if config.enable_backward_search:
            plans = dedupe_plans(
                [
                    *plans,
                    *search_backward_plans(
                        artifacts=self.artifacts,
                        skill_by_id=self.skill_by_id,
                        can_feed_edges=can_feed_edges,
                        incoming_edges=build_incoming_edges(can_feed_edges),
                        grounded=grounded,
                        max_depth=max(1, config.max_depth),
                        max_plans=max(1, config.max_plans),
                        max_branch=max(1, config.max_branch),
                        beam_width=max(1, config.beam_width),
                    ),
                ]
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
        candidate_plans = [
            _annotate_plan_execution_feasibility(plan)
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
                -int(plan.get("consumed_user_artifacts") or 0),
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
    beam_width: int | None,
    top_m: int | None,
    top_k: int | None,
    include_candidates: bool | None,
    conservative_reject: bool | None,
    hard_fail_missing_inputs: bool | None,
    enable_backward_search: bool | None,
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
        beam_width=int(beam_width) if beam_width is not None else base_config.beam_width,
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
        enable_backward_search=(
            bool(enable_backward_search)
            if enable_backward_search is not None
            else base_config.enable_backward_search
        ),
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
        beam_width=int(defaults.get("beam_width", PlanningConfig.beam_width)),
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
        enable_backward_search=bool(
            defaults.get(
                "enable_backward_search",
                PlanningConfig.enable_backward_search,
            )
        ),
    )


def _annotate_plan_execution_feasibility(
    plan: dict[str, Any],
) -> dict[str, Any]:
    plan_copy = dict(plan)
    steps = list(plan_copy.get("steps", []))
    edges = list(plan_copy.get("can_feed_edges", []))
    connectivity_trace = sorted(
        {
            str(edge.get("relation_type") or edge.get("method") or "unknown")
            for edge in edges
        }
    )
    strong_edge_types = {"can_feed"}
    has_strong_data_edge = any(
        str(edge.get("relation_type") or "").strip() in strong_edge_types
        or str(edge.get("relation_type") or "").strip() == ""
        for edge in edges
    )
    plan_copy["connectivity_trace"] = connectivity_trace
    if not steps:
        plan_copy["plan_classification"] = "invalid"
    elif len(steps) > 1 and not has_strong_data_edge:
        plan_copy["plan_classification"] = "structurally_valid_but_incomplete"
    elif plan_copy.get("missing_inputs"):
        plan_copy["plan_classification"] = "structurally_valid_but_incomplete"
    else:
        plan_copy["plan_classification"] = "executable"
    return plan_copy


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


