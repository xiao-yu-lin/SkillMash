from __future__ import annotations

import logging

from skillmash.graph import CandidateGenerator, SkillRegistryBuilder
from skillmash.representation.models import ArtifactSpec, ParameterSpec, SkillRepresentation


def test_candidate_generator_debug_logs_generated_candidates(caplog) -> None:
    registry = SkillRegistryBuilder().register(
        [
            SkillRepresentation(
                id="web_search",
                name="Web Search",
                description="Search the web and return relevant results.",
                version="1.0.0",
                inputs=[ParameterSpec(name="topic", type="text")],
                outputs=[ArtifactSpec(name="search_results", type="json")],
            ),
            SkillRepresentation(
                id="summarize_text",
                name="Summarize Text",
                description="Summarize search results into a concise summary.",
                version="1.0.0",
                inputs=[ParameterSpec(name="search_results", type="json")],
                outputs=[ArtifactSpec(name="summary", type="markdown")],
            ),
        ]
    )

    with caplog.at_level(logging.DEBUG, logger="skillmash.graph.candidates"):
        CandidateGenerator().generate(registry)

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "skillmash.graph.candidates"
    ]
    assert any(
        "candidate_generated" in message
        and "source=web_search" in message
        and "target=summarize_text" in message
        and "methods=exact_io_match" in message
        and "relation_hints=can_feed" in message
        and "matched_terms=search_results" in message
        for message in messages
    )
