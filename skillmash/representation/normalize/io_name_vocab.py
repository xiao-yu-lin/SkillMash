"""Dynamic vocabulary for normalizing Skill input/output names.

This module provides specialized vocabulary classes for normalizing
I/O names (like 'query', 'paper', 'summary'). It builds on the
shared base vocabulary infrastructure from base_vocab.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Set, Union

from skillmash.representation.normalize.base_vocab import (
    BaseCandidate,
    BaseResolution,
    BaseResolver,
    BaseVocabTerm,
    BaseVocabulary,
    HeuristicBaseResolver,
    _max_vocab_size_from_data,
    _normalize_max_vocab_size,
)
from skillmash.common.llm import (
    LLMConfig,
    create_llm_client,
)
from skillmash.representation.models import NormalizationConfig


@dataclass(frozen=True)
class IONameCandidate(BaseCandidate):
    """Candidate for I/O name vocabulary resolution with direction and type context."""

    direction: str = ""
    data_type: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["direction"] = self.direction
        data["type"] = self.data_type
        return data


# IONameResolution is identical to BaseResolution, use it as alias.
IONameResolution = BaseResolution

# IONameResolver protocol is identical to BaseResolver, use it as alias.
IONameResolver = BaseResolver

# IONameVocabTerm is identical to BaseVocabTerm, use it as alias.
IONameVocabTerm = BaseVocabTerm


class IONameVocabulary(BaseVocabulary):
    """Mutable io_name_vocab with bounded canonical terms and unbounded aliases.

    This vocabulary manages I/O name terms used for graph linking.
    """

    @classmethod
    def from_config(cls, config: NormalizationConfig) -> "IONameVocabulary":
        """Build vocabulary from NormalizationConfig."""
        terms: Dict[str, IONameVocabTerm] = {}
        for alias, name in config.io_name_aliases.items():
            term = terms.setdefault(name, IONameVocabTerm(name=name))
            term.aliases.add(alias)
        for name, term in list(terms.items()):
            term.aliases.discard(name)
        return cls(
            version=config.io_name_vocab_version,
            max_vocab_size=config.max_vocab_size,
            terms=list(terms.values()),
        )

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        config: NormalizationConfig,
    ) -> "IONameVocabulary":
        """Build vocabulary from a dictionary with config defaults."""
        return cls(
            version=str(data.get("version") or config.io_name_vocab_version),
            max_vocab_size=_max_vocab_size_from_data(
                data.get("max_vocab_size"),
                config.max_vocab_size,
            ),
            terms=[
                IONameVocabTerm.from_dict(item)
                for item in data.get("terms", [])
                if isinstance(item, dict)
            ],
        )

    @classmethod
    def load(
        cls,
        path: Union[Path, str],
        config: NormalizationConfig,
    ) -> "IONameVocabulary":
        """Load vocabulary from a JSON file with config defaults."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data, config)


class HeuristicIONameResolver(HeuristicBaseResolver):
    """Deterministic fallback resolver used when no LLM resolver is configured."""

    def resolve(
        self,
        candidate: IONameCandidate,
        vocabulary: IONameVocabulary,
    ) -> IONameResolution:
        return self.resolve_base(
            token=candidate.token,
            description=candidate.description,
            vocabulary=vocabulary,
        )

    def resolve_many(
        self,
        candidates: List[IONameCandidate],
        vocabulary: IONameVocabulary,
    ) -> Dict[str, IONameResolution]:
        return self.resolve_many_base(candidates, vocabulary)


class LLMIONameResolver:
    """LLM-backed resolver for unseen io_name_vocab terms."""

    def __init__(
        self,
        config: LLMConfig,
        progress: Optional[Callable[[str, IONameCandidate, Optional[IONameResolution]], None]] = None,
    ) -> None:
        self.config = config
        self.client = create_llm_client(config)
        self.progress = progress
        self._cache: Dict[str, IONameResolution] = {}
        self._cache_lock = RLock()

    def resolve(
        self,
        candidate: IONameCandidate,
        vocabulary: IONameVocabulary,
    ) -> IONameResolution:
        with self._cache_lock:
            cached = self._cache.get(candidate.token)
        if cached is not None:
            return cached

        context = {
            "candidate": candidate.to_dict(),
            "vocabulary": vocabulary.resolver_context(),
            "allowed_actions": [
                "alias_existing",
                "create_new",
                "merge_existing",
                "exclude_non_runtime",
            ],
            "rules": [
                "If the candidate is clearly bookkeeping-only telemetry, analytics, tracking, or original-copy data, use exclude_non_runtime.",
                "Do not exclude logs, traces, metrics, stats, or evidence when they are runtime inputs consumed by debugging, performance, security, or database analysis Skills.",
                "If the candidate is synonymous with an existing term, use alias_existing and set target to that term.",
                "If the vocabulary is not full and the candidate is a genuinely new runtime semantic role, use create_new.",
                "If the vocabulary is full, do not use create_new; use merge_existing or exclude_non_runtime.",
            ],
        }
        self._emit_progress("start", candidate, None)
        content = self.client.complete_json(
            system_prompt=_IO_NAME_RESOLVER_PROMPT,
            user_content=json.dumps(context, ensure_ascii=False, indent=2),
            error_context="IO name vocabulary LLM",
        )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "IO name vocabulary LLM response is not valid JSON. "
                f"content_prefix={content[:1000]!r}"
            ) from exc
        resolution = _resolution_from_payload(payload, vocabulary)
        with self._cache_lock:
            self._cache[candidate.token] = resolution
        self._emit_progress("done", candidate, resolution)
        return resolution

    def resolve_many(
        self,
        candidates: List[IONameCandidate],
        vocabulary: IONameVocabulary,
    ) -> Dict[str, IONameResolution]:
        unique_candidates: List[IONameCandidate] = []
        seen: Set[str] = set()
        resolutions: Dict[str, IONameResolution] = {}
        for candidate in candidates:
            if not candidate.token or candidate.token in seen:
                continue
            seen.add(candidate.token)
            with self._cache_lock:
                cached = self._cache.get(candidate.token)
            if cached is not None:
                resolutions[candidate.token] = cached
                continue
            unique_candidates.append(candidate)

        if not unique_candidates:
            return resolutions

        context = {
            "candidates": [candidate.to_dict() for candidate in unique_candidates],
            "vocabulary": vocabulary.resolver_context(),
            "allowed_actions": [
                "alias_existing",
                "create_new",
                "merge_existing",
                "exclude_non_runtime",
            ],
            "rules": [
                "Return exactly one resolution for every candidate token.",
                "If a candidate is clearly bookkeeping-only telemetry, analytics, tracking, or original-copy data, use exclude_non_runtime.",
                "Do not exclude logs, traces, metrics, stats, or evidence when they are runtime inputs consumed by debugging, performance, security, or database analysis Skills.",
                "If a candidate is synonymous with an existing term, use alias_existing and set target to that term.",
                "If the vocabulary is not full and a candidate is a genuinely new runtime semantic role, use create_new.",
                "If the vocabulary is full, do not use create_new; use merge_existing or exclude_non_runtime.",
                "Use the candidate list as one Skill's combined input/output context; resolve names consistently across directions.",
            ],
        }
        for candidate in unique_candidates:
            self._emit_progress("start", candidate, None)
        content = self.client.complete_json(
            system_prompt=_IO_NAME_BATCH_RESOLVER_PROMPT,
            user_content=json.dumps(context, ensure_ascii=False, indent=2),
            timeout=200,
            error_context="IO name vocabulary batch LLM",
        )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "IO name vocabulary batch LLM response is not valid JSON. "
                f"content_prefix={content[:1000]!r}"
            ) from exc

        payload_items = payload.get("resolutions", [])
        if not isinstance(payload_items, list):
            raise RuntimeError("IO name vocabulary batch LLM response must contain resolutions array.")

        remaining = {candidate.token for candidate in unique_candidates}
        for item in payload_items:
            if not isinstance(item, dict):
                continue
            token = str(item.get("token") or "").strip()
            if token not in remaining:
                continue
            resolution = _resolution_from_payload(item, vocabulary)
            resolutions[token] = resolution
            with self._cache_lock:
                self._cache[token] = resolution
            remaining.remove(token)
            candidate = next(candidate for candidate in unique_candidates if candidate.token == token)
            self._emit_progress("done", candidate, resolution)

        if remaining:
            raise RuntimeError(
                "IO name vocabulary batch LLM response omitted resolutions for tokens: "
                + ", ".join(sorted(remaining))
            )
        return resolutions

    def _emit_progress(
        self,
        stage: str,
        candidate: IONameCandidate,
        resolution: Optional[IONameResolution],
    ) -> None:
        if self.progress is not None:
            self.progress(stage, candidate, resolution)


def _resolution_from_payload(
    payload: Dict[str, Any],
    vocabulary: IONameVocabulary,
) -> IONameResolution:
    action = str(payload.get("action") or "merge_existing")
    if action not in {
        "alias_existing",
        "create_new",
        "merge_existing",
        "exclude_non_runtime",
    }:
        action = "merge_existing"

    target = payload.get("target") or payload.get("normalized_value") or payload.get("normalized_name")
    normalized_value = str(target).strip() if target is not None else None
    if action == "exclude_non_runtime":
        normalized_value = None
    elif action in {"alias_existing", "merge_existing"}:
        if normalized_value not in vocabulary.term_names():
            normalized_value = vocabulary.closest_term(normalized_value or "") or normalized_value
    elif action == "create_new" and vocabulary.is_full():
        action = "merge_existing"
        normalized_value = vocabulary.closest_term(normalized_value or "")

    return IONameResolution(
        action=action,
        normalized_value=normalized_value,
        confidence=_coerce_confidence(payload.get("confidence")),
        reason=str(payload.get("reason") or ""),
        forced_merge=bool(payload.get("forced_merge")) or action == "merge_existing",
    )


def _coerce_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


_IO_NAME_RESOLVER_PROMPT = """Resolve a new Skill input/output name against io_name_vocab.

Return JSON only:
{
  "action": "alias_existing|create_new|merge_existing|exclude_non_runtime",
  "target": "existing_or_new_vocab_term_or_null",
  "confidence": 0.0,
  "reason": "short explanation"
}

Use input/output names as semantic vocab terms for graph linking. The type field
is only the data representation, not the semantic role.
"""

_IO_NAME_BATCH_RESOLVER_PROMPT = """Resolve new Skill input/output names against io_name_vocab.

Return JSON only:
{
  "resolutions": [
    {
      "token": "candidate_token",
      "action": "alias_existing|create_new|merge_existing|exclude_non_runtime",
      "target": "existing_or_new_vocab_term_or_null",
      "confidence": 0.0,
      "reason": "short explanation"
    }
  ]
}

The candidates come from one Skill's combined inputs and outputs. Use that
shared context to choose consistent semantic names. The type field is only the
data representation, not the semantic role.
"""