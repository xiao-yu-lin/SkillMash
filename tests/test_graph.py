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
from skillmash.representation import (
    ArtifactSpec,
    ParameterSpec,
    SkillRepresentation,
)


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


def test_candidate_generator_ignores_generic_exact_io_names() -> None:
    registry = SkillRegistryBuilder().register(
        [
            SkillRepresentation(
                id="report_writer",
                name="Report Writer",
                description="Write a review report.",
                version="1.0.0",
                inputs=[ParameterSpec(name="topic", type="text")],
                outputs=[ArtifactSpec(name="review_report", type="markdown")],
            ),
            SkillRepresentation(
                id="report_reviewer",
                name="Report Reviewer",
                description="Review a prior report.",
                version="1.0.0",
                inputs=[ParameterSpec(name="review_report", type="markdown")],
                outputs=[ArtifactSpec(name="score", type="json")],
            ),
        ]
    )

    candidates = CandidateGenerator().generate(registry)

    assert not [
        candidate
        for candidate in candidates
        if "exact_io_match" in candidate.candidate_methods
    ]


def test_candidate_generator_allows_markdown_output_to_feed_text_input() -> None:
    registry = SkillRegistryBuilder().register(
        [
            SkillRepresentation(
                id="read_arxiv_paper",
                name="Read Arxiv Paper",
                description="Read an arxiv paper and produce a markdown summary.",
                version="1.0.0",
                inputs=[ParameterSpec(name="url", type="url")],
                outputs=[
                    ArtifactSpec(
                        name="summary",
                        type="markdown",
                        description="Markdown summary of the paper.",
                    )
                ],
            ),
            SkillRepresentation(
                id="tts",
                name="Text to Speech",
                description="Convert provided text into speech audio.",
                version="1.0.0",
                inputs=[
                    ParameterSpec(
                        name="text",
                        type="text",
                        description="Text content to convert to speech.",
                    )
                ],
                outputs=[ArtifactSpec(name="audio", type="audio")],
            ),
        ]
    )

    candidates = CandidateGenerator().generate(registry)

    compatible = [
        candidate
        for candidate in candidates
        if candidate.source_id == "read_arxiv_paper"
        and candidate.target_id == "tts"
        and "compatible_type_match" in candidate.candidate_methods
    ]

    assert len(compatible) == 1
    evidence = compatible[0].evidence["directions"]["read_arxiv_paper->tts"]
    assert evidence["matched_type"] == "markdown->text"


def test_candidate_generator_adds_content_port_mappings() -> None:
    registry = SkillRegistryBuilder().register(
        [
            _image_translation_skill(),
            _general_writing_skill(),
            _email_skill(),
        ]
    )

    candidates = CandidateGenerator().generate(registry)

    translation_to_writing = next(
        candidate
        for candidate in candidates
        if candidate.key == "general-writing<->xiaoyi-image-translation"
    )
    translation_evidence = translation_to_writing.evidence["directions"][
        "xiaoyi-image-translation->general-writing"
    ]
    translation_ports = {
        (mapping["source_output"], mapping["target_input"])
        for mapping in translation_evidence["port_mappings"]
    }
    assert ("translated_text", "query") in translation_ports
    assert ("ocr_text", "query") in translation_ports

    writing_to_email = next(
        candidate
        for candidate in candidates
        if candidate.key == "general-writing<->imap-smtp-email"
    )
    email_evidence = writing_to_email.evidence["directions"][
        "general-writing->imap-smtp-email"
    ]
    email_ports = {
        (mapping["source_output"], mapping["target_input"])
        for mapping in email_evidence["port_mappings"]
    }
    assert ("document", "body") in email_ports
    assert ("document", "command") not in email_ports
    assert len(email_evidence["port_mappings"]) <= 12


def test_candidate_generator_ignores_high_fanout_text_terms() -> None:
    registry = SkillRegistryBuilder().register(
        [
            SkillRepresentation(
                id=f"shared_{index}",
                name=f"Shared {index}",
                description="Commonterm capability.",
                version="1.0.0",
                inputs=[ParameterSpec(name=f"input_{index}", type="text")],
                outputs=[ArtifactSpec(name=f"output_{index}", type="json")],
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


def test_validate_llm_matches_accepts_candidate_port_mapping() -> None:
    registry = SkillRegistryBuilder().register(
        [_general_writing_skill(), _email_skill()]
    )
    candidates = CandidateGenerator().generate(registry)
    candidate = next(
        item
        for item in candidates
        if item.key == "general-writing<->imap-smtp-email"
    )

    matches, diagnostics = validate_llm_matches(
        {
            "matches": [
                {
                    "candidate_id": candidate.key,
                    "source_id": "general-writing",
                    "target_id": "imap-smtp-email",
                    "relation_type": "can_feed",
                    "confidence": 0.91,
                    "reasons": ["The generated document can be used as email body."],
                    "supporting_fields": {
                        "port_mappings": [
                            {
                                "source_output": "document",
                                "target_input": "body",
                            }
                        ],
                        "source_outputs": ["document"],
                        "target_inputs": ["body"],
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


def test_validate_llm_matches_rejects_unknown_port_mapping() -> None:
    registry = SkillRegistryBuilder().register(
        [_general_writing_skill(), _email_skill()]
    )
    candidates = CandidateGenerator().generate(registry)
    candidate = next(
        item
        for item in candidates
        if item.key == "general-writing<->imap-smtp-email"
    )

    matches, diagnostics = validate_llm_matches(
        {
            "matches": [
                {
                    "candidate_id": candidate.key,
                    "source_id": "general-writing",
                    "target_id": "imap-smtp-email",
                    "relation_type": "can_feed",
                    "confidence": 0.91,
                    "reasons": ["This field pair was not in the candidate."],
                    "supporting_fields": {
                        "port_mappings": [
                            {
                                "source_output": "document",
                                "target_input": "command",
                            }
                        ],
                        "source_outputs": ["document"],
                        "target_inputs": ["command"],
                    },
                }
            ]
        },
        registry,
        [candidate],
    )

    assert matches[0].accepted is False
    assert any(
        "port_mappings do not match candidate evidence"
        in diagnostic.details["errors"]
        for diagnostic in diagnostics
    )


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
        inputs=[ParameterSpec(name="topic", type="text")],
        outputs=[ArtifactSpec(name="search_results", type="json")],
    )


def _summarize_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="summarize_text",
        name="Summarize Text",
        description="Summarize search results into a concise summary.",
        version="1.0.0",
        inputs=[ParameterSpec(name="search_results", type="json")],
        outputs=[ArtifactSpec(name="summary", type="markdown")],
    )


def _generic_report_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="generic_report",
        name="Generic Report",
        description="Read and write a generic review report.",
        version="1.0.0",
        inputs=[ParameterSpec(name="review_report", type="markdown")],
        outputs=[ArtifactSpec(name="review_report", type="markdown")],
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
            ArtifactSpec(
                name="translated_text",
                type="text",
                description="Translated text recognized from the image.",
            ),
            ArtifactSpec(
                name="ocr_text",
                type="text",
                description="Original OCR text recognized from the image.",
            ),
        ],
    )


def _general_writing_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="general-writing",
        name="General Writing",
        description="Produce a written markdown document from a user request.",
        version="1.0.0",
        inputs=[
            ParameterSpec(
                name="query",
                type="text",
                description="The user's writing request or topic.",
            ),
            ParameterSpec(
                name="sources",
                type="json",
                required=False,
                description="Source material used to support the writing.",
            ),
        ],
        outputs=[
            ArtifactSpec(
                name="document",
                type="markdown",
                description="Written markdown document.",
            )
        ],
    )


def _email_skill() -> SkillRepresentation:
    return SkillRepresentation(
        id="imap-smtp-email",
        name="IMAP SMTP Email",
        description="Read and send email via IMAP and SMTP.",
        version="1.0.0",
        inputs=[
            ParameterSpec(
                name="command",
                type="text",
                description="Operation to perform: check, fetch, send.",
            ),
            ParameterSpec(
                name="to",
                type="text",
                required=False,
                description="Recipient email addresses.",
            ),
            ParameterSpec(
                name="body",
                type="text",
                required=False,
                description="Plain text or HTML email body content.",
            ),
        ],
        outputs=[ArtifactSpec(name="confirmation", type="text")],
    )


