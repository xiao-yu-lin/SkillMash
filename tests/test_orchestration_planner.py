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
    _annotate_plan_execution_feasibility,
)
from skillmash.representation import (
    ArtifactSpec,
    Condition,
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


class SubstituteMatcher:
    def match(self, registry, candidates):
        return [
            LLMMatch(
                source_id="review_api_pro",
                target_id="review_api",
                relation_type="substitute_for",
                confidence=0.9,
                method="test_matcher",
                accepted=True,
            )
        ]


class IncompatibleSubstituteMatcher:
    def match(self, registry, candidates):
        return [
            LLMMatch(
                source_id="review_api_needs_extra_input",
                target_id="review_api",
                relation_type="substitute_for",
                confidence=0.9,
                method="test_matcher",
                accepted=True,
            )
        ]


class EmptyMatcher:
    def match(self, registry, candidates):
        return []


def test_planning_config_exposes_entry_width_and_conservative_flags() -> None:
    cfg = PlanningConfig()
    assert hasattr(cfg, "max_entry_skills")
    assert hasattr(cfg, "conservative_reject")
    assert cfg.conservative_reject is True


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


def test_orchestrator_applies_slot_substitute_candidates(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=SubstituteMatcher()).build(
        [_make_api_skill(), _review_api_skill(), _review_api_pro_skill()]
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

    matched_plan = next(
        candidate
        for candidate in plan["plans"]
        if any(step.get("skill_id") == "review_api_pro" for step in candidate.get("steps", []))
    )
    assert matched_plan["slot_candidates"]


def test_orchestrator_records_feedback_for_incompatible_substitute(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=IncompatibleSubstituteMatcher()).build(
        [
            _make_api_skill(),
            _review_api_skill(),
            _review_api_needs_extra_input_skill(),
        ]
    )
    write_graph_build_result(result, tmp_path)
    feedback_path = tmp_path / "runtime" / "relation_feedback.jsonl"

    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [],
                "goal_terms": ["generate", "review", "api", "spec"],
            }
        ),
        planning_config=PlanningConfig(
            relation_feedback_path=str(feedback_path),
            include_candidates=True,
        ),
        max_plans=5,
    )
    planner.plan("Generate an api spec and review it")

    assert feedback_path.exists()
    lines = feedback_path.read_text(encoding="utf-8").splitlines()
    assert any("slot_incompatible_signature" in line for line in lines)


def test_orchestrator_connects_parallel_reviews_to_aggregator_without_direct_can_feed(
    tmp_path: Path,
) -> None:
    result = GraphBuilder(matcher=EmptyMatcher()).build(
        [
            _review_prd_skill(),
            _api_review_findings_skill(),
            _ui_review_findings_skill(),
            _delivery_brief_from_findings_skill(),
        ]
    )
    write_graph_build_result(result, tmp_path)
    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [
                    {"name": "prd_doc", "type": "markdown"},
                    {"name": "api_spec", "type": "yaml"},
                    {"name": "ui_prototype", "type": "image"},
                ],
                "goal_terms": ["review", "delivery", "brief"],
            }
        ),
        planning_config=PlanningConfig(conservative_reject=False),
        max_plans=10,
    )
    response = planner.plan("对PRD API UI做并行评审后汇总")

    mixed = next(
        candidate
        for candidate in response.get("plans", [])
        if "mixed_graph_slot_routing" in candidate.get("reasons", [])
    )
    assert mixed["plan_classification"] == "executable"
    assert "aggregates" in mixed["connectivity_trace"]
    assert {step["skill_id"] for step in mixed["steps"]} >= {
        "review_prd",
        "review_api_findings",
        "review_ui_findings",
        "delivery_brief",
    }


def test_orchestrator_uses_produces_consumes_to_bridge_generation_review_and_aggregation(
    tmp_path: Path,
) -> None:
    result = GraphBuilder(matcher=EmptyMatcher()).build(
        [
            _generate_api_spec_skill(),
            _api_review_findings_skill(),
            _delivery_brief_from_findings_skill(),
        ]
    )
    write_graph_build_result(result, tmp_path)
    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient(
            {
                "available_artifacts": [{"name": "goal", "type": "text"}],
                "goal_terms": ["generate", "review", "delivery", "brief"],
            }
        ),
        planning_config=PlanningConfig(conservative_reject=False),
        max_plans=10,
    )
    response = planner.plan("先生成API文档再评审并汇总")

    mixed = next(
        candidate
        for candidate in response.get("plans", [])
        if "mixed_graph_slot_routing" in candidate.get("reasons", [])
    )
    skill_order = [step["skill_id"] for step in mixed["steps"]]
    assert "generate_api_spec" in skill_order
    assert "review_api_findings" in skill_order
    assert "delivery_brief" in skill_order
    assert skill_order.index("generate_api_spec") < skill_order.index("review_api_findings")
    assert mixed["plan_classification"] == "executable"
    assert "consumes" in mixed["connectivity_trace"]


def test_depends_on_only_connectivity_is_not_treated_as_executable() -> None:
    annotated = _annotate_plan_execution_feasibility(
        {
            "steps": [
                {"skill_id": "prepare_context", "outputs": []},
                {"skill_id": "security_review", "outputs": []},
            ],
            "can_feed_edges": [
                {
                    "source_id": "prepare_context",
                    "target_id": "security_review",
                    "relation_type": "depends_on",
                    "method": "depends_on_link",
                }
            ],
            "missing_inputs": [],
        },
        slot_contracts={"contracts": {}},
    )
    assert annotated["plan_classification"] == "structurally_valid_but_incomplete"


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
        planning_config=PlanningConfig(conservative_reject=True),
        max_plans=5,
    )
    plan = planner.plan("Please review my api specification")

    assert plan["recommended_plans"] == []
    assert plan["ranking_mode"] == "conservative_reject"
    assert plan["decision"]["mode"] == "conservative_reject"
    assert plan["decision"]["fail_code_counts"]


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
        planning_config=PlanningConfig(conservative_reject=True),
    )
    response = planner.plan("review api")
    assert "decision" in response
    assert "mode" in response["decision"]
    assert "fail_code_counts" in response["decision"]


def test_slot_replacement_requires_explicit_can_feed_adjacency(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=SubstituteMatcher()).build(
        [_make_api_skill(), _review_api_skill(), _review_api_pro_skill()]
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
    )
    response = planner.plan("Generate an api spec and review it")

    for candidate in response.get("plans", []):
        assert all(
            edge.get("method") != "slot_replacement_chain"
            for edge in candidate.get("can_feed_edges", [])
        )


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


def _review_api_pro_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="review_api_pro",
        name="Review API Pro",
        description="Review API specification for security with pro checks.",
        version="1.0.0",
        tasks=["review", "audit"],
        inputs=[ParameterSpec(name="api_spec", type="yaml")],
        outputs=[ArtifactSpec(name="review_report", type="markdown")],
        preconditions=[],
        postconditions=[],
    )


def _review_api_needs_extra_input_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="review_api_needs_extra_input",
        name="Review API Needs Extra Input",
        description="Review API specification but requires extra context.",
        version="1.0.0",
        tasks=["review", "audit"],
        inputs=[
            ParameterSpec(name="api_spec", type="yaml"),
            ParameterSpec(name="workspace", type="path"),
        ],
        outputs=[ArtifactSpec(name="review_report", type="markdown")],
        preconditions=[],
        postconditions=[],
    )


def _generate_api_spec_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="generate_api_spec",
        name="Generate API Spec",
        description="Generate api spec from goal.",
        version="1.0.0",
        tasks=["generate", "design"],
        inputs=[ParameterSpec(name="goal", type="text")],
        outputs=[ArtifactSpec(name="api_spec", type="yaml")],
        preconditions=[],
        postconditions=[],
    )


def _review_prd_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="review_prd",
        name="Review PRD",
        description="Review PRD and output structured findings.",
        version="1.0.0",
        tasks=["review", "analysis"],
        inputs=[ParameterSpec(name="prd_doc", type="markdown")],
        outputs=[
            ArtifactSpec(name="summary", type="text"),
            ArtifactSpec(name="severity", type="text"),
            ArtifactSpec(name="evidence", type="text"),
            ArtifactSpec(name="recommendation", type="text"),
            ArtifactSpec(name="blocking", type="bool"),
        ],
        emits_slots=["prd_findings"],
        preconditions=[],
        postconditions=[],
    )


def _api_review_findings_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="review_api_findings",
        name="Review API Findings",
        description="Review api spec and output structured findings.",
        version="1.0.0",
        tasks=["review", "audit"],
        inputs=[ParameterSpec(name="api_spec", type="yaml")],
        outputs=[
            ArtifactSpec(name="summary", type="text"),
            ArtifactSpec(name="severity", type="text"),
            ArtifactSpec(name="evidence", type="text"),
            ArtifactSpec(name="recommendation", type="text"),
            ArtifactSpec(name="blocking", type="bool"),
        ],
        emits_slots=["security_findings"],
        preconditions=[],
        postconditions=[],
    )


def _ui_review_findings_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="review_ui_findings",
        name="Review UI Findings",
        description="Review ui prototype and output structured findings.",
        version="1.0.0",
        tasks=["review", "analysis"],
        inputs=[ParameterSpec(name="ui_prototype", type="image")],
        outputs=[
            ArtifactSpec(name="summary", type="text"),
            ArtifactSpec(name="severity", type="text"),
            ArtifactSpec(name="evidence", type="text"),
            ArtifactSpec(name="recommendation", type="text"),
            ArtifactSpec(name="blocking", type="bool"),
        ],
        emits_slots=["design_review_findings"],
        preconditions=[],
        postconditions=[],
    )


def _delivery_brief_from_findings_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="delivery_brief",
        name="Delivery Brief",
        description="Aggregate findings and produce a delivery brief.",
        version="1.0.0",
        tasks=["synthesize", "orchestrate"],
        inputs=[],
        outputs=[ArtifactSpec(name="delivery_brief", type="markdown")],
        consumes_slots=["prd_findings", "security_findings", "design_review_findings"],
        emits_slots=["delivery_brief"],
        preconditions=[Condition(type="depends_on_skill", expression="review_api_findings")],
        postconditions=[],
    )
