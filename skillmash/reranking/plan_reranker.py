"""Rerank candidate Skill plans with an LLM and deterministic fallback."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from skillmash.common.llm import LLMConfig, create_llm_client

logger = logging.getLogger(__name__)


class RerankClient(Protocol):
    """Minimal JSON completion interface used by plan reranking."""

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        timeout: int | None = None,
        error_context: str = "LLM",
    ) -> str:
        ...


_PLAN_RERANK_SYSTEM_PROMPT = """You rank existing candidate Skill execution plans.
Return strict JSON only.

Rules:
- Select candidate plans by their integer index.
- Do not merge plans.
- Do not change skill order.
- Do not invent skills, inputs, outputs, or edge relationships.
- Prefer plans that best satisfy the user request with fewer missing critical inputs.

Schema:
{
  "recommended_plans": [
    {
      "plan_index": 3,
      "title": "API review path",
      "reason": "why this existing candidate is recommended"
    }
  ]
}
"""


class PlanReranker:
    """Use an LLM to sort existing candidate plans into top-k recommendations."""

    prompt_version = "plan-reranker-v2"

    def __init__(
        self,
        *,
        llm_config: LLMConfig | None = None,
        llm_client: RerankClient | None = None,
    ) -> None:
        self.model_name = ""
        if llm_client is not None:
            self.llm_client = llm_client
        elif llm_config is not None:
            self.model_name = llm_config.model
            self.llm_client = create_llm_client(llm_config)
        else:
            raise ValueError("PlanReranker requires llm_config or llm_client.")

    def rerank(
        self,
        planning_result: dict[str, Any],
        *,
        top_k: int = 3,
        top_m: int = 12,
        include_candidates: bool = True,
    ) -> dict[str, Any]:
        """Return planning_result enriched with recommendations and rank trace."""

        top_k = max(1, int(top_k))
        top_m = max(1, int(top_m))
        plans = list(planning_result.get("plans", []))
        prefer_multi_step = _prefer_multi_step_plans(
            str(planning_result.get("query") or ""),
            planning_result.get("grounded_query", {}),
        )
        sorted_indexes = _deterministic_plan_indexes(
            plans,
            query=str(planning_result.get("query") or ""),
            grounded_query=planning_result.get("grounded_query", {}),
        )
        pool_indexes = sorted_indexes[:top_m]
        logger.debug(
            "rerank start: plans=%s top_m=%s top_k=%s prefer_multi_step=%s pool_indexes=%s",
            len(plans),
            top_m,
            top_k,
            prefer_multi_step,
            pool_indexes,
        )

        payload = {
            "query": planning_result.get("query", ""),
            "grounded_query": planning_result.get("grounded_query", {}),
            "top_k": top_k,
            "candidate_plans": _candidate_payload(plans, pool_indexes),
        }

        recommended: list[dict[str, Any]] = []
        fallback_used = False
        fallback_reason = ""

        try:
            raw = self.llm_client.complete_json(
                system_prompt=_PLAN_RERANK_SYSTEM_PROMPT,
                user_content=json.dumps(payload, ensure_ascii=False),
                error_context="orchestration plan reranking",
            )
            parsed = json.loads(raw)
            recommended = _normalize_recommendations(parsed, plans, top_k=top_k)
        except Exception as exc:  # pragma: no cover - covered by behavior assertions
            fallback_used = True
            fallback_reason = str(exc)
            logger.debug("rerank llm failed, fallback enabled: %s", fallback_reason)

        if len(recommended) < top_k:
            fallback_used = True
            if not fallback_reason:
                fallback_reason = "llm_result_insufficient"
            logger.debug(
                "rerank backfill needed: recommended=%s top_k=%s reason=%s",
                len(recommended),
                top_k,
                fallback_reason,
            )
            recommended = _backfill_recommendations(
                recommended,
                plans,
                sorted_indexes,
                top_k=top_k,
            )
        recommended = _ensure_multi_step_coverage(
            recommended,
            plans,
            sorted_indexes,
            top_k=top_k,
            prefer_multi_step=prefer_multi_step,
        )
        logger.debug(
            "rerank done: ranking_mode=%s recommended_indexes=%s",
            "fallback" if fallback_used else "llm",
            [
                item.get("source_plan_index")
                for item in recommended
                if isinstance(item.get("source_plan_index"), int)
            ],
        )

        result = dict(planning_result)
        if not include_candidates:
            result.pop("plans", None)
        result["recommended_plans"] = recommended
        result["ranking_mode"] = "fallback" if fallback_used else "llm"
        result["rank_trace"] = {
            "ranker_name": self.__class__.__name__,
            "model": self.model_name,
            "prompt_version": self.prompt_version,
            "top_m": top_m,
            "top_k": top_k,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
        }
        return result


def _candidate_payload(
    plans: list[dict[str, Any]],
    indexes: list[int],
) -> list[dict[str, Any]]:
    payload = []
    for source_index in indexes:
        plan = plans[source_index - 1]
        payload.append(
            {
                "index": source_index,
                "status": plan.get("status"),
                "goal_score": plan.get("goal_score"),
                "edge_confidence": plan.get("edge_confidence"),
                "skill_order": [
                    step.get("skill_id")
                    for step in plan.get("steps", [])
                    if step.get("skill_id")
                ],
                "stages": [
                    [
                        skill.get("skill_id")
                        for skill in stage.get("skills", [])
                        if skill.get("skill_id")
                    ]
                    for stage in plan.get("stages", [])
                ],
                "missing_inputs": plan.get("missing_inputs", []),
                "reasons": plan.get("reasons", []),
            }
        )
    return payload


def _normalize_recommendations(
    payload: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    candidate_by_index = {
        index: plan for index, plan in enumerate(candidates, start=1)
    }
    normalized = []
    seen_indexes = set()
    for item in payload.get("recommended_plans", []):
        if not isinstance(item, dict):
            continue
        try:
            plan_index = int(item.get("plan_index"))
        except (TypeError, ValueError):
            continue
        if plan_index in seen_indexes or plan_index not in candidate_by_index:
            continue
        seen_indexes.add(plan_index)
        normalized.append(
            _recommendation_from_candidate(
                candidate_by_index[plan_index],
                plan_index,
                title=str(item.get("title") or f"Candidate plan {plan_index}"),
                reason=str(item.get("reason") or ""),
            )
        )
        if len(normalized) >= top_k:
            break
    return normalized


def _backfill_recommendations(
    existing: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sorted_indexes: list[int],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    if len(existing) >= top_k:
        return existing[:top_k]
    selected = {
        int(item.get("source_plan_index"))
        for item in existing
        if isinstance(item.get("source_plan_index"), int)
    }
    output = list(existing)
    for plan_index in sorted_indexes:
        if plan_index in selected:
            continue
        selected.add(plan_index)
        output.append(
            _recommendation_from_candidate(
                candidates[plan_index - 1],
                plan_index,
                title=f"Candidate plan {plan_index}",
                reason="deterministic fallback",
            )
        )
        if len(output) >= top_k:
            break
    return output


def _recommendation_from_candidate(
    candidate: dict[str, Any],
    plan_index: int,
    *,
    title: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "status": candidate.get("status", "unknown"),
        "steps": list(candidate.get("steps", [])),
        "skill_order": [
            step.get("skill_id")
            for step in candidate.get("steps", [])
            if step.get("skill_id")
        ],
        "stages": [
            {
                "stage": stage.get("stage"),
                "skill_order": [
                    skill.get("skill_id")
                    for skill in stage.get("skills", [])
                    if skill.get("skill_id")
                ],
            }
            for stage in candidate.get("stages", [])
        ],
        "source_plan_index": plan_index,
        "can_feed_edges": list(candidate.get("can_feed_edges", [])),
        "missing_inputs": candidate.get("missing_inputs", []),
        "plan_classification": candidate.get("plan_classification"),
        "connectivity_trace": candidate.get("connectivity_trace", []),
        "missing_contracts": candidate.get("missing_contracts", []),
        "reason": reason,
    }


def _ensure_multi_step_coverage(
    recommended: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    sorted_indexes: list[int],
    *,
    top_k: int,
    prefer_multi_step: bool,
) -> list[dict[str, Any]]:
    if not prefer_multi_step or not recommended:
        return recommended[:top_k]
    target_multi = 2 if top_k >= 3 else 1

    def _step_count(item: dict[str, Any]) -> int:
        return len(item.get("skill_order") or [])

    multi_count = sum(1 for item in recommended if _step_count(item) >= 2)
    if multi_count >= target_multi:
        return recommended[:top_k]

    selected_indexes = {
        int(item.get("source_plan_index"))
        for item in recommended
        if isinstance(item.get("source_plan_index"), int)
    }
    candidate_multi_indexes = [
        idx
        for idx in sorted_indexes
        if idx not in selected_indexes
        and len(plans[idx - 1].get("steps") or []) >= 2
        and float(plans[idx - 1].get("goal_score") or 0.0) > 0
    ]
    if not candidate_multi_indexes:
        return recommended[:top_k]

    output = list(recommended[:top_k])
    while multi_count < target_multi and candidate_multi_indexes:
        replace_at = None
        for i in range(len(output) - 1, -1, -1):
            if _step_count(output[i]) < 2:
                replace_at = i
                break
        if replace_at is None:
            break
        incoming_index = candidate_multi_indexes.pop(0)
        output[replace_at] = _recommendation_from_candidate(
            plans[incoming_index - 1],
            incoming_index,
            title=f"Candidate plan {incoming_index}",
            reason="deterministic diversity fallback",
        )
        multi_count = sum(1 for item in output if _step_count(item) >= 2)
    return output[:top_k]


def _deterministic_plan_indexes(
    plans: list[dict[str, Any]],
    *,
    query: str = "",
    grounded_query: dict[str, Any] | None = None,
) -> list[int]:
    prefer_multi_step = _prefer_multi_step_plans(query, grounded_query or {})
    scored = []
    for index, plan in enumerate(plans, start=1):
        step_count = len(plan.get("steps") or [])
        goal_score = float(plan.get("goal_score") or 0.0)
        edge_confidence = float(plan.get("edge_confidence") or 0.0)
        consumed_user_artifacts = int(plan.get("consumed_user_artifacts") or 0)
        relevance_rank = 0 if goal_score > 0 else 1
        multi_step_penalty = 1 if prefer_multi_step and step_count < 2 else 0
        status_rank = 1 if plan.get("status") != "ready" else 0
        if (
            prefer_multi_step
            and step_count >= 2
            and plan.get("plan_classification") == "structurally_valid_but_incomplete"
        ):
            status_rank = 0
        missing_count = len(plan.get("missing_inputs") or [])

        if prefer_multi_step:
            sort_key = (
                relevance_rank,
                -goal_score,
                status_rank,
                multi_step_penalty,
                missing_count,
                -edge_confidence,
                -consumed_user_artifacts,
                step_count,
                index,
            )
        else:
            sort_key = (
                relevance_rank,
                -goal_score,
                status_rank,
                missing_count,
                -edge_confidence,
                -consumed_user_artifacts,
                step_count,
                index,
            )
        scored.append(
            sort_key
        )
    return [item[-1] for item in sorted(scored)]


def _prefer_multi_step_plans(query: str, grounded_query: dict[str, Any]) -> bool:
    query_text = str(query or "").lower()
    goal_terms = {
        str(item).lower()
        for item in grounded_query.get("goal_terms", [])
        if str(item).strip()
    }
    if len(goal_terms) >= 4:
        return True

    trigger_terms = {
        "risk",
        "review",
        "audit",
        "security",
        "design",
        "test",
        "prd",
        "api",
        "ui",
        "上线",
        "风险",
        "评审",
        "审计",
        "安全",
        "测试",
        "建议",
    }
    matched = sum(1 for token in trigger_terms if token in query_text)
    return matched >= 2
