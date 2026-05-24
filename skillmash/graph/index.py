"""Build online retrieval indexes for Skill graphs."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Set

from skillmash.lexicon import (
    ArtifactLexicon,
    DEFAULT_GRAPH_INDEX_GENERIC_IO_NAMES,
    DEFAULT_GRAPH_STOP_TERMS,
)
from skillmash.graph.models import SkillGraph, SkillIndex, SkillRegistry


_GRAPH_TERM_LEXICON = ArtifactLexicon.create(
    stop_terms=DEFAULT_GRAPH_STOP_TERMS,
    min_token_length=3,
)


class SkillIndexBuilder:
    """Build deterministic inverted and adjacency indexes."""

    def __init__(
        self,
        *,
        generic_io_names: Iterable[str] = DEFAULT_GRAPH_INDEX_GENERIC_IO_NAMES,
        max_io_bucket_size: int = 16,
        max_text_bucket_size: int = 24,
    ) -> None:
        self.lexicon = ArtifactLexicon.create(
            stop_terms=DEFAULT_GRAPH_STOP_TERMS,
            min_token_length=3,
            generic_io_names=generic_io_names,
        )
        self.max_io_bucket_size = max(1, max_io_bucket_size)
        self.max_text_bucket_size = max(2, max_text_bucket_size)

    def build(self, registry: SkillRegistry, graph: SkillGraph) -> SkillIndex:
        by_output: Dict[str, Set[str]] = defaultdict(set)
        by_input: Dict[str, Set[str]] = defaultdict(set)
        by_task: Dict[str, Set[str]] = defaultdict(set)
        by_data_type: Dict[str, Set[str]] = defaultdict(set)
        by_text_term: Dict[str, Set[str]] = defaultdict(set)
        by_slot: Dict[str, Set[str]] = defaultdict(set)
        by_artifact: Dict[str, Set[str]] = defaultdict(set)
        by_aggregator: Dict[str, Set[str]] = defaultdict(set)

        for skill in registry.ordered_skills():
            for output in skill.outputs:
                if not self.lexicon.is_generic_io_name(output.name):
                    by_output[output.name].add(skill.id)
                by_data_type[output.type].add(skill.id)
            for parameter in skill.inputs:
                if not self.lexicon.is_generic_io_name(parameter.name):
                    by_input[parameter.name].add(skill.id)
                by_data_type[parameter.type].add(skill.id)
            for task in skill.tasks:
                by_task[task].add(skill.id)
            for term in _skill_terms(skill):
                by_text_term[term].add(skill.id)

        neighbors: Dict[str, Set[str]] = defaultdict(set)
        upstream_by_input: Dict[str, Set[str]] = defaultdict(set)
        downstream_by_output: Dict[str, Set[str]] = defaultdict(set)
        skill_inputs = {
            skill.id: {parameter.name for parameter in skill.inputs}
            for skill in registry.ordered_skills()
        }
        skill_outputs = {
            skill.id: {output.name for output in skill.outputs}
            for skill in registry.ordered_skills()
        }

        for edge in graph.edges:
            if edge.source.startswith("skill:") and edge.target.startswith("skill:"):
                source_id = edge.source.removeprefix("skill:")
                target_id = edge.target.removeprefix("skill:")
                neighbors[source_id].add(target_id)
                if edge.type == "can_feed":
                    shared = sorted(
                        skill_outputs.get(source_id, set())
                        & skill_inputs.get(target_id, set())
                    )
                    for name in shared:
                        if self.lexicon.is_generic_io_name(name):
                            continue
                        upstream_by_input[name].add(source_id)
                        downstream_by_output[name].add(target_id)

            if edge.type == "produces" and edge.source.startswith("skill:"):
                source_id = edge.source.removeprefix("skill:")
                if edge.target.startswith("slot:"):
                    slot_name = edge.target.removeprefix("slot:")
                    by_slot[slot_name].add(source_id)
                if edge.target.startswith("artifact:"):
                    artifact_name = _artifact_name_from_id(edge.target)
                    if artifact_name:
                        by_artifact[artifact_name].add(source_id)

            if edge.type == "aggregates" and edge.source.startswith("slot:") and edge.target.startswith("skill:"):
                slot_name = edge.source.removeprefix("slot:")
                target_id = edge.target.removeprefix("skill:")
                by_aggregator[slot_name].add(target_id)

            if edge.type == "consumes" and edge.source.startswith("artifact:") and edge.target.startswith("skill:"):
                artifact_name = _artifact_name_from_id(edge.source)
                if artifact_name:
                    by_artifact[artifact_name].add(edge.target.removeprefix("skill:"))

        return SkillIndex(
            by_output=_freeze_index(
                by_output,
                max_bucket_size=self.max_io_bucket_size,
            ),
            by_input=_freeze_index(
                by_input,
                max_bucket_size=self.max_io_bucket_size,
            ),
            by_task=_freeze_index(by_task),
            by_data_type=_freeze_index(by_data_type),
            neighbors=_freeze_index(neighbors),
            upstream_by_input=_freeze_index(
                upstream_by_input,
                max_bucket_size=self.max_io_bucket_size,
            ),
            downstream_by_output=_freeze_index(
                downstream_by_output,
                max_bucket_size=self.max_io_bucket_size,
            ),
            by_text_term=_freeze_index(
                by_text_term,
                max_bucket_size=self.max_text_bucket_size,
            ),
            by_slot=_freeze_index(by_slot),
            by_artifact=_freeze_index(by_artifact),
            by_aggregator=_freeze_index(by_aggregator),
        )


def _freeze_index(
    index: Dict[str, Set[str]],
    *,
    max_bucket_size: int | None = None,
) -> Dict[str, List[str]]:
    frozen = {}
    for key, values in sorted(index.items()):
        if max_bucket_size is not None and len(values) > max_bucket_size:
            continue
        frozen[key] = sorted(values)
    return frozen


def _skill_terms(skill) -> Set[str]:
    chunks: List[str] = [skill.id, skill.name, skill.description]
    chunks.extend(skill.tasks)
    chunks.extend(item.name for item in skill.inputs)
    chunks.extend(item.description for item in skill.inputs)
    chunks.extend(item.name for item in skill.outputs)
    chunks.extend(item.description for item in skill.outputs)
    terms: Set[str] = set()
    for chunk in chunks:
        terms.update(_tokenize(chunk))
    return terms


def _tokenize(text: str) -> Set[str]:
    return _GRAPH_TERM_LEXICON.tokenize(text)


def _artifact_name_from_id(node_id: str) -> str:
    parts = node_id.split(":", 2)
    if len(parts) < 3:
        return ""
    return parts[1]
