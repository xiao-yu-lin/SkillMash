from __future__ import annotations

import json
from pathlib import Path

from skillmash.graph import GraphBuilder, LLMMatch, write_graph_build_result
from skillmash.orchestration import (
    PlanningConfig,
    SkillOrchestrator,
    load_build_artifacts,
)
from skillmash.orchestration.planning.orchestrator import (
    _augment_with_structural_incomplete_plans,
    _annotate_plan_execution_feasibility,
)
from skillmash.representation import (
    ArtifactSpec,
    ParameterSpec,
    SkillRepresentation,
)


class ExactMatcher:
    def match(self, registry, candidates):
        return [
            LLMMatch(
                source_id="make_api",
                target_id="review_api",
                relation_type="can_feed",
                confidence=0.95,
                method="test_matcher",
                supporting_fields={
                    "source_outputs": ["api_spec"],
                    "target_inputs": ["api_spec"],
                },
                accepted=True,
            )
        ]


class BranchingMatcher:
    def match(self, registry, candidates):
        return [
            LLMMatch(
                source_id="make_api",
                target_id="review_api",
                relation_type="can_feed",
                confidence=0.95,
                method="test_matcher",
                supporting_fields={
                    "source_outputs": ["api_spec"],
                    "target_inputs": ["api_spec"],
                },
                accepted=True,
            ),
            LLMMatch(
                source_id="make_api",
                target_id="deploy_api",
                relation_type="can_feed",
                confidence=0.92,
                method="test_matcher",
                supporting_fields={
                    "source_outputs": ["api_spec"],
                    "target_inputs": ["api_spec"],
                },
                accepted=True,
            ),
        ]


class FakeGroundingClient:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, **kwargs):
        return json.dumps(self.payload)


class EmptyMatcher:
    def match(self, registry, candidates):
        return []


class PortFlowMatcher:
    def match(self, registry, candidates):
        return [
            LLMMatch(
                source_id="xiaoyi-image-translation",
                target_id="general-writing",
                relation_type="can_feed",
                confidence=0.92,
                method="test_matcher",
                supporting_fields={
                    "port_mappings": [
                        {
                            "source_output": "translated_text",
                            "target_input": "query",
                        }
                    ],
                    "source_outputs": ["translated_text"],
                    "target_inputs": ["query"],
                },
                accepted=True,
            ),
            LLMMatch(
                source_id="general-writing",
                target_id="imap-smtp-email",
                relation_type="can_feed",
                confidence=0.9,
                method="test_matcher",
                supporting_fields={
                    "port_mappings": [
                        {
                            "source_output": "document",
                            "target_input": "body",
                        }
                    ],
                    "source_outputs": ["document"],
                    "target_inputs": ["body"],
                },
                accepted=True,
            ),
        ]


class FirstPlanRanker:
    def rerank(self, planning_result, *, top_k=3, top_m=12, include_candidates=True):
        result = dict(planning_result)
        plans = list(planning_result.get("plans", []))
        result["recommended_plans"] = plans[:top_k]
        result["ranking_mode"] = "test"
        result["rank_trace"] = {"top_k": top_k, "top_m": top_m}
        if not include_candidates:
            result.pop("plans", None)
        return result


def test_planning_config_exposes_entry_width_and_conservative_flags() -> None:
    cfg = PlanningConfig()
    assert hasattr(cfg, "max_entry_skills")
    assert hasattr(cfg, "conservative_reject")
    assert hasattr(cfg, "hard_fail_missing_inputs")
    assert cfg.conservative_reject is True
    assert cfg.hard_fail_missing_inputs is False


def test_orchestrator_uses_user_artifacts_as_entry(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=ExactMatcher()).build(
        [_make_api_skill(), _review_api_skill()]
    )
    write_graph_build_result(result, tmp_path)

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [{"name": "api_spec", "type": "yaml"}],
                "goal_terms": ["review", "api", "spec"],
            }
        ),
        max_plans=5,
    )
    plan = planner.plan("I have api_spec and want an API review")

    assert plan["grounded_query"]["available_artifacts"]
    first = plan["plans"][0]
    assert first["steps"][0]["skill_id"] == "review_api"
    assert first["status"] == "ready"


def test_orchestrator_traverses_can_feed_when_needed(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=ExactMatcher()).build(
        [_make_api_skill(), _review_api_skill()]
    )
    write_graph_build_result(result, tmp_path)

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [],
                "goal_terms": ["generate", "review", "api", "spec"],
            }
        ),
        max_plans=5,
    )
    plan = planner.plan("Generate an api spec and review it")

    step_ids = [
        step["skill_id"]
        for candidate in plan["plans"]
        for step in candidate["steps"]
    ]
    assert "make_api" in step_ids
    assert "review_api" in step_ids


def test_orchestrator_composes_shared_upstream_paths_into_dag(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=BranchingMatcher()).build(
        [_make_api_skill(), _review_api_skill(), _deploy_api_skill()]
    )
    write_graph_build_result(result, tmp_path)

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [],
                "goal_terms": ["generate", "review", "deploy", "api", "spec"],
            }
        ),
        max_plans=5,
    )
    plan = planner.plan("Generate an api spec, review it, and prepare deployment")

    dag_plan = next(
        candidate
        for candidate in plan["plans"]
        if len(candidate["stages"]) >= 2
        and len(candidate["stages"][1]["skills"]) == 2
    )
    assert [skill["skill_id"] for skill in dag_plan["stages"][0]["skills"]] == [
        "make_api"
    ]
    assert {
        skill["skill_id"] for skill in dag_plan["stages"][1]["skills"]
    } == {"review_api", "deploy_api"}


def test_orchestrator_returns_recommendations_with_ranking_trace(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=ExactMatcher()).build(
        [_make_api_skill(), _review_api_skill()]
    )
    write_graph_build_result(result, tmp_path)

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [],
                "goal_terms": ["generate", "review", "api", "spec"],
            }
        ),
        max_plans=5,
    )
    plan = planner.plan("Generate an api spec and review it")

    assert "recommended_plans" in plan
    assert "ranking_mode" in plan
    assert "rank_trace" in plan
    assert plan["rank_trace"]["top_k"] == 3


def test_orchestrator_returns_conservative_rejection_when_no_validated_plan(
    tmp_path: Path,
) -> None:
    result = GraphBuilder(matcher=ExactMatcher()).build([_review_api_skill()])
    write_graph_build_result(result, tmp_path)

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [],
                "goal_terms": ["review", "api", "spec"],
            }
        ),
        planning_config=PlanningConfig(
            conservative_reject=True,
            hard_fail_missing_inputs=True,
        ),
        max_plans=5,
    )
    plan = planner.plan("Please review my api specification")

    assert plan["recommended_plans"] == []
    assert plan["ranking_mode"] == "conservative_reject"
    assert plan["decision"]["mode"] == "conservative_reject"
    assert plan["decision"]["fail_code_counts"]


def test_orchestrator_keeps_needs_input_plan_in_ranking_pool_by_default(
    tmp_path: Path,
) -> None:
    result = GraphBuilder(matcher=ExactMatcher()).build([_review_api_skill()])
    write_graph_build_result(result, tmp_path)

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [],
                "goal_terms": ["review", "api", "spec"],
            }
        ),
        planning_config=PlanningConfig(conservative_reject=True),
        max_plans=5,
    )
    plan = planner.plan("Please review my api specification")

    assert plan["recommended_plans"]
    assert plan["ranking_mode"] != "conservative_reject"
    assert any(
        candidate.get("status") == "needs_input"
        for candidate in plan.get("plans", [])
    )


def test_orchestrator_decision_trace_has_mode_and_fail_aggregation(
    tmp_path: Path,
) -> None:
    result = GraphBuilder(matcher=ExactMatcher()).build([_review_api_skill()])
    write_graph_build_result(result, tmp_path)

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [],
                "goal_terms": ["review", "api", "spec"],
            }
        ),
        planning_config=PlanningConfig(
            conservative_reject=True,
            hard_fail_missing_inputs=True,
        ),
    )
    response = planner.plan("review api")
    assert "decision" in response
    assert "mode" in response["decision"]
    assert "fail_code_counts" in response["decision"]


def test_reliability_first_records_strategy_in_decision(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=BranchingMatcher()).build(
        [_make_api_skill(), _review_api_skill(), _deploy_api_skill()]
    )
    write_graph_build_result(result, tmp_path)
    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [],
                "goal_terms": ["generate", "review", "deploy", "api", "spec"],
            }
        ),
    )

    response = planner.plan("Generate and review and deploy api")
    assert response["decision"]["strategy"] == "reliability_first"


def test_augment_with_structural_incomplete_plans_prefers_complex_query() -> None:
    validated = [
        {
            "status": "ready",
            "goal_score": 9.0,
            "edge_confidence": 1.0,
            "steps": [{"skill_id": "prd-review-team"}],
            "plan_classification": "executable",
            "missing_inputs": [],
        }
    ]
    candidates = validated + [
        {
            "status": "needs_input",
            "goal_score": 8.0,
            "edge_confidence": 1.0,
            "steps": [
                {"skill_id": "prd-review-team"},
                {"skill_id": "api-design-review-team"},
                {"skill_id": "wisedev-team"},
            ],
            "plan_classification": "structurally_valid_but_incomplete",
            "missing_inputs": [{"skill_id": "wisedev-team", "name": "x", "type": "text"}],
        }
    ]
    output = _augment_with_structural_incomplete_plans(
        validated,
        candidates,
        query="杩欐槸鎴戜滑鐨?PRD銆丄PI 鑽夋鍜?UI 鍘熷瀷銆傝璇勪及椋庨櫓骞剁粰涓婄嚎寤鸿",
        grounded_query={"goal_terms": ["review", "audit"]},
        top_k=3,
    )
    assert len(output) > len(validated)
    assert any(len(plan.get("steps", [])) >= 2 for plan in output)


def test_augment_with_structural_incomplete_plans_noop_for_simple_query() -> None:
    validated = [
        {
            "status": "ready",
            "goal_score": 9.0,
            "edge_confidence": 1.0,
            "steps": [{"skill_id": "review_api"}],
            "plan_classification": "executable",
            "missing_inputs": [],
        }
    ]
    candidates = validated + [
        {
            "status": "needs_input",
            "goal_score": 8.0,
            "edge_confidence": 1.0,
            "steps": [{"skill_id": "a"}, {"skill_id": "b"}],
            "plan_classification": "structurally_valid_but_incomplete",
            "missing_inputs": [{"skill_id": "b", "name": "x", "type": "text"}],
        }
    ]
    output = _augment_with_structural_incomplete_plans(
        validated,
        candidates,
        query="review api",
        grounded_query={"goal_terms": ["review", "api"]},
        top_k=3,
    )
    assert output == validated


def test_orchestrator_uses_port_edges_and_inferred_control_inputs(
    tmp_path: Path,
) -> None:
    result = GraphBuilder(matcher=PortFlowMatcher()).build(
        [
            _image_translation_skill(),
            _general_writing_skill(),
            _email_skill(),
        ]
    )
    write_graph_build_result(result, tmp_path)

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [
                    {"name": "image_url", "type": "url"},
                ],
                "inferred_inputs": [
                    {
                        "skill_id": "xiaoyi-image-translation",
                        "name": "target_language",
                        "type": "text",
                        "value": "zh-CHS",
                    },
                    {
                        "skill_id": "imap-smtp-email",
                        "name": "command",
                        "type": "text",
                        "value": "send",
                    },
                    {
                        "skill_id": "imap-smtp-email",
                        "name": "to",
                        "type": "text",
                        "value": "王总",
                    },
                ],
                "goal_terms": [
                    "translate",
                    "image",
                    "writing",
                    "document",
                    "email",
                    "send",
                ],
            }
        ),
        ranker=FirstPlanRanker(),
        min_edge_confidence=0.7,
        max_depth=4,
        max_plans=20,
        top_k=5,
    )

    response = planner.plan("翻译图中的英文报告，然后提炼前三页核心观点发邮件给王总")
    plan = next(
        item
        for item in response["plans"]
        if [step["skill_id"] for step in item["steps"]]
        == [
            "xiaoyi-image-translation",
            "general-writing",
            "imap-smtp-email",
        ]
    )

    assert ("imap-smtp-email", "command") not in {
        (item["skill_id"], item["name"]) for item in plan["missing_inputs"]
    }
    assert ("imap-smtp-email", "to") in {
        (item["skill_id"], item["name"]) for item in plan["missing_inputs"]
    }
    email_step = next(
        step for step in plan["steps"] if step["skill_id"] == "imap-smtp-email"
    )
    assert {
        (item["name"], item["value"]) for item in email_step["filled_inputs"]
    } == {("command", "send")}
    edge_ports = [
        mapping
        for edge in plan["can_feed_edges"]
        for mapping in edge["port_mappings"]
    ]
    assert {
        (mapping["source_output"], mapping["target_input"])
        for mapping in edge_ports
    } >= {("translated_text", "query"), ("document", "body")}


def _make_api_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="make_api",
        name="Make API",
        description="Generate API specification from a goal.",
        version="1.0.0",
        tasks=["generate", "design"],
        inputs=[ParameterSpec(name="goal", type="text")],
        outputs=[ArtifactSpec(name="api_spec", type="yaml")],
        preconditions=[],
        postconditions=[],
    )


def _review_api_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="review_api",
        name="Review API",
        description="Review API specification for security issues.",
        version="1.0.0",
        tasks=["review", "audit"],
        inputs=[ParameterSpec(name="api_spec", type="yaml")],
        outputs=[ArtifactSpec(name="review_report", type="markdown")],
        preconditions=[],
        postconditions=[],
    )


def _deploy_api_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="deploy_api",
        name="Deploy API",
        description="Prepare API deployment pipeline.",
        version="1.0.0",
        tasks=["deploy", "validate"],
        inputs=[ParameterSpec(name="api_spec", type="yaml")],
        outputs=[ArtifactSpec(name="deployment_plan", type="markdown")],
        preconditions=[],
        postconditions=[],
    )


def _image_translation_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="xiaoyi-image-translation",
        name="Image Translation",
        description="Recognize and translate text in images.",
        version="1.0.0",
        inputs=[
            ParameterSpec(name="image_url", type="url", required=False),
            ParameterSpec(name="target_language", type="text"),
        ],
        outputs=[
            ArtifactSpec(name="translated_text", type="text"),
            ArtifactSpec(name="ocr_text", type="text"),
        ],
    )


def _general_writing_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="general-writing",
        name="General Writing",
        description="Write markdown from a request and source material.",
        version="1.0.0",
        inputs=[
            ParameterSpec(name="query", type="text"),
            ParameterSpec(name="sources", type="json", required=False),
        ],
        outputs=[ArtifactSpec(name="document", type="markdown")],
    )


def _email_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="imap-smtp-email",
        name="IMAP SMTP Email",
        description="Send email using SMTP.",
        version="1.0.0",
        inputs=[
            ParameterSpec(name="command", type="text"),
            ParameterSpec(name="to", type="text"),
            ParameterSpec(name="body", type="text", required=False),
        ],
        outputs=[ArtifactSpec(name="confirmation", type="text")],
    )


