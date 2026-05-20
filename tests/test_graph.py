from __future__ import annotations

from pathlib import Path

from skillmash.graph import (
    CandidateGenerator,
    GraphBuilder,
    LLMMatch,
    SkillRegistryBuilder,
    validate_llm_matches,
    write_graph_build_result,
)
from skillmash.representation import ArtifactSpec, ParameterSpec, SkillRepresentation


class AcceptingMatcher:
    def match(self, registry, candidates):
        matches = []
        for candidate in candidates:
            if candidate.relation_hint != "can_feed":
                continue
            matches.append(
                LLMMatch(
                    source_id=candidate.source_id,
                    target_id=candidate.target_id,
                    relation_type=candidate.relation_hint,
                    confidence=0.95,
                    method="test_matcher",
                    reasons=["candidate accepted"],
                    supporting_fields={
                        "source_outputs": [
                            item["name"]
                            for item in candidate.evidence.get("source_outputs", [])
                        ],
                        "target_inputs": [
                            item["name"]
                            for item in candidate.evidence.get("target_inputs", [])
                        ],
                    },
                    candidate_id=candidate.key,
                    accepted=True,
                )
            )
        return matches


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
        and candidate.relation_hint == "can_feed"
    ]
    assert len(exact) == 1
    assert exact[0].candidate_method == "exact_io_match"
    assert exact[0].priority == "high"
    assert "search_results" in exact[0].evidence["matched_terms"]


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
        and item.relation_hint == "can_feed"
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


def test_graph_builder_pipeline_writes_expected_artifacts(tmp_path: Path) -> None:
    result = GraphBuilder(matcher=AcceptingMatcher()).build(
        [_web_search_skill(), _summarize_skill()]
    )

    assert result.index.by_output["search_results"] == ["web_search"]
    assert result.index.by_input["search_results"] == ["summarize_text"]
    assert result.index.neighbors["web_search"] == ["summarize_text"]

    edge_types = {(edge.source, edge.target, edge.type) for edge in result.graph.edges}
    assert ("skill:web_search", "skill:summarize_text", "can_feed") in edge_types
    assert ("skill:web_search", "artifact:search_results", "produces") in edge_types
    assert ("artifact:search_results", "skill:summarize_text", "consumes") in edge_types

    write_graph_build_result(result, tmp_path)

    assert (tmp_path / "build_manifest.json").exists()
    assert (tmp_path / "skills.json").exists()
    assert (tmp_path / "skill_graph.json").exists()
    assert (tmp_path / "skill_index.json").exists()
    assert (tmp_path / "llm_matches.json").exists()
    assert (tmp_path / "diagnostics.json").exists()


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
