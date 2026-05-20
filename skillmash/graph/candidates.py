"""Deterministic relation candidate generation."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, MutableMapping, Set, Tuple

from skillmash.graph.models import ALLOWED_RELATION_TYPES, RelationCandidate, SkillRegistry
from skillmash.representation.models import ArtifactSpec, ParameterSpec, SkillRepresentation


DEFAULT_TASK_TRANSITIONS = {
    ("search", "summarize"),
    ("search", "analyze"),
    ("extract", "analyze"),
    ("analyze", "write"),
    ("write", "generate"),
    ("write", "convert"),
    ("summarize", "write"),
    ("generate", "convert"),
}

PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}


class CandidateGenerator:
    """Generate high-recall Skill-Skill relation candidates."""

    def __init__(
        self,
        *,
        max_candidates_per_skill_relation: int = 12,
        task_transitions: Iterable[Tuple[str, str]] = DEFAULT_TASK_TRANSITIONS,
    ) -> None:
        self.max_candidates_per_skill_relation = max_candidates_per_skill_relation
        self.task_transitions = set(task_transitions)

    def generate(self, registry: SkillRegistry) -> List[RelationCandidate]:
        skills = registry.ordered_skills()
        indexes = _CandidateIndexes.from_skills(skills)
        candidates: Dict[Tuple[str, str, str], RelationCandidate] = {}

        self._add_exact_io_candidates(indexes, candidates)
        self._add_compatible_type_candidates(skills, candidates)
        self._add_task_transition_candidates(indexes, candidates)
        self._add_task_overlap_candidates(indexes, candidates)
        self._add_shape_similarity_candidates(skills, candidates)
        self._add_text_term_candidates(indexes, candidates)

        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                item.source_id,
                item.relation_hint,
                -PRIORITY_RANK.get(item.priority, 0),
                item.target_id,
                item.candidate_method,
            ),
        )
        return self._limit_per_skill_relation(ordered)

    def _add_exact_io_candidates(
        self,
        indexes: "_CandidateIndexes",
        candidates: MutableMapping[Tuple[str, str, str], RelationCandidate],
    ) -> None:
        for name in sorted(set(indexes.by_output_name) & set(indexes.by_input_name)):
            for source_id in sorted(indexes.by_output_name[name]):
                for target_id in sorted(indexes.by_input_name[name]):
                    if source_id == target_id:
                        continue
                    self._merge_candidate(
                        candidates,
                        RelationCandidate(
                            source_id=source_id,
                            target_id=target_id,
                            relation_hint="can_feed",
                            candidate_method="exact_io_match",
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
        candidates: MutableMapping[Tuple[str, str, str], RelationCandidate],
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
                        if not shared_terms and output.name != parameter.name:
                            continue
                        self._merge_candidate(
                            candidates,
                            RelationCandidate(
                                source_id=source.id,
                                target_id=target.id,
                                relation_hint="can_feed",
                                candidate_method="compatible_type_match",
                                priority="medium",
                                evidence={
                                    "matched_terms": shared_terms,
                                    "source_outputs": [output.to_dict()],
                                    "target_inputs": [parameter.to_dict()],
                                    "matched_type": output.type,
                                },
                            ),
                        )

    def _add_task_transition_candidates(
        self,
        indexes: "_CandidateIndexes",
        candidates: MutableMapping[Tuple[str, str, str], RelationCandidate],
    ) -> None:
        for source_task, target_task in sorted(self.task_transitions):
            for source_id in sorted(indexes.by_task.get(source_task, [])):
                for target_id in sorted(indexes.by_task.get(target_task, [])):
                    if source_id == target_id:
                        continue
                    self._merge_candidate(
                        candidates,
                        RelationCandidate(
                            source_id=source_id,
                            target_id=target_id,
                            relation_hint="composes_with",
                            candidate_method="task_transition_match",
                            priority="medium",
                            evidence={
                                "source_tasks": [source_task],
                                "target_tasks": [target_task],
                            },
                        ),
                    )

    def _add_task_overlap_candidates(
        self,
        indexes: "_CandidateIndexes",
        candidates: MutableMapping[Tuple[str, str, str], RelationCandidate],
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
                                relation_hint="similar_to",
                                candidate_method="task_overlap_match",
                                priority="medium",
                                evidence={"shared_tasks": [task]},
                            ),
                        )

    def _add_shape_similarity_candidates(
        self,
        skills: List[SkillRepresentation],
        candidates: MutableMapping[Tuple[str, str, str], RelationCandidate],
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
                            relation_hint="substitute_for",
                            candidate_method="shape_similarity_match",
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
        candidates: MutableMapping[Tuple[str, str, str], RelationCandidate],
    ) -> None:
        for term, skill_ids in sorted(indexes.by_text_term.items()):
            ids = sorted(skill_ids)
            if len(ids) < 2 or len(term) < 4:
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
                                relation_hint="similar_to",
                                candidate_method="text_term_match",
                                priority="low",
                                evidence={"matched_terms": [term]},
                            ),
                        )

    def _merge_candidate(
        self,
        candidates: MutableMapping[Tuple[str, str, str], RelationCandidate],
        candidate: RelationCandidate,
    ) -> None:
        if candidate.relation_hint not in ALLOWED_RELATION_TYPES:
            return
        key = (candidate.source_id, candidate.target_id, candidate.relation_hint)
        existing = candidates.get(key)
        if existing is None:
            candidates[key] = candidate
            return

        priority = (
            candidate.priority
            if PRIORITY_RANK.get(candidate.priority, 0)
            > PRIORITY_RANK.get(existing.priority, 0)
            else existing.priority
        )
        evidence = _merge_evidence(existing.evidence, candidate.evidence)
        methods = set(evidence.get("candidate_methods", []))
        methods.add(existing.candidate_method)
        methods.add(candidate.candidate_method)
        evidence["candidate_methods"] = sorted(methods)
        candidates[key] = RelationCandidate(
            source_id=existing.source_id,
            target_id=existing.target_id,
            relation_hint=existing.relation_hint,
            candidate_method=existing.candidate_method,
            priority=priority,
            evidence=evidence,
        )

    def _limit_per_skill_relation(
        self, candidates: List[RelationCandidate]
    ) -> List[RelationCandidate]:
        buckets: Dict[Tuple[str, str], List[RelationCandidate]] = defaultdict(list)
        for candidate in candidates:
            buckets[(candidate.source_id, candidate.relation_hint)].append(candidate)

        limited: List[RelationCandidate] = []
        for key in sorted(buckets):
            limited.extend(
                sorted(
                    buckets[key],
                    key=lambda item: (
                        -PRIORITY_RANK.get(item.priority, 0),
                        item.target_id,
                        item.candidate_method,
                    ),
                )[: self.max_candidates_per_skill_relation]
            )
        return sorted(
            limited,
            key=lambda item: (
                item.source_id,
                item.relation_hint,
                -PRIORITY_RANK.get(item.priority, 0),
                item.target_id,
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


def _tokenize(text: str) -> Set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(text).lower())
        if len(token) >= 3
    }
