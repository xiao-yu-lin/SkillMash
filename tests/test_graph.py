from __future__ import annotations

import json
from pathlib import Path

from skillmash.graph import (
    CandidateGenerator,
    GraphBuilder,
    GraphDiagnostic,
    LLMMatch,
    SkillIndexBuilder,
    SkillRegistryBuilder,
    validate_llm_matches,
    write_graph_build_result,
)
from skillmash.orchestration import load_build_artifacts
from skillmash.representation import ArtifactSpec, Condition, ParameterSpec, SkillRepresentation


class AcceptingMatcher:
    def match(self, registry, candidates):
        matches = []
        for candidate in candidates:
            if "can_feed" not in candidate.relation_hints:
                continue
            for direction, evidence in candidate.evidence.get("directions", {}).items():
                if "source_outputs" not in evidence or "target_inputs" not in evidence:
                    continue
                source_id, target_id = direction.split("->", 1)
                break
            else:
                continue
            matches.append(
                LLMMatch(
                    source_id=source_id,
                    target_id=target_id,
                    relation_type="can_feed",
                    confidence=0.95,
                    method="test_matcher",
                    reasons=["candidate accepted"],
                    supporting_fields={
                        "source_outputs": [
                            item["name"]
                            for item in evidence.get("source_outputs", [])
                        ],
                        "target_inputs": [
                            item["name"]
                            for item in evidence.get("target_inputs", [])
                        ],
                    },
                    candidate_id=candidate.key,
                    accepted=True,
                )
            )
        return matches


def test_load_build_artifacts_prefers_manifest_vocab_paths(tmp_path: Path) -> None:
    repre_dir = tmp_path / "repre"
    repre_dir.mkdir(parents=True)

    manifest = {
        "schema_version": "skillmash-build-v1",
        "artifacts": {
            "skills": "skills.json",
            "graph": "skill_graph.json",
            "index": "skill_index.json",
            "io_name_vocab": "repre/io_name_vocab.json",
            "task_vocab": "repre/task_vocab.json",
        },
    }
    (tmp_path / "build_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (tmp_path / "skills.json").write_text(
        json.dumps({"skills": []}), encoding="utf-8"
    )
    (tmp_path / "skill_graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )
    (tmp_path / "skill_index.json").write_text(
        json.dumps({"by_output": {}, "by_input": {}, "by_task": {}, "by_data_type": {}, "neighbors": {}, "upstream_by_input": {}, "downstream_by_output": {}, "by_text_term": {}}),
        encoding="utf-8",
    )
    (repre_dir / "io_name_vocab.json").write_text(
        json.dumps({"version": "io-name-vocab-v1", "terms": []}),
        encoding="utf-8",
    )
    (repre_dir / "task_vocab.json").write_text(
        json.dumps({"version": "task-vocab-v1", "terms": []}),
        encoding="utf-8",
    )

    artifacts = load_build_artifacts(tmp_path)
    assert artifacts.io_name_vocab is not None
    assert artifacts.task_vocab is not None


def test_load_build_artifacts_reads_optional_slot_artifacts_and_degrades_when_missing(
    tmp_path: Path,
) -> None:
    manifest = {
        "schema_version": "skillmash-build-v1",
        "artifacts": {
            "skills": "skills.json",
            "graph": "skill_graph.json",
            "index": "skill_index.json",
            "slot_taxonomy": "custom/slot_taxonomy.json",
            "slot_contracts": "custom/slot_contracts.json",
        },
    }
    (tmp_path / "custom").mkdir(parents=True)
    (tmp_path / "build_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (tmp_path / "skills.json").write_text(
        json.dumps({"skills": []}), encoding="utf-8"
    )
    (tmp_path / "skill_graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )
    (tmp_path / "skill_index.json").write_text(
        json.dumps(
            {
                "by_output": {},
                "by_input": {},
                "by_task": {},
                "by_data_type": {},
                "neighbors": {},
                "upstream_by_input": {},
                "downstream_by_output": {},
                "by_text_term": {},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "custom" / "slot_taxonomy.json").write_text(
        json.dumps({"slots": ["security_findings"]}), encoding="utf-8"
    )
    (tmp_path / "custom" / "slot_contracts.json").write_text(
        json.dumps({"contracts": {"security_findings": {"required_fields": ["summary"]}}}),
        encoding="utf-8",
    )

    artifacts = load_build_artifacts(tmp_path)
    assert artifacts.slot_taxonomy == {"slots": ["security_findings"]}
    assert artifacts.slot_contracts is not None

    (tmp_path / "custom" / "slot_taxonomy.json").unlink()
    (tmp_path / "custom" / "slot_contracts.json").unlink()
    degraded = load_build_artifacts(tmp_path)
    assert degraded.slot_taxonomy is None
    assert degraded.slot_contracts is None


def test_candidate_generator_finds_exact_io_can_feed_candidate() -> None:
    registry = SkillRegistryBuilder().register(
        [_web_search_skill(), _summarize_skill()]
    )

    candidates = CandidateGenerator().generate(registry)

    exact = [
        candidate
        for candidate in candidates
        if candidate.source_id == "web_search"
        and candidate.target_id == "summarize_text"
        and "can_feed" in candidate.relation_hints
    ]
    assert len(exact) == 1
    assert "exact_io_match" in exact[0].candidate_methods
    assert exact[0].priority == "high"
    evidence = exact[0].evidence["directions"]["web_search->summarize_text"]
    assert "search_results" in evidence["matched_terms"]


def test_candidate_generator_emits_slot_and_dependency_candidates() -> None:
    registry = SkillRegistryBuilder().register(
        [_review_api_skill(), _review_ui_skill(), _delivery_brief_skill()]
    )

    candidates = CandidateGenerator().generate(registry)
    by_pair = {(candidate.source_id, candidate.target_id): candidate for candidate in candidates}

    review_api_pair = by_pair[("review_api", "delivery_brief")]
    assert "produces" in review_api_pair.relation_hints
    assert "aggregates" in review_api_pair.relation_hints

    review_ui_pair = by_pair[("review_ui", "delivery_brief")]
    assert "produces" in review_ui_pair.relation_hints
    assert "aggregates" in review_ui_pair.relation_hints

    dependency_pair = by_pair[("review_api", "delivery_brief")]
    assert "depends_on" in dependency_pair.relation_hints


def test_candidate_generator_ignores_generic_exact_io_names() -> None:
    registry = SkillRegistryBuilder().register(
        [
            SkillRepresentation(
                id="report_writer",
                name="Report Writer",
                description="Write a review report.",
                version="1.0.0",
                tasks=["review"],
                inputs=[ParameterSpec(name="topic", type="text")],
                outputs=[ArtifactSpec(name="review_report", type="markdown")],
                preconditions=[],
                postconditions=[],
            ),
            SkillRepresentation(
                id="report_reviewer",
                name="Report Reviewer",
                description="Review a prior report.",
                version="1.0.0",
                tasks=["review"],
                inputs=[ParameterSpec(name="review_report", type="markdown")],
                outputs=[ArtifactSpec(name="score", type="json")],
                preconditions=[],
                postconditions=[],
            ),
        ]
    )

    candidates = CandidateGenerator().generate(registry)

    assert not [
        candidate
        for candidate in candidates
        if "exact_io_match" in candidate.candidate_methods
    ]


def test_candidate_generator_ignores_high_fanout_text_terms() -> None:
    registry = SkillRegistryBuilder().register(
        [
            SkillRepresentation(
                id=f"shared_{index}",
                name=f"Shared {index}",
                description="Commonterm capability.",
                version="1.0.0",
                tasks=[f"task_{index}"],
                inputs=[ParameterSpec(name=f"input_{index}", type="text")],
                outputs=[ArtifactSpec(name=f"output_{index}", type="json")],
                preconditions=[],
                postconditions=[],
            )
            for index in range(3)
        ]
    )

    candidates = CandidateGenerator(max_text_term_bucket_size=2).generate(registry)

    assert not [
        candidate
        for candidate in candidates
        if "text_term_match" in candidate.candidate_methods
    ]


def test_index_builder_omits_generic_io_names_and_stop_terms() -> None:
    registry = SkillRegistryBuilder().register(
        [_web_search_skill(), _summarize_skill(), _generic_report_skill()]
    )
    graph = GraphBuilder(matcher=AcceptingMatcher()).build(
        [_web_search_skill(), _summarize_skill(), _generic_report_skill()]
    ).graph

    index = SkillIndexBuilder().build(registry, graph)

    assert "search_results" in index.by_output
    assert "review_report" not in index.by_output
    assert "review_report" not in index.by_input
    assert "and" not in index.by_text_term


def test_validate_llm_matches_accepts_candidate_backed_match() -> None:
    registry = SkillRegistryBuilder().register(
        [_web_search_skill(), _summarize_skill()]
    )
    candidates = CandidateGenerator().generate(registry)
    candidate = next(
        item
        for item in candidates
        if item.source_id == "web_search"
        and item.target_id == "summarize_text"
        and "can_feed" in item.relation_hints
    )

    matches, diagnostics = validate_llm_matches(
        {
            "matches": [
                {
                    "candidate_id": candidate.key,
                    "source_id": "web_search",
                    "target_id": "summarize_text",
                    "relation_type": "can_feed",
                    "confidence": 0.91,
                    "method": "llm_ontology_match",
                    "reasons": ["search_results can feed the summarizer."],
                    "supporting_fields": {
                        "source_outputs": ["search_results"],
                        "target_inputs": ["search_results"],
                    },
                }
            ]
        },
        registry,
        [candidate],
    )

    assert diagnostics == []
    assert len(matches) == 1
    assert matches[0].accepted is True
    assert matches[0].candidate_id == candidate.key


def test_validate_llm_matches_parses_decorated_supporting_field_strings() -> None:
    registry = SkillRegistryBuilder().register(
        [_web_search_skill(), _summarize_skill()]
    )
    candidates = CandidateGenerator().generate(registry)
    candidate = next(
        item
        for item in candidates
        if item.source_id == "web_search"
        and item.target_id == "summarize_text"
        and "can_feed" in item.relation_hints
    )

    matches, diagnostics = validate_llm_matches(
        {
            "matches": [
                {
                    "candidate_id": candidate.key,
                    "source_id": "web_search",
                    "target_id": "summarize_text",
                    "relation_type": "can_feed",
                    "confidence": 0.91,
                    "reasons": ["decorated fields should still validate."],
                    "supporting_fields": {
                        "source_outputs": ["search_results (json): web hits"],
                        "target_inputs": ["search_results: search hits"],
                    },
                }
            ]
        },
        registry,
        [candidate],
    )

    assert diagnostics == []
    assert len(matches) == 1
    assert matches[0].accepted is True


def test_graph_builder_adds_deterministic_exact_io_edges() -> None:
    class EmptyMatcher:
        def match(self, registry, candidates):
            return []

    result = GraphBuilder(matcher=EmptyMatcher()).build(
        [_web_search_skill(), _summarize_skill()]
    )

    edge_types = {(edge.source, edge.target, edge.type, edge.method) for edge in result.graph.edges}
    assert (
        "skill:web_search",
        "skill:summarize_text",
        "can_feed",
        "deterministic_exact_io_match",
    ) in edge_types


def test_graph_builder_mirrors_similar_edges_but_keeps_substitute_directional() -> None:
    class RelationMatcher:
        def match(self, registry, candidates):
            return [
                LLMMatch(
                    source_id="web_search",
                    target_id="summarize_text",
                    relation_type="similar_to",
                    confidence=0.7,
                    accepted=True,
                ),
                LLMMatch(
                    source_id="web_search",
                    target_id="summarize_text",
                    relation_type="substitute_for",
                    confidence=0.8,
                    accepted=True,
                ),
            ]

    result = GraphBuilder(matcher=RelationMatcher()).build(
        [_web_search_skill(), _summarize_skill()]
    )
    edge_types = {(edge.source, edge.target, edge.type) for edge in result.graph.edges}
    assert ("skill:web_search", "skill:summarize_text", "similar_to") in edge_types
    assert ("skill:summarize_text", "skill:web_search", "similar_to") in edge_types
    assert ("skill:web_search", "skill:summarize_text", "substitute_for") in edge_types
    assert ("skill:summarize_text", "skill:web_search", "substitute_for") not in edge_types


def test_graph_builder_pipeline_writes_expected_artifacts(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=AcceptingMatcher()).build(
        [_web_search_skill(), _summarize_skill()]
    )

    assert result.index.by_output["search_results"] == ["web_search"]
    assert result.index.by_input["search_results"] == ["summarize_text"]
    assert result.index.neighbors["web_search"] == ["summarize_text"]

    edge_types = {(edge.source, edge.target, edge.type) for edge in result.graph.edges}
    assert ("skill:web_search", "skill:summarize_text", "can_feed") in edge_types
    assert "skill" in {node.type for node in result.graph.nodes}
    web_search_node = next(
        node for node in result.graph.nodes if node.id == "skill:web_search"
    )
    assert web_search_node.properties["outputs"][0]["name"] == "search_results"

    write_graph_build_result(result, tmp_path)

    assert (tmp_path / "build_manifest.json").exists()
    assert (tmp_path / "skills.json").exists()
    assert (tmp_path / "skill_graph.json").exists()
    assert (tmp_path / "skill_index.json").exists()
    assert (tmp_path / "llm_matches.json").exists()
    assert (tmp_path / "diagnostics.json").exists()


def test_graph_builder_adds_artifact_and_slot_nodes_with_structured_edges() -> None:
    result = GraphBuilder(matcher=AcceptingMatcher()).build(
        [_review_api_skill(), _review_ui_skill(), _delivery_brief_skill()]
    )

    node_by_id = {node.id: node for node in result.graph.nodes}
    assert "artifact:api_spec:yaml" in node_by_id
    assert "slot:security_findings" in node_by_id
    assert "slot:design_review_findings" in node_by_id

    edge_types = {(edge.source, edge.target, edge.type) for edge in result.graph.edges}
    assert ("skill:review_api", "artifact:review_report:markdown", "produces") in edge_types
    assert ("artifact:api_spec:yaml", "skill:review_api", "consumes") in edge_types
    assert ("skill:review_api", "slot:security_findings", "produces") in edge_types
    assert ("slot:security_findings", "skill:delivery_brief", "aggregates") in edge_types
    assert ("skill:review_api", "skill:delivery_brief", "depends_on") in edge_types


def test_index_builder_tracks_slot_and_aggregator_indexes() -> None:
    result = GraphBuilder(matcher=AcceptingMatcher()).build(
        [_review_api_skill(), _review_ui_skill(), _delivery_brief_skill()]
    )
    index = result.index

    assert index.by_slot["security_findings"] == ["review_api"]
    assert index.by_slot["design_review_findings"] == ["review_ui"]
    assert index.by_aggregator["security_findings"] == ["delivery_brief"]
    assert index.by_artifact["api_spec"] == ["review_api"]


def test_graph_builder_uses_match_diagnostics_without_matcher_diagnostics() -> None:
    class MatchWithDiagnosticsMatcher:
        def match(self, registry, candidates):
            return [
                LLMMatch(
                    source_id="web_search",
                    target_id="summarize_text",
                    relation_type="can_feed",
                    confidence=0.8,
                    method="test_matcher",
                    accepted=True,
                    diagnostics=["output type mismatch risk"],
                )
            ]

    result = GraphBuilder(matcher=MatchWithDiagnosticsMatcher()).build(
        [_web_search_skill(), _summarize_skill()]
    )

    assert any(
        diagnostic.code == "match_diagnostic"
        and "mismatch" in diagnostic.message
        for diagnostic in result.diagnostics
    )


def test_graph_builder_prefers_matcher_level_diagnostics() -> None:
    class MatcherWithOwnDiagnostics:
        diagnostics = []

        def match(self, registry, candidates):
            self.diagnostics = [
                GraphDiagnostic(
                    stage="llm_match",
                    severity="info",
                    code="matcher_info",
                    message="matcher emitted own diagnostics",
                )
            ]
            return [
                LLMMatch(
                    source_id="web_search",
                    target_id="summarize_text",
                    relation_type="can_feed",
                    confidence=0.8,
                    method="test_matcher",
                    accepted=True,
                    diagnostics=["should not be surfaced when matcher has diagnostics"],
                )
            ]

    result = GraphBuilder(matcher=MatcherWithOwnDiagnostics()).build(
        [_web_search_skill(), _summarize_skill()]
    )

    assert any(
        diagnostic.code == "matcher_info"
        for diagnostic in result.diagnostics
    )
    assert not any(
        getattr(diagnostic, "code", "") == "match_diagnostic"
        for diagnostic in result.diagnostics
    )


def _web_search_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="web_search",
        name="Web Search",
        description="Search the web and return relevant results.",
        version="1.0.0",
        tasks=["search"],
        inputs=[ParameterSpec(name="topic", type="text")],
        outputs=[ArtifactSpec(name="search_results", type="json")],
        preconditions=[],
        postconditions=[],
    )


def _summarize_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="summarize_text",
        name="Summarize Text",
        description="Summarize search results into a concise summary.",
        version="1.0.0",
        tasks=["summarize"],
        inputs=[ParameterSpec(name="search_results", type="json")],
        outputs=[ArtifactSpec(name="summary", type="markdown")],
        preconditions=[],
        postconditions=[],
    )


def _generic_report_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="generic_report",
        name="Generic Report",
        description="Read and write a generic review report.",
        version="1.0.0",
        tasks=["review"],
        inputs=[ParameterSpec(name="review_report", type="markdown")],
        outputs=[ArtifactSpec(name="review_report", type="markdown")],
        preconditions=[],
        postconditions=[],
    )


def _review_api_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="review_api",
        name="Review API",
        description="Review API security and produce findings.",
        version="1.0.0",
        tasks=["review", "audit"],
        inputs=[ParameterSpec(name="api_spec", type="yaml")],
        outputs=[ArtifactSpec(name="review_report", type="markdown")],
        emits_slots=["security_findings"],
        preconditions=[],
        postconditions=[],
    )


def _review_ui_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="review_ui",
        name="Review UI",
        description="Review UI design and accessibility findings.",
        version="1.0.0",
        tasks=["review", "analyze"],
        inputs=[ParameterSpec(name="ui_prototype", type="image")],
        outputs=[ArtifactSpec(name="review_report", type="markdown")],
        emits_slots=["design_review_findings"],
        preconditions=[],
        postconditions=[],
    )


def _delivery_brief_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="delivery_brief",
        name="Delivery Brief",
        description="Aggregate findings and produce brief.",
        version="1.0.0",
        tasks=["synthesize", "orchestrate"],
        inputs=[ParameterSpec(name="constraints", type="text", required=False)],
        outputs=[ArtifactSpec(name="delivery_brief", type="markdown")],
        consumes_slots=["security_findings", "design_review_findings"],
        emits_slots=["delivery_brief"],
        preconditions=[
            Condition(type="depends_on_skill", expression="review_api"),
        ],
        postconditions=[],
    )
