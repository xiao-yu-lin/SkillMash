"""LLM-backed ontology relation matching."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Protocol

from skillmash.graph.models import (
    ALLOWED_RELATION_TYPES,
    GraphDiagnostic,
    LLMMatch,
    RelationCandidate,
    SkillRegistry,
)
from skillmash.representation.llm import (
    LLMConfig,
    create_openai_client,
    extract_message_content,
    safe_model_dump,
)
from skillmash.representation.models import SkillRepresentation


DEFAULT_THRESHOLDS = {
    "can_feed": 0.0,
    "similar_to": 0.0,
    "substitute_for": 0.0,
}


class OntologyMatcher(Protocol):
    """Protocol for relation candidate matchers."""

    def match(
        self,
        registry: SkillRegistry,
        candidates: Iterable[RelationCandidate],
    ) -> List[LLMMatch]:
        ...


class MatchProgress(Protocol):
    """Progress callback for LLM candidate matching."""

    def __call__(self, event: str, current: int, total: int, details: Dict[str, Any]) -> None:
        ...


class OpenAICompatibleOntologyMatcher:
    """Validate relation candidates through an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        batch_size: int = 12,
        max_workers: int = 1,
        require_consensus: bool = True,
        prompt_version: str = "skillmash-graph-match-v1",
        thresholds: Dict[str, float] = DEFAULT_THRESHOLDS,
        progress: Optional[MatchProgress] = None,
    ) -> None:
        self.config = config
        self.batch_size = batch_size
        self.max_workers = max(1, max_workers)
        self.require_consensus = require_consensus
        self.prompt_version = prompt_version
        self.thresholds = thresholds
        self.progress = progress
        self.client = create_openai_client(config)
        self.diagnostics: List[GraphDiagnostic] = []

    def match(
        self,
        registry: SkillRegistry,
        candidates: Iterable[RelationCandidate],
    ) -> List[LLMMatch]:
        candidate_list = list(candidates)
        matches: List[LLMMatch] = []
        self.diagnostics = []
        total_batches = (
            (len(candidate_list) + self.batch_size - 1) // self.batch_size
            if candidate_list
            else 0
        )
        self._emit_progress(
            "matching_start",
            0,
            total_batches,
            {
                "candidate_count": len(candidate_list),
                "batch_size": self.batch_size,
                "max_workers": self.max_workers,
                "consensus_runs": 2 if self.require_consensus else 1,
            },
        )
        batches = [
            (batch_index, candidate_list[start : start + self.batch_size])
            for batch_index, start in enumerate(
                range(0, len(candidate_list), self.batch_size),
                start=1,
            )
        ]
        batch_sizes = {
            batch_index: len(batch)
            for batch_index, batch in batches
        }
        if self.max_workers == 1 or len(batches) <= 1:
            results = [
                self._match_batch(registry, batch, batch_index, total_batches)
                for batch_index, batch in batches
            ]
        else:
            results = []
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [
                    executor.submit(
                        self._match_batch,
                        registry,
                        batch,
                        batch_index,
                        total_batches,
                    )
                    for batch_index, batch in batches
                ]
                for future in as_completed(futures):
                    results.append(future.result())

        for batch_index, batch_matches, batch_diagnostics in sorted(
            results,
            key=lambda item: item[0],
        ):
            self.diagnostics.extend(batch_diagnostics)
            matches.extend(batch_matches)
            self._emit_progress(
                "batch_done",
                batch_index,
                total_batches,
                {
                    "candidate_count": batch_sizes[batch_index],
                    "match_count": len(batch_matches),
                    "accepted_count": len(
                        [match for match in batch_matches if match.accepted]
                    ),
                    "diagnostics_count": len(batch_diagnostics),
                },
            )
        self._emit_progress(
            "matching_done",
            total_batches,
            total_batches,
            {
                "match_count": len(matches),
                "accepted_count": len([match for match in matches if match.accepted]),
                "diagnostics_count": len(self.diagnostics),
            },
        )
        return matches

    def manifest_metadata(self) -> Dict[str, Any]:
        return {
            "model": self.config.model,
            "base_url": self.config.base_url,
            "temperature": self.config.temperature,
            "prompt_version": self.prompt_version,
            "batch_size": self.batch_size,
            "max_workers": self.max_workers,
            "require_consensus": self.require_consensus,
            "consensus_runs": 2 if self.require_consensus else 1,
        }

    def _match_batch(
        self,
        registry: SkillRegistry,
        batch: List[RelationCandidate],
        batch_index: int,
        total_batches: int,
    ) -> tuple[int, List[LLMMatch], List[GraphDiagnostic]]:
        payload = _build_llm_context(registry, batch)
        self._emit_progress(
            "batch_start",
            batch_index,
            total_batches,
            {
                "candidate_count": len(batch),
                "input_sha256": payload["input_sha256"],
                "candidate_ids": [candidate.key for candidate in batch],
                "consensus_runs": 2 if self.require_consensus else 1,
            },
        )
        first_matches, first_diagnostics = self._request_and_validate_batch(
            registry,
            batch,
            reverse_skill_order=False,
        )
        if not self.require_consensus:
            return batch_index, first_matches, first_diagnostics

        second_matches, second_diagnostics = self._request_and_validate_batch(
            registry,
            batch,
            reverse_skill_order=True,
        )
        consensus_matches, consensus_diagnostics = _consensus_matches(
            first_matches,
            second_matches,
        )
        return (
            batch_index,
            consensus_matches,
            first_diagnostics + second_diagnostics + consensus_diagnostics,
        )

    def _request_and_validate_batch(
        self,
        registry: SkillRegistry,
        batch: List[RelationCandidate],
        *,
        reverse_skill_order: bool,
    ) -> tuple[List[LLMMatch], List[GraphDiagnostic]]:
        payload = _build_llm_context(
            registry,
            batch,
            reverse_skill_order=reverse_skill_order,
        )
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=self.config.temperature,
                timeout=200,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                            indent=2,
                        ),
                    },
                ],
            )
        except Exception as exc:
            raise RuntimeError(f"LLM graph matching request failed: {exc}") from exc

        choice = response.choices[0]
        content = extract_message_content(choice.message)
        if not content:
            raise RuntimeError(
                "LLM graph matching response content is empty. "
                f"finish_reason={getattr(choice, 'finish_reason', None)!r}; "
                f"message={safe_model_dump(choice.message)}"
            )
        try:
            raw_payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "LLM graph matching response is not valid JSON. "
                f"content_prefix={content[:1000]!r}"
            ) from exc
        batch_matches, batch_diagnostics = validate_llm_matches(
            raw_payload,
            registry,
            batch,
            thresholds=self.thresholds,
        )
        return batch_matches, batch_diagnostics

    def _emit_progress(
        self,
        event: str,
        current: int,
        total: int,
        details: Dict[str, Any],
    ) -> None:
        if self.progress is None:
            return
        self.progress(event, current, total, details)


def validate_llm_matches(
    payload: Dict[str, Any],
    registry: SkillRegistry,
    candidates: Iterable[RelationCandidate],
    *,
    thresholds: Dict[str, float] = DEFAULT_THRESHOLDS,
) -> tuple[List[LLMMatch], List[GraphDiagnostic]]:
    """Normalize and validate raw LLM matches."""

    candidate_by_key = {candidate.key: candidate for candidate in candidates}
    candidates_by_pair = {}
    for candidate in candidates:
        candidates_by_pair[(candidate.source_id, candidate.target_id)] = candidate
        candidates_by_pair[(candidate.target_id, candidate.source_id)] = candidate
    matches: List[LLMMatch] = []
    diagnostics: List[GraphDiagnostic] = []

    raw_matches = payload.get("matches", [])
    if not isinstance(raw_matches, list):
        return [], [
            GraphDiagnostic(
                stage="llm_match",
                severity="error",
                code="invalid_matches_payload",
                message="LLM payload field 'matches' must be a list.",
            )
        ]

    for index, raw in enumerate(raw_matches):
        if not isinstance(raw, dict):
            diagnostics.append(
                GraphDiagnostic(
                    stage="llm_match",
                    severity="warning",
                    code="invalid_match_item",
                    message="LLM match item is not an object.",
                    details={"index": index, "item": raw},
                )
            )
            continue

        match, item_diagnostics = _normalize_match(
            raw, registry, candidate_by_key, candidates_by_pair, thresholds
        )
        diagnostics.extend(item_diagnostics)
        if match is not None:
            matches.append(match)

    return matches, diagnostics


def _normalize_match(
    raw: Dict[str, Any],
    registry: SkillRegistry,
    candidate_by_key: Dict[str, RelationCandidate],
    candidates_by_pair: Dict[tuple[str, str], RelationCandidate],
    thresholds: Dict[str, float],
) -> tuple[Optional[LLMMatch], List[GraphDiagnostic]]:
    diagnostics: List[GraphDiagnostic] = []
    source_id = str(raw.get("source_id") or "")
    target_id = str(raw.get("target_id") or "")
    relation_type = str(raw.get("relation_type") or "")
    candidate_id = raw.get("candidate_id")
    candidate_id = str(candidate_id) if candidate_id else None

    errors: List[str] = []
    if source_id not in registry.skills:
        errors.append("source_id does not exist")
    if target_id not in registry.skills:
        errors.append("target_id does not exist")
    if relation_type not in ALLOWED_RELATION_TYPES:
        errors.append("relation_type is not allowed")

    candidate = None
    if candidate_id:
        candidate = candidate_by_key.get(candidate_id)
    if candidate is None:
        candidate = candidates_by_pair.get((source_id, target_id))
    if candidate is None:
        errors.append("match does not correspond to an input candidate")
    elif relation_type not in candidate.relation_hints:
        errors.append("relation_type is not allowed for the input candidate")

    try:
        confidence = float(raw.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
        errors.append("confidence is not numeric")
    if confidence < 0 or confidence > 1:
        errors.append("confidence must be between 0 and 1")
        confidence = max(0, min(1, confidence))

    reasons = [str(item) for item in raw.get("reasons", []) if str(item).strip()]
    supporting_fields = raw.get("supporting_fields")
    if not isinstance(supporting_fields, dict):
        supporting_fields = {}

    if relation_type == "can_feed" and candidate is not None:
        source_outputs = set(_field_names(supporting_fields.get("source_outputs", [])))
        target_inputs = set(_field_names(supporting_fields.get("target_inputs", [])))
        directional_evidence = _directional_evidence(
            candidate,
            source_id,
            target_id,
        )
        evidence_outputs = {
            item.get("name")
            for item in directional_evidence.get("source_outputs", [])
            if isinstance(item, dict)
        }
        evidence_inputs = {
            item.get("name")
            for item in directional_evidence.get("target_inputs", [])
            if isinstance(item, dict)
        }
        if source_outputs and target_inputs:
            if not (source_outputs & evidence_outputs and target_inputs & evidence_inputs):
                errors.append("supporting_fields do not match candidate evidence")
        elif not (evidence_outputs & evidence_inputs):
            errors.append("can_feed has no supported output/input pair")

    accepted = not errors and confidence >= thresholds.get(relation_type, 1.0)
    match = LLMMatch(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        confidence=confidence,
        method=str(raw.get("method") or "llm_ontology_match"),
        reasons=reasons,
        supporting_fields=supporting_fields,
        candidate_id=candidate.key if candidate is not None else candidate_id,
        accepted=accepted,
        diagnostics=errors,
        raw=raw,
    )

    if errors:
        diagnostics.append(
            GraphDiagnostic(
                stage="llm_match",
                severity="warning",
                code="rejected_llm_match",
                message="LLM match failed validation.",
                skill_id=source_id or None,
                details={"errors": errors, "match": raw},
            )
        )
    elif not accepted:
        diagnostics.append(
            GraphDiagnostic(
                stage="llm_match",
                severity="info",
                code="low_confidence_llm_match",
                message="LLM match is below the relation threshold.",
                skill_id=source_id,
                details={
                    "threshold": thresholds.get(relation_type),
                    "match": match.to_dict(),
                },
            )
        )

    return match, diagnostics


def _field_names(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    names = []
    for value in values:
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, dict) and value.get("name"):
            names.append(str(value["name"]))
    return names


def _directional_evidence(
    candidate: RelationCandidate,
    source_id: str,
    target_id: str,
) -> Dict[str, Any]:
    directions = candidate.evidence.get("directions", {})
    if isinstance(directions, dict):
        evidence = directions.get(f"{source_id}->{target_id}")
        if isinstance(evidence, dict):
            return evidence
    return candidate.evidence


def _consensus_matches(
    first_matches: List[LLMMatch],
    second_matches: List[LLMMatch],
) -> tuple[List[LLMMatch], List[GraphDiagnostic]]:
    first_by_key = {
        _match_key(match): match for match in first_matches if match.accepted
    }
    second_by_key = {
        _match_key(match): match for match in second_matches if match.accepted
    }
    shared_keys = sorted(set(first_by_key) & set(second_by_key))
    consensus: List[LLMMatch] = []
    diagnostics: List[GraphDiagnostic] = []

    for key in shared_keys:
        first = first_by_key[key]
        second = second_by_key[key]
        consensus.append(
            LLMMatch(
                source_id=first.source_id,
                target_id=first.target_id,
                relation_type=first.relation_type,
                confidence=min(first.confidence, second.confidence),
                method="llm_consensus_match",
                reasons=_dedupe_strings(first.reasons + second.reasons),
                supporting_fields=_merge_supporting_fields(
                    first.supporting_fields,
                    second.supporting_fields,
                ),
                candidate_id=first.candidate_id,
                accepted=True,
                raw={
                    "first": first.raw,
                    "second": second.raw,
                    "first_confidence": first.confidence,
                    "second_confidence": second.confidence,
                },
            )
        )

    for key in sorted(set(first_by_key) ^ set(second_by_key)):
        match = first_by_key.get(key) or second_by_key[key]
        diagnostics.append(
            GraphDiagnostic(
                stage="llm_match",
                severity="info",
                code="no_consensus_match",
                message="LLM match did not appear in both order-swapped runs.",
                skill_id=match.source_id,
                details={"match": match.to_dict()},
            )
        )

    return consensus, diagnostics


def _match_key(match: LLMMatch) -> tuple[str, str, str, Optional[str]]:
    return (
        match.source_id,
        match.target_id,
        match.relation_type,
        match.candidate_id,
    )


def _dedupe_strings(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _merge_supporting_fields(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if key not in merged:
            merged[key] = value
            continue
        if isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = _dedupe_strings([str(item) for item in merged[key] + value])
    return merged


def _build_llm_context(
    registry: SkillRegistry,
    candidates: List[RelationCandidate],
    *,
    reverse_skill_order: bool = False,
) -> Dict[str, Any]:
    skill_ids = _ordered_skill_ids_for_candidates(
        candidates,
        reverse_skill_order=reverse_skill_order,
    )
    skills = [registry.skills[skill_id] for skill_id in skill_ids]
    candidate_payload = [candidate.to_dict() for candidate in candidates]
    return {
        "allowed_relation_types": sorted(ALLOWED_RELATION_TYPES),
        "skills": [_skill_context(skill) for skill in skills],
        "candidates": candidate_payload,
        "input_sha256": hashlib.sha256(
            json.dumps(candidate_payload, sort_keys=True, ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest(),
    }


def _ordered_skill_ids_for_candidates(
    candidates: List[RelationCandidate],
    *,
    reverse_skill_order: bool,
) -> List[str]:
    ordered: List[str] = []
    for candidate in candidates:
        pair = (
            [candidate.target_id, candidate.source_id]
            if reverse_skill_order
            else [candidate.source_id, candidate.target_id]
        )
        for skill_id in pair:
            if skill_id not in ordered:
                ordered.append(skill_id)
    return ordered


def _skill_context(skill: SkillRepresentation) -> Dict[str, Any]:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "tasks": list(skill.tasks),
        "inputs": [item.to_dict() for item in skill.inputs],
        "outputs": [item.to_dict() for item in skill.outputs],
    }


_SYSTEM_PROMPT = """You validate Skill relation candidates for a Skill graph.

Return JSON only. Do not include markdown.

Input contains:
- skills: normalized Skill representations.
- candidates: deterministic relation candidates.
- allowed_relation_types.

For each candidate pair, decide which relation_hints are valid. A single
candidate may produce multiple matches, for example both similar_to and
substitute_for. Do not invent new skills, relation types, or candidate pairs.
If a candidate direction is wrong, omit it or return it with low confidence
and a reason.

Return:
{
  "matches": [
    {
      "candidate_id": "skill_a<->skill_b",
      "source_id": "directed source skill id",
      "target_id": "directed target skill id",
      "relation_type": "can_feed|similar_to|substitute_for",
      "confidence": 0.0-1.0,
      "method": "llm_ontology_match",
      "reasons": ["short evidence-based reason"],
      "supporting_fields": {
        "source_outputs": ["..."],
        "target_inputs": ["..."],
        "source_tasks": ["..."],
        "target_tasks": ["..."]
      }
    }
  ]
}

Relation meanings:
- can_feed: source output can satisfy target input.
- similar_to: skills have similar purpose or capability.
- substitute_for: source can plausibly replace target in similar contexts.
"""
