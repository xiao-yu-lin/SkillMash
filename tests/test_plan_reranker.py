from __future__ import annotations

import json

from skillmash.reranking import PlanReranker


class FakeRerankClient:
    def complete_json(self, **kwargs):
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


def test_plan_reranker_only_sorts_existing_candidate_plans() -> None:
    result = {
        "query": "prepare API launch",
        "grounded_query": {"goal_terms": ["review", "security"]},
        "plans": [
            {
                "status": "needs_input",
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
        ],
    }

    reranked = PlanReranker(llm_client=FakeRerankClient()).rerank(result, top_k=2)

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
