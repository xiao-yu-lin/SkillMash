"""Deterministic relation candidate generation."""

from __future__ import annotations

import logging
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
COMPATIBLE_CAN_FEED_TYPES = frozenset(
    {
        ("markdown", "text"),
        ("audio", "file"),      # 音频文件是文件的子类型
        ("video", "file"),      # 视频文件是文件的子类型
        ("image", "file"),      # 图片文件是文件的子类型
        ("pdf", "file"),        # PDF 是文件的子类型
        ("path", "file"),       # 路径可以指向文件
    }
)
GENERIC_TEXT_INPUT_NAMES = frozenset(
    {
        "body",
        "content",
        "prompt",
        "script",
        "text",
        "transcript",
    }
)
TEXTUAL_OUTPUT_TERMS = frozenset(
    {
        "article",
        "brief",
        "content",
        "draft",
        "notes",
        "report",
        "review",
        "script",
        "summary",
        "transcript",
    }
)
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
LOGGER = logging.getLogger(__name__)


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

        LOGGER.debug("candidate_generation_start skill_count=%s", len(skills))
        self._add_exact_io_candidates(indexes, candidates)
        self._add_compatible_type_candidates(skills, candidates)

        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                item.source_id,
                -PRIORITY_RANK.get(item.priority, 0),
                item.target_id,
                ",".join(item.relation_hints),
            ),
        )
        limited = self._limit_per_skill_relation(ordered)
        LOGGER.debug(
            "candidate_generation_done generated_count=%s emitted_count=%s",
            len(ordered),
            len(limited),
        )
        return limited

    def _add_exact_io_candidates(
        self,
        indexes: "_CandidateIndexes",
        candidates: MutableMapping[Tuple[str, str], RelationCandidate],
    ) -> None:
        for name in sorted(set(indexes.by_output_name) & set(indexes.by_input_name)):
            if self.lexicon.is_generic_io_name(name):
                LOGGER.debug(
                    "candidate_skipped reason=generic_io_name "
                    "method=exact_io_match name=%s output_skills=%s input_skills=%s",
                    name,
                    ",".join(sorted(indexes.by_output_name[name])),
                    ",".join(sorted(indexes.by_input_name[name])),
                )
                continue
            pair_fanout = (
                len(indexes.by_output_name[name]) * len(indexes.by_input_name[name])
            )
            if pair_fanout > self.max_exact_io_pair_fanout:
                LOGGER.debug(
                    "candidate_skipped reason=max_exact_io_pair_fanout "
                    "method=exact_io_match name=%s fanout=%s limit=%s "
                    "output_skills=%s input_skills=%s",
                    name,
                    pair_fanout,
                    self.max_exact_io_pair_fanout,
                    ",".join(sorted(indexes.by_output_name[name])),
                    ",".join(sorted(indexes.by_input_name[name])),
                )
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
                        if not _can_feed_by_type(output.type, parameter.type):
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
                                    "matched_type": f"{output.type}->{parameter.type}",
                                },
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
        _debug_candidate_generated(candidate, relation_hints)
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
            ordered_bucket = sorted(
                buckets[key],
                key=lambda item: (
                    -PRIORITY_RANK.get(item.priority, 0),
                    item.target_id,
                    ",".join(item.relation_hints),
                ),
            )
            kept = ordered_bucket[: self.max_candidates_per_skill_relation]
            dropped = ordered_bucket[self.max_candidates_per_skill_relation :]
            limited.extend(kept)
            for candidate in dropped:
                LOGGER.debug(
                    "candidate_skipped reason=max_candidates_per_skill_relation "
                    "source=%s target=%s priority=%s methods=%s "
                    "relation_hints=%s limit=%s",
                    candidate.source_id,
                    candidate.target_id,
                    candidate.priority,
                    ",".join(candidate.candidate_methods),
                    ",".join(candidate.relation_hints),
                    self.max_candidates_per_skill_relation,
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


def _debug_candidate_generated(
    candidate: RelationCandidate,
    relation_hints: List[str],
) -> None:
    if not LOGGER.isEnabledFor(logging.DEBUG):
        return
    evidence = candidate.evidence
    LOGGER.debug(
        "candidate_generated source=%s target=%s priority=%s methods=%s "
        "relation_hints=%s matched_terms=%s matched_type=%s",
        candidate.source_id,
        candidate.target_id,
        candidate.priority,
        ",".join(candidate.candidate_methods),
        ",".join(relation_hints),
        ",".join(str(item) for item in evidence.get("matched_terms", [])),
        evidence.get("matched_type", ""),
    )


def _skill_terms(skill: SkillRepresentation) -> Set[str]:
    terms: Set[str] = set()
    chunks = [skill.id, skill.name, skill.description]
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
    if output_terms & input_terms:
        return True
    return _can_feed_via_textual_coercion(output, parameter, output_terms, input_terms)


def _can_feed_by_type(output_type: str, input_type: str) -> bool:
    if output_type == "unknown" or input_type == "unknown":
        return False
    if output_type == input_type:
        return True
    return (output_type, input_type) in COMPATIBLE_CAN_FEED_TYPES


def _can_feed_via_textual_coercion(
    output: ArtifactSpec,
    parameter: ParameterSpec,
    output_terms: Set[str],
    input_terms: Set[str],
) -> bool:
    if (output.type, parameter.type) != ("markdown", "text"):
        return False
    if not _is_generic_text_input(parameter, input_terms):
        return False
    return _is_textual_output(output, output_terms)


def _is_generic_text_input(parameter: ParameterSpec, input_terms: Set[str]) -> bool:
    return parameter.name in GENERIC_TEXT_INPUT_NAMES or bool(
        input_terms & GENERIC_TEXT_INPUT_NAMES
    )


def _is_textual_output(output: ArtifactSpec, output_terms: Set[str]) -> bool:
    return output.name in TEXTUAL_OUTPUT_TERMS or bool(output_terms & TEXTUAL_OUTPUT_TERMS)


def _tokenize(text: str) -> Set[str]:
    return _GRAPH_TERM_LEXICON.tokenize(text)
