"""Offline graph construction orchestration."""

from __future__ import annotations

from typing import Iterable, Optional

from skillmash.graph.builder import SkillGraphBuilder
from skillmash.graph.candidates import CandidateGenerator
from skillmash.graph.index import SkillIndexBuilder
from skillmash.graph.matcher import DEFAULT_THRESHOLDS, OntologyMatcher
from skillmash.graph.models import BuildManifest, GraphBuildResult, GraphDiagnostic, LLMMatch
from skillmash.graph.registry import SkillRegistryBuilder
from skillmash.representation.models import SkillRepresentation


class GraphBuilder:
    """Build Skill graph artifacts from normalized Skill representations."""

    def __init__(
        self,
        *,
        matcher: OntologyMatcher,
        registry_builder: Optional[SkillRegistryBuilder] = None,
        candidate_generator: Optional[CandidateGenerator] = None,
        graph_builder: Optional[SkillGraphBuilder] = None,
        index_builder: Optional[SkillIndexBuilder] = None,
    ) -> None:
        self.matcher = matcher
        self.registry_builder = registry_builder or SkillRegistryBuilder()
        self.candidate_generator = candidate_generator or CandidateGenerator()
        self.graph_builder = graph_builder or SkillGraphBuilder()
        self.index_builder = index_builder or SkillIndexBuilder()

    def build(
        self,
        representations: Iterable[SkillRepresentation],
    ) -> GraphBuildResult:
        diagnostics: list[GraphDiagnostic] = []
        registry = self.registry_builder.register(representations)
        diagnostics.extend(registry.diagnostics)

        candidates = self.candidate_generator.generate(registry)
        llm_matches = self.matcher.match(registry, candidates)
        llm_matches.extend(_deterministic_exact_io_matches(candidates))
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

        graph = self.graph_builder.build(registry, llm_matches)
        index = self.index_builder.build(registry, graph)
        manifest = BuildManifest(
            thresholds=_matcher_thresholds(self.matcher),
            llm=_matcher_metadata(self.matcher),
        )

        return GraphBuildResult(
            manifest=manifest,
            skills=registry.ordered_skills(),
            candidates=candidates,
            llm_matches=llm_matches,
            graph=graph,
            index=index,
            diagnostics=diagnostics,
        )


def _matcher_metadata(matcher: OntologyMatcher) -> dict:
    if hasattr(matcher, "manifest_metadata"):
        return matcher.manifest_metadata()
    return {"matcher": matcher.__class__.__name__}


def _matcher_thresholds(matcher: OntologyMatcher) -> dict:
    if hasattr(matcher, "thresholds"):
        return dict(matcher.thresholds)
    return dict(DEFAULT_THRESHOLDS)


def _deterministic_exact_io_matches(candidates) -> list[LLMMatch]:
    matches: list[LLMMatch] = []
    for candidate in candidates:
        if "exact_io_match" not in candidate.candidate_methods:
            continue
        if "can_feed" not in candidate.relation_hints:
            continue
        for direction, evidence in sorted(candidate.evidence.get("directions", {}).items()):
            matched_outputs, matched_inputs = _exact_io_fields(evidence)
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


def _exact_io_fields(evidence: dict) -> tuple[list[str], list[str]]:
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
