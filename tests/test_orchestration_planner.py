from __future__ import annotations

import json
from pathlib import Path

from skillmash.graph import GraphBuilder, LLMMatch, write_graph_build_result
from skillmash.orchestration import (
    PlanningConfig,
    SkillOrchestrator,
    load_build_artifacts,
)
from skillmash.representation import ArtifactSpec, ParameterSpec, SkillRepresentation


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
