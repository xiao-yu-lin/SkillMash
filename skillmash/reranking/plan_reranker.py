"""Rerank candidate Skill plans with an LLM."""

from __future__ import annotations

import json
from typing import Any, Protocol

from skillmash.representation.llm import LLMConfig, create_llm_client


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

    def __init__(
        self,
        *,
        llm_config: LLMConfig | None = None,
        llm_client: RerankClient | None = None,
    ) -> None:
        if llm_client is not None:
            self.llm_client = llm_client
        elif llm_config is not None:
            self.llm_client = create_llm_client(llm_config)
        else:
            raise ValueError("PlanReranker requires llm_config or llm_client.")

    def rerank(self, planning_result: dict[str, Any], *, top_k: int = 3) -> dict[str, Any]:
        """Return planning_result with a recommended_plans field added."""

        candidates = _candidate_payload(planning_result)
        payload = {
            "query": planning_result.get("query", ""),
            "grounded_query": planning_result.get("grounded_query", {}),
            "top_k": max(1, top_k),
            "candidate_plans": candidates,
        }
        raw = self.llm_client.complete_json(
            system_prompt=_PLAN_RERANK_SYSTEM_PROMPT,
            user_content=json.dumps(payload, ensure_ascii=False),
            error_context="orchestration plan reranking",
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid plan reranking JSON: {raw[:500]}") from exc

        result = dict(planning_result)
        result["recommended_plans"] = _normalize_recommendations(
            parsed,
            planning_result.get("plans", []),
            top_k=max(1, top_k),
        )
        return result


def _candidate_payload(planning_result: dict[str, Any]) -> list[dict[str, Any]]:
    payload = []
    for index, plan in enumerate(planning_result.get("plans", []), start=1):
        payload.append(
            {
                "index": index,
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
        candidate = candidate_by_index[plan_index]
        normalized.append(
            {
                "title": str(item.get("title") or f"Candidate plan {plan_index}"),
                "status": candidate.get("status", "unknown"),
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
                "can_feed_edges": candidate.get("can_feed_edges", []),
                "source_plan_index": plan_index,
                "missing_inputs": candidate.get("missing_inputs", []),
                "reason": str(item.get("reason") or ""),
            }
        )
        if len(normalized) >= top_k:
            break
    return normalized
