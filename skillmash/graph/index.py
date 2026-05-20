"""Build online retrieval indexes for Skill graphs."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Set

from skillmash.graph.models import SkillGraph, SkillIndex, SkillRegistry


class SkillIndexBuilder:
    """Build deterministic inverted and adjacency indexes."""

    def build(self, registry: SkillRegistry, graph: SkillGraph) -> SkillIndex:
        by_output: Dict[str, Set[str]] = defaultdict(set)
        by_input: Dict[str, Set[str]] = defaultdict(set)
        by_task: Dict[str, Set[str]] = defaultdict(set)
        by_data_type: Dict[str, Set[str]] = defaultdict(set)
        by_text_term: Dict[str, Set[str]] = defaultdict(set)

        for skill in registry.ordered_skills():
            for output in skill.outputs:
                by_output[output.name].add(skill.id)
                by_data_type[output.type].add(skill.id)
            for parameter in skill.inputs:
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
            if not edge.source.startswith("skill:") or not edge.target.startswith("skill:"):
                continue
            source_id = edge.source.removeprefix("skill:")
            target_id = edge.target.removeprefix("skill:")
            neighbors[source_id].add(target_id)
            if edge.type == "can_feed":
                shared = sorted(skill_outputs.get(source_id, set()) & skill_inputs.get(target_id, set()))
                for name in shared:
                    upstream_by_input[name].add(source_id)
                    downstream_by_output[name].add(target_id)

        return SkillIndex(
            by_output=_freeze_index(by_output),
            by_input=_freeze_index(by_input),
            by_task=_freeze_index(by_task),
            by_data_type=_freeze_index(by_data_type),
            neighbors=_freeze_index(neighbors),
            upstream_by_input=_freeze_index(upstream_by_input),
            downstream_by_output=_freeze_index(downstream_by_output),
            by_text_term=_freeze_index(by_text_term),
        )


def _freeze_index(index: Dict[str, Set[str]]) -> Dict[str, List[str]]:
    return {key: sorted(values) for key, values in sorted(index.items())}


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
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(text).lower())
        if len(token) >= 3
    }
