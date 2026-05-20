"""Dynamic vocabulary for normalizing Skill input/output names.

This module provides specialized vocabulary classes for normalizing
I/O names (like 'query', 'paper', 'summary'). It builds on the
shared base vocabulary infrastructure from base_vocab.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Set, Union

from skillmash.representation.base_vocab import (
    BaseCandidate,
    BaseResolution,
    BaseResolver,
    BaseVocabTerm,
    HeuristicBaseResolver,
    NON_RUNTIME_HINTS,
    term_similarity,
)
from skillmash.representation.llm import (
    LLMConfig,
    create_openai_client,
    extract_message_content,
    safe_model_dump,
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


@dataclass
class IONameVocabTerm(BaseVocabTerm):
    """I/O name vocabulary term with allowed types constraint."""

    allowed_types: Set[str] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["allowed_types"] = sorted(self.allowed_types)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IONameVocabTerm":
        name = str(data.get("name") or "").strip()
        aliases = {
            str(alias).strip()
            for alias in data.get("aliases", [])
            if str(alias).strip()
        }
        allowed_types = {
            str(item).strip()
            for item in data.get("allowed_types", [])
            if str(item).strip()
        }
        return cls(
            name=name,
            aliases=aliases,
            definition=str(data.get("definition") or ""),
            allowed_types=allowed_types,
            examples=[str(item) for item in data.get("examples", [])],
            count=int(data.get("count") or 0),
        )


class IONameVocabulary:
    """Mutable io_name_vocab with bounded canonical terms and unbounded aliases.

    This vocabulary manages I/O name terms used for graph linking.
    Unlike BaseVocabulary, it supports allowed_types constraints.
    """

    def __init__(
        self,
        *,
        version: str,
        max_vocab_size: int,
        terms: Optional[List[IONameVocabTerm]] = None,
    ) -> None:
        self.version = version
        self.max_vocab_size = max(1, max_vocab_size)
        self._terms: Dict[str, IONameVocabTerm] = {}
        self._aliases: Dict[str, str] = {}
        self._lock = RLock()
        for term in terms or []:
            self.add_term(term)

    @classmethod
    def from_config(cls, config: NormalizationConfig) -> "IONameVocabulary":
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
        return cls(
            version=str(data.get("version") or config.io_name_vocab_version),
            max_vocab_size=int(data.get("max_vocab_size") or config.max_vocab_size),
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
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data, config)

    def save(self, path: Union[Path, str]) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_term(self, term: IONameVocabTerm) -> None:
        if not term.name:
            return
        with self._lock:
            self._terms[term.name] = term
            self._aliases[term.name] = term.name
            for alias in term.aliases:
                if alias:
                    self._aliases[alias] = term.name

    def lookup(self, token: str) -> Optional[str]:
        with self._lock:
            return self._aliases.get(token)

    def size(self) -> int:
        with self._lock:
            return len(self._terms)

    def is_full(self) -> bool:
        return self.size() >= self.max_vocab_size

    def term_names(self) -> List[str]:
        with self._lock:
            return sorted(self._terms)

    def add_alias(
        self,
        alias: str,
        target: str,
        *,
        data_type: str = "",
        example: str = "",
    ) -> None:
        with self._lock:
            term = self._terms.get(target)
            if term is None:
                return
            if alias and alias != target:
                term.aliases.add(alias)
                self._aliases[alias] = target
            if data_type:
                term.allowed_types.add(data_type)
            if example and example not in term.examples:
                term.examples.append(example)
            term.count += 1

    def create_term(
        self,
        name: str,
        *,
        alias: str = "",
        data_type: str = "",
        example: str = "",
    ) -> str:
        with self._lock:
            if name not in self._terms and len(self._terms) >= self.max_vocab_size:
                return self.closest_term(name) or name
            term = self._terms.get(name)
            if term is None:
                term = IONameVocabTerm(name=name)
                self._terms[name] = term
                self._aliases[name] = name
            if alias and alias != name:
                term.aliases.add(alias)
                self._aliases[alias] = name
            if data_type:
                term.allowed_types.add(data_type)
            if example:
                term.examples.append(example)
            term.count += 1
            return name

    def closest_term(self, token: str) -> Optional[str]:
        with self._lock:
            if not self._terms:
                return None
            return max(
                self._terms,
                key=lambda name: term_similarity(token, name),
            )

    def resolver_context(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": self.version,
                "max_vocab_size": self.max_vocab_size,
                "is_full": len(self._terms) >= self.max_vocab_size,
                "terms": [
                    self._terms[name].to_dict()
                    for name in sorted(self._terms)
                ],
            }

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": self.version,
                "max_vocab_size": self.max_vocab_size,
                "terms": [
                    self._terms[name].to_dict()
                    for name in sorted(self._terms)
                ],
            }


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


class OpenAICompatibleIONameResolver:
    """LLM-backed resolver for unseen io_name_vocab terms."""

    def __init__(
        self,
        config: LLMConfig,
        progress: Optional[Callable[[str, IONameCandidate, Optional[IONameResolution]], None]] = None,
    ) -> None:
        self.config = config
        self.client = create_openai_client(config)
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
                "If the candidate is only for logs, statistics, telemetry, tracing, or original-copy bookkeeping, use exclude_non_runtime.",
                "If the candidate is synonymous with an existing term, use alias_existing and set target to that term.",
                "If the vocabulary is not full and the candidate is a genuinely new runtime semantic role, use create_new.",
                "If the vocabulary is full, do not use create_new; use merge_existing or exclude_non_runtime.",
            ],
        }
        self._emit_progress("start", candidate, None)
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=self.config.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": _IO_NAME_RESOLVER_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(context, ensure_ascii=False, indent=2),
                    },
                ],
            )
        except Exception as exc:
            raise RuntimeError(f"IO name vocabulary LLM request failed: {exc}") from exc

        choice = response.choices[0]
        content = extract_message_content(choice.message)
        if not content:
            raise RuntimeError(
                "IO name vocabulary LLM response content is empty. "
                f"finish_reason={getattr(choice, 'finish_reason', None)!r}; "
                f"message={safe_model_dump(choice.message)}"
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
                "If a candidate is only for logs, statistics, telemetry, tracing, or original-copy bookkeeping, use exclude_non_runtime.",
                "If a candidate is synonymous with an existing term, use alias_existing and set target to that term.",
                "If the vocabulary is not full and a candidate is a genuinely new runtime semantic role, use create_new.",
                "If the vocabulary is full, do not use create_new; use merge_existing or exclude_non_runtime.",
                "Use the candidate list as one Skill's combined input/output context; resolve names consistently across directions.",
            ],
        }
        for candidate in unique_candidates:
            self._emit_progress("start", candidate, None)
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=self.config.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": _IO_NAME_BATCH_RESOLVER_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(context, ensure_ascii=False, indent=2),
                    },
                ],
            )
        except Exception as exc:
            raise RuntimeError(f"IO name vocabulary batch LLM request failed: {exc}") from exc

        choice = response.choices[0]
        content = extract_message_content(choice.message)
        if not content:
            raise RuntimeError(
                "IO name vocabulary batch LLM response content is empty. "
                f"finish_reason={getattr(choice, 'finish_reason', None)!r}; "
                f"message={safe_model_dump(choice.message)}"
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
