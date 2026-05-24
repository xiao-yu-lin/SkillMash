"""Deterministic relation candidate generation."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, MutableMapping, Set, Tuple

from skillmash.graph.models import ALLOWED_RELATION_TYPES, RelationCandidate, SkillRegistry
from skillmash.lexicon import (
    ArtifactLexicon,
    DEFAULT_GRAPH_CANDIDATE_GENERIC_IO_NAMES,
    DEFAULT_GRAPH_STOP_TERMS,
)
from skillmash.representation.models import ArtifactSpec, ParameterSpec, SkillRepresentation


PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}
CAN_FEED_WEAK_TERMS = frozenset(
    {
        "analysis",
        "analyze",
        "config",
        "configuration",
        "dependencies",
        "document",
        "findings",
        "input",
        "output",
        "parallel",
        "report",
        "review",
        "role",
        "team",
    }
)
_GRAPH_TERM_LEXICON = ArtifactLexicon.create(
    stop_terms=DEFAULT_GRAPH_STOP_TERMS,
    min_token_length=3,
)


class CandidateGenerator:
    """Generate high-recall Skill-Skill relation candidates."""

    def __init__(
        self,
        *,
        max_candidates_per_skill_relation: int = 12,
        generic_io_names: Iterable[str] = DEFAULT_GRAPH_CANDIDATE_GENERIC_IO_NAMES,
        max_exact_io_pair_fanout: int = 64,
        max_text_term_bucket_size: int = 12,
    ) -> None:
        self.max_candidates_per_skill_relation = max_candidates_per_skill_relation
        self.lexicon = ArtifactLexicon.create(
            stop_terms=DEFAULT_GRAPH_STOP_TERMS,
            min_token_length=3,
            generic_io_names=generic_io_names,
        )
        self.max_exact_io_pair_fanout = max(1, max_exact_io_pair_fanout)
        self.max_text_term_bucket_size = max(2, max_text_term_bucket_size)

    def generate(self, registry: SkillRegistry) -> List[RelationCandidate]:
        skills = registry.ordered_skills()
        indexes = _CandidateIndexes.from_skills(skills)
        candidates: Dict[Tuple[str, str], RelationCandidate] = {}

        self._add_exact_io_candidates(indexes, candidates)
        self._add_slot_flow_candidates(skills, candidates)
        self._add_compatible_type_candidates(skills, candidates)
        self._add_task_overlap_candidates(indexes, candidates)
        self._add_shape_similarity_candidates(skills, candidates)
        self._add_text_term_candidates(indexes, candidates)

        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                item.source_id,
                -PRIORITY_RANK.get(item.priority, 0),
                item.target_id,
                ",".join(item.relation_hints),
            ),
        )
        return self._limit_per_skill_relation(ordered)

    def _add_exact_io_candidates(
        self,
        indexes: "_CandidateIndexes",
        candidates: MutableMapping[Tuple[str, str], RelationCandidate],
    ) -> None:
        for name in sorted(set(indexes.by_output_name) & set(indexes.by_input_name)):
            if self.lexicon.is_generic_io_name(name):
                continue
            pair_fanout = (
                len(indexes.by_output_name[name]) * len(indexes.by_input_name[name])
            )
            if pair_fanout > self.max_exact_io_pair_fanout:
                continue
            for source_id in sorted(indexes.by_output_name[name]):
                for target_id in sorted(indexes.by_input_name[name]):
                    if source_id == target_id:
                        continue
                    self._merge_candidate(
                        candidates,
                        RelationCandidate(
                            source_id=source_id,
                            target_id=target_id,
                            relation_hints=["can_feed"],
                            candidate_methods=["exact_io_match"],
                            priority="high",
                            evidence={
                                "matched_terms": [name],
                                "source_outputs": [
                                    item.to_dict()
                                    for item in indexes.outputs_by_skill_name[
                                        (source_id, name)
                                    ]
                                ],
                                "target_inputs": [
                                    item.to_dict()
                                    for item in indexes.inputs_by_skill_name[
                                        (target_id, name)
                                    ]
                                ],
                            },
                        ),
                    )

    def _add_compatible_type_candidates(
        self,
        skills: List[SkillRepresentation],
        candidates: MutableMapping[Tuple[str, str], RelationCandidate],
    ) -> None:
        for source in skills:
            source_terms = _skill_terms(source)
            for target in skills:
                if source.id == target.id:
                    continue
                target_terms = _skill_terms(target)
                shared_terms = sorted(source_terms & target_terms)
                for output in source.outputs:
                    for parameter in target.inputs:
                        if output.type != parameter.type:
                            continue
                        if output.type == "unknown":
                            continue
                        if not _can_feed_by_field_overlap(output, parameter):
                            continue
                        self._merge_candidate(
                            candidates,
                            RelationCandidate(
                                source_id=source.id,
                                target_id=target.id,
                                relation_hints=["can_feed"],
                                candidate_methods=["compatible_type_match"],
                                priority="medium",
                                evidence={
                                    "matched_terms": shared_terms,
                                    "source_outputs": [output.to_dict()],
                                    "target_inputs": [parameter.to_dict()],
                                    "matched_type": output.type,
                                },
                            ),
                        )

    def _add_slot_flow_candidates(
        self,
        skills: List[SkillRepresentation],
        candidates: MutableMapping[Tuple[str, str], RelationCandidate],
    ) -> None:
        producers_by_slot: Dict[str, Set[str]] = defaultdict(set)
        consumers_by_slot: Dict[str, Set[str]] = defaultdict(set)
        skill_by_id = {skill.id: skill for skill in skills}

        for skill in skills:
            for slot_name in getattr(skill, "emits_slots", []) or []:
                if slot_name:
                    producers_by_slot[str(slot_name)].add(skill.id)
            for slot_name in getattr(skill, "consumes_slots", []) or []:
                if slot_name:
                    consumers_by_slot[str(slot_name)].add(skill.id)

        for slot_name in sorted(set(producers_by_slot) & set(consumers_by_slot)):
            for source_id in sorted(producers_by_slot[slot_name]):
                for target_id in sorted(consumers_by_slot[slot_name]):
                    if source_id == target_id:
                        continue
                    target = skill_by_id[target_id]
                    relation_hints = ["produces"]
                    relation_hints.append(
                        "aggregates"
                        if len(getattr(target, "consumes_slots", []) or []) > 1
                        else "consumes"
                    )
                    self._merge_candidate(
                        candidates,
                        RelationCandidate(
                            source_id=source_id,
                            target_id=target_id,
                            relation_hints=relation_hints,
                            candidate_methods=["slot_flow_match"],
                            priority="high",
                            evidence={
                                "slot_name": slot_name,
                                "source_emits_slots": [slot_name],
                                "target_consumes_slots": [slot_name],
                            },
                        ),
                    )

        for skill in skills:
            for condition in skill.preconditions:
                if condition.type != "depends_on_skill":
                    continue
                source_id = str(condition.expression or "").strip()
                if not source_id or source_id not in skill_by_id or source_id == skill.id:
                    continue
                self._merge_candidate(
                    candidates,
                    RelationCandidate(
                        source_id=source_id,
                        target_id=skill.id,
                        relation_hints=["depends_on"],
                        candidate_methods=["explicit_precondition_match"],
                        priority="medium",
                        evidence={
                            "precondition_type": condition.type,
                            "precondition_expression": source_id,
                        },
                    ),
                )

    def _add_task_overlap_candidates(
        self,
        indexes: "_CandidateIndexes",
        candidates: MutableMapping[Tuple[str, str], RelationCandidate],
    ) -> None:
        for task, skill_ids in sorted(indexes.by_task.items()):
            ids = sorted(skill_ids)
            for source_id in ids:
                for target_id in ids:
                    if source_id >= target_id:
                        continue
                    for left, right in ((source_id, target_id), (target_id, source_id)):
                        self._merge_candidate(
                            candidates,
                            RelationCandidate(
                                source_id=left,
                                target_id=right,
                                relation_hints=["similar_to"],
                                candidate_methods=["task_overlap_match"],
                                priority="medium",
                                evidence={"shared_tasks": [task]},
                            ),
                        )

    def _add_shape_similarity_candidates(
        self,
        skills: List[SkillRepresentation],
        candidates: MutableMapping[Tuple[str, str], RelationCandidate],
    ) -> None:
        signatures = {skill.id: _io_signature(skill) for skill in skills}
        for source in skills:
            for target in skills:
                if source.id >= target.id:
                    continue
                shared_inputs = sorted(
                    signatures[source.id]["inputs"] & signatures[target.id]["inputs"]
                )
                shared_outputs = sorted(
                    signatures[source.id]["outputs"] & signatures[target.id]["outputs"]
                )
                if not shared_outputs:
                    continue
                if not shared_inputs and len(shared_outputs) < 2:
                    continue
                for left, right in ((source.id, target.id), (target.id, source.id)):
                    self._merge_candidate(
                        candidates,
                        RelationCandidate(
                            source_id=left,
                            target_id=right,
                            relation_hints=["substitute_for"],
                            candidate_methods=["shape_similarity_match"],
                            priority="medium",
                            evidence={
                                "shared_input_shape": shared_inputs,
                                "shared_output_shape": shared_outputs,
                            },
                        ),
                    )

    def _add_text_term_candidates(
        self,
        indexes: "_CandidateIndexes",
        candidates: MutableMapping[Tuple[str, str], RelationCandidate],
    ) -> None:
        for term, skill_ids in sorted(indexes.by_text_term.items()):
            ids = sorted(skill_ids)
            if len(ids) < 2 or len(term) < 4:
                continue
            if len(ids) > self.max_text_term_bucket_size:
                continue
            for source_id in ids:
                for target_id in ids:
                    if source_id >= target_id:
                        continue
                    for left, right in ((source_id, target_id), (target_id, source_id)):
                        self._merge_candidate(
                            candidates,
                            RelationCandidate(
                                source_id=left,
                                target_id=right,
                                relation_hints=["similar_to"],
                                candidate_methods=["text_term_match"],
                                priority="low",
                                evidence={"matched_terms": [term]},
                            ),
                        )

    def _merge_candidate(
        self,
        candidates: MutableMapping[Tuple[str, str], RelationCandidate],
        candidate: RelationCandidate,
    ) -> None:
        relation_hints = [
            hint for hint in candidate.relation_hints if hint in ALLOWED_RELATION_TYPES
        ]
        if not relation_hints:
            return
        key = tuple(sorted((candidate.source_id, candidate.target_id)))
        direction_key = f"{candidate.source_id}->{candidate.target_id}"
        candidate_evidence = {
            "directions": {direction_key: candidate.evidence},
        }
        existing = candidates.get(key)
        if existing is None:
            candidates[key] = RelationCandidate(
                source_id=candidate.source_id,
                target_id=candidate.target_id,
                relation_hints=sorted(set(relation_hints)),
                candidate_methods=sorted(set(candidate.candidate_methods)),
                priority=candidate.priority,
                evidence=candidate_evidence,
            )
            return

        priority = (
            candidate.priority
            if PRIORITY_RANK.get(candidate.priority, 0)
            > PRIORITY_RANK.get(existing.priority, 0)
            else existing.priority
        )
        evidence = _merge_directional_evidence(existing.evidence, candidate_evidence)
        candidates[key] = RelationCandidate(
            source_id=existing.source_id,
            target_id=existing.target_id,
            relation_hints=sorted(set(existing.relation_hints) | set(relation_hints)),
            candidate_methods=sorted(
                set(existing.candidate_methods) | set(candidate.candidate_methods)
            ),
            priority=priority,
            evidence=evidence,
        )

    def _limit_per_skill_relation(
        self, candidates: List[RelationCandidate]
    ) -> List[RelationCandidate]:
        buckets: Dict[str, List[RelationCandidate]] = defaultdict(list)
        for candidate in candidates:
            buckets[candidate.source_id].append(candidate)

        limited: List[RelationCandidate] = []
        for key in sorted(buckets):
            limited.extend(
                sorted(
                    buckets[key],
                    key=lambda item: (
                        -PRIORITY_RANK.get(item.priority, 0),
                        item.target_id,
                        ",".join(item.relation_hints),
                    ),
                )[: self.max_candidates_per_skill_relation]
            )
        return sorted(
            limited,
            key=lambda item: (
                item.source_id,
                -PRIORITY_RANK.get(item.priority, 0),
                item.target_id,
                ",".join(item.relation_hints),
            ),
        )


class _CandidateIndexes:
    def __init__(self) -> None:
        self.by_output_name: Dict[str, Set[str]] = defaultdict(set)
        self.by_input_name: Dict[str, Set[str]] = defaultdict(set)
        self.by_task: Dict[str, Set[str]] = defaultdict(set)
        self.by_text_term: Dict[str, Set[str]] = defaultdict(set)
        self.outputs_by_skill_name: Dict[Tuple[str, str], List[ArtifactSpec]] = (
            defaultdict(list)
        )
        self.inputs_by_skill_name: Dict[Tuple[str, str], List[ParameterSpec]] = (
            defaultdict(list)
        )

    @classmethod
    def from_skills(cls, skills: Iterable[SkillRepresentation]) -> "_CandidateIndexes":
        indexes = cls()
        for skill in skills:
            for output in skill.outputs:
                indexes.by_output_name[output.name].add(skill.id)
                indexes.outputs_by_skill_name[(skill.id, output.name)].append(output)
            for parameter in skill.inputs:
                indexes.by_input_name[parameter.name].add(skill.id)
                indexes.inputs_by_skill_name[(skill.id, parameter.name)].append(
                    parameter
                )
            for task in skill.tasks:
                indexes.by_task[task].add(skill.id)
            for term in _skill_terms(skill):
                indexes.by_text_term[term].add(skill.id)
        return indexes


def _merge_evidence(left: Dict[str, object], right: Dict[str, object]) -> Dict[str, object]:
    merged = dict(left)
    for key, value in right.items():
        if key not in merged:
            merged[key] = value
            continue
        if isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = _dedupe_list(merged[key] + value)
    return merged


def _merge_directional_evidence(
    left: Dict[str, object],
    right: Dict[str, object],
) -> Dict[str, object]:
    merged = dict(left)
    directions = dict(merged.get("directions", {}))
    for direction, evidence in dict(right.get("directions", {})).items():
        if direction in directions and isinstance(directions[direction], dict) and isinstance(evidence, dict):
            directions[direction] = _merge_evidence(directions[direction], evidence)
        else:
            directions[direction] = evidence
    merged["directions"] = directions
    return merged


def _dedupe_list(values: List[object]) -> List[object]:
    seen = set()
    result = []
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _io_signature(skill: SkillRepresentation) -> Dict[str, Set[Tuple[str, str]]]:
    return {
        "inputs": {(item.name, item.type) for item in skill.inputs},
        "outputs": {(item.name, item.type) for item in skill.outputs},
    }


def _skill_terms(skill: SkillRepresentation) -> Set[str]:
    terms: Set[str] = set()
    chunks = [skill.id, skill.name, skill.description]
    chunks.extend(skill.tasks)
    chunks.extend(item.name for item in skill.inputs)
    chunks.extend(item.description for item in skill.inputs)
    chunks.extend(item.name for item in skill.outputs)
    chunks.extend(item.description for item in skill.outputs)
    for chunk in chunks:
        terms.update(_tokenize(chunk))
    return terms


def _can_feed_by_field_overlap(output: ArtifactSpec, parameter: ParameterSpec) -> bool:
    if output.name == parameter.name:
        return True
    output_terms = _tokenize(f"{output.name} {output.description}") - CAN_FEED_WEAK_TERMS
    input_terms = _tokenize(f"{parameter.name} {parameter.description}") - CAN_FEED_WEAK_TERMS
    return bool(output_terms & input_terms)


def _tokenize(text: str) -> Set[str]:
    return _GRAPH_TERM_LEXICON.tokenize(text)
