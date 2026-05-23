from __future__ import annotations

import json

from skillmash.reranking import PlanReranker


class FakeRerankClient:
    def __init__(self):
        self.requests = []

    def complete_json(self, **kwargs):
        self.requests.append(kwargs)
        return json.dumps(
            {
                "recommended_plans": [
                    {
                        "plan_index": 2,
                        "title": "Security path",
                        "reason": "Best matches security concern.",
                    },
                    {
                        "plan_index": 999,
                        "title": "Invalid path",
                        "reason": "Should be ignored.",
                    },
                    {
                        "plan_index": 1,
                        "title": "API review path",
                        "reason": "Useful secondary path.",
                    },
                ]
            }
        )


class InvalidJsonRerankClient:
    def complete_json(self, **kwargs):
        return "{not-json"


class SparseRerankClient:
    def complete_json(self, **kwargs):
        return json.dumps(
            {
                "recommended_plans": [
                    {"plan_index": 2, "title": "Only one", "reason": "single result"}
                ]
            }
        )


def _sample_planning_result() -> dict:
    return {
        "query": "prepare API launch",
        "grounded_query": {"goal_terms": ["review", "security"]},
        "plans": [
            {
                "status": "needs_input",
                "goal_score": 8.0,
                "edge_confidence": 0.8,
                "steps": [
                    {"skill_id": "wisedev-team"},
                    {"skill_id": "api-design-review-team"},
                ],
                "missing_inputs": [
                    {
                        "skill_id": "wisedev-team",
                        "name": "workspace",
                        "type": "path",
                    }
                ],
            },
            {
                "status": "needs_input",
                "goal_score": 9.0,
                "edge_confidence": 0.9,
                "steps": [
                    {"skill_id": "wisedev-team"},
                    {"skill_id": "api-pentest-team"},
                ],
                "missing_inputs": [
                    {
                        "skill_id": "api-pentest-team",
                        "name": "api_base_url",
                        "type": "url",
                    }
                ],
            },
            {
                "status": "ready",
                "goal_score": 5.0,
                "edge_confidence": 0.6,
                "steps": [{"skill_id": "quick-summary-team"}],
                "missing_inputs": [],
            },
        ],
    }


def test_plan_reranker_only_sorts_existing_candidate_plans() -> None:
    result = _sample_planning_result()
    reranked = PlanReranker(llm_client=FakeRerankClient()).rerank(
        result,
        top_k=2,
    )

    recommended = reranked["recommended_plans"]
    assert [item["source_plan_index"] for item in recommended] == [2, 1]
    assert recommended[0]["skill_order"] == ["wisedev-team", "api-pentest-team"]
    assert recommended[1]["skill_order"] == [
        "wisedev-team",
        "api-design-review-team",
    ]
    assert recommended[0]["missing_inputs"] == [
        {
            "skill_id": "api-pentest-team",
            "name": "api_base_url",
            "type": "url",
        }
    ]


def test_plan_reranker_limits_llm_candidates_by_top_m() -> None:
    result = _sample_planning_result()
    client = FakeRerankClient()

    PlanReranker(llm_client=client).rerank(result, top_k=2, top_m=1)

    payload = json.loads(client.requests[0]["user_content"])
    assert len(payload["candidate_plans"]) == 1


def test_plan_reranker_falls_back_for_invalid_json() -> None:
    result = _sample_planning_result()
    reranked = PlanReranker(llm_client=InvalidJsonRerankClient()).rerank(
        result,
        top_k=2,
    )

    assert reranked["ranking_mode"] == "fallback"
    assert reranked["rank_trace"]["fallback_used"] is True
    assert len(reranked["recommended_plans"]) == 2


def test_plan_reranker_backfills_when_llm_returns_too_few() -> None:
    result = _sample_planning_result()
    reranked = PlanReranker(llm_client=SparseRerankClient()).rerank(result, top_k=3)

    assert reranked["ranking_mode"] == "fallback"
    assert [item["source_plan_index"] for item in reranked["recommended_plans"][:1]] == [2]
    assert len(reranked["recommended_plans"]) == 3
