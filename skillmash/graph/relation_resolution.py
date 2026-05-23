"""Centralized relation resolution for graph building."""

from __future__ import annotations

from typing import Iterable

from skillmash.graph.matcher import OntologyMatcher
from skillmash.graph.models import GraphDiagnostic, LLMMatch, RelationCandidate, SkillRegistry


class RelationResolver:
    """Resolve final relation matches from candidates and matcher output."""

    def __init__(self, *, matcher: OntologyMatcher) -> None:
        self.matcher = matcher

    def resolve(
        self,
        registry: SkillRegistry,
        candidates: Iterable[RelationCandidate],
    ) -> tuple[list[LLMMatch], list[GraphDiagnostic]]:
        candidate_list = list(candidates)
        llm_matches = self.matcher.match(registry, candidate_list)
        llm_matches.extend(deterministic_exact_io_matches(candidate_list))

        diagnostics: list[GraphDiagnostic] = []
        matcher_diagnostics = []
        if hasattr(self.matcher, "diagnostics"):
            matcher_diagnostics = list(self.matcher.diagnostics)
            diagnostics.extend(matcher_diagnostics)

        for match in llm_matches:
            if matcher_diagnostics:
                continue
            for message in match.diagnostics:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="llm_match",
                        severity="warning",
                        code="match_diagnostic",
                        message=message,
                        skill_id=match.source_id,
                        details={"match": match.to_dict()},
                    )
                )

        return llm_matches, diagnostics


def deterministic_exact_io_matches(
    candidates: Iterable[RelationCandidate],
) -> list[LLMMatch]:
    matches: list[LLMMatch] = []
    for candidate in candidates:
        if "exact_io_match" not in candidate.candidate_methods:
            continue
        if "can_feed" not in candidate.relation_hints:
            continue
        for direction, evidence in sorted(candidate.evidence.get("directions", {}).items()):
            matched_outputs, matched_inputs = exact_io_fields(evidence)
            if not matched_outputs or not matched_inputs:
                continue
            source_id, target_id = direction.split("->", 1)
            matches.append(
                LLMMatch(
                    source_id=source_id,
                    target_id=target_id,
                    relation_type="can_feed",
                    confidence=1.0,
                    method="deterministic_exact_io_match",
                    reasons=[
                        "Source output and target input share the same normalized name."
                    ],
                    supporting_fields={
                        "source_outputs": matched_outputs,
                        "target_inputs": matched_inputs,
                    },
                    candidate_id=candidate.key,
                    accepted=True,
                )
            )
    return matches


def exact_io_fields(evidence: dict) -> tuple[list[str], list[str]]:
    outputs = [
        item
        for item in evidence.get("source_outputs", [])
        if isinstance(item, dict) and item.get("name") and item.get("type")
    ]
    inputs = [
        item
        for item in evidence.get("target_inputs", [])
        if isinstance(item, dict) and item.get("name") and item.get("type")
    ]
    output_names: list[str] = []
    input_names: list[str] = []
    for output in outputs:
        for input_item in inputs:
            if output["name"] != input_item["name"]:
                continue
            if output["type"] != input_item["type"]:
                continue
            output_names.append(str(output["name"]))
            input_names.append(str(input_item["name"]))
    return sorted(set(output_names)), sorted(set(input_names))
