"""Offline graph construction orchestration."""

from __future__ import annotations

from typing import Iterable, Optional

from skillmash.graph.builder import SkillGraphBuilder
from skillmash.graph.candidates import CandidateGenerator
from skillmash.graph.index import SkillIndexBuilder
from skillmash.graph.matcher import DEFAULT_THRESHOLDS, OntologyMatcher
from skillmash.graph.models import BuildManifest, GraphBuildResult, GraphDiagnostic
from skillmash.graph.relation_resolution import RelationResolver
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
        relation_resolver: Optional[RelationResolver] = None,
    ) -> None:
        self.matcher = matcher
        self.registry_builder = registry_builder or SkillRegistryBuilder()
        self.candidate_generator = candidate_generator or CandidateGenerator()
        self.graph_builder = graph_builder or SkillGraphBuilder()
        self.index_builder = index_builder or SkillIndexBuilder()
        self.relation_resolver = relation_resolver or RelationResolver(matcher=matcher)

    def build(
        self,
        representations: Iterable[SkillRepresentation],
    ) -> GraphBuildResult:
        diagnostics: list[GraphDiagnostic] = []
        registry = self.registry_builder.register(representations)
        diagnostics.extend(registry.diagnostics)

        candidates = self.candidate_generator.generate(registry)
        llm_matches, relation_diagnostics = self.relation_resolver.resolve(
            registry,
            candidates,
        )
        diagnostics.extend(relation_diagnostics)

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
