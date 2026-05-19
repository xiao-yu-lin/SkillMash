"""Dynamic vocabulary for normalizing Skill input/output names."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Union

from skillmash.representation.llm import (
    LLMConfig,
    create_openai_client,
    extract_message_content,
    safe_model_dump,
)
from skillmash.representation.models import NormalizationConfig


@dataclass(frozen=True)
class IONameCandidate:
    raw_name: str
    token: str
    direction: str
    data_type: str
    description: str
    skill_id: str
    path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_name": self.raw_name,
            "token": self.token,
            "direction": self.direction,
            "type": self.data_type,
            "description": self.description,
            "skill_id": self.skill_id,
            "path": self.path,
        }


@dataclass(frozen=True)
class IONameResolution:
    action: str
    normalized_name: Optional[str]
    confidence: float
    reason: str = ""
    forced_merge: bool = False


class IONameResolver(Protocol):
    def resolve(
        self,
        candidate: IONameCandidate,
        vocabulary: "IONameVocabulary",
    ) -> IONameResolution:
        ...


@dataclass
class IONameVocabTerm:
    name: str
    aliases: Set[str] = field(default_factory=set)
    definition: str = ""
    allowed_types: Set[str] = field(default_factory=set)
    examples: List[str] = field(default_factory=list)
    count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "definition": self.definition,
            "aliases": sorted(alias for alias in self.aliases if alias != self.name),
            "allowed_types": sorted(self.allowed_types),
            "examples": list(self.examples),
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IONameVocabTerm":
        name = str(data.get("name") or "").strip()
        aliases = {str(alias).strip() for alias in data.get("aliases", []) if str(alias).strip()}
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
    """Mutable io_name_vocab with bounded canonical terms and unbounded aliases."""

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
                key=lambda name: _term_similarity(token, name),
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


class HeuristicIONameResolver:
    """Deterministic fallback resolver used when no LLM resolver is configured."""

    _NON_RUNTIME_HINTS = {
        "analytics",
        "log",
        "logging",
        "metric",
        "metrics",
        "origin",
        "original",
        "raw",
        "stat",
        "stats",
        "telemetry",
        "trace",
        "tracking",
    }

    def resolve(
        self,
        candidate: IONameCandidate,
        vocabulary: IONameVocabulary,
    ) -> IONameResolution:
        if self._is_non_runtime(candidate):
            return IONameResolution(
                action="exclude_non_runtime",
                normalized_name=None,
                confidence=0.88,
                reason="Name or description indicates logging, analytics, or original-copy data.",
            )

        if not vocabulary.is_full():
            return IONameResolution(
                action="create_new",
                normalized_name=candidate.token,
                confidence=0.7,
                reason="No existing alias matched and vocabulary has remaining capacity.",
            )

        target = vocabulary.closest_term(candidate.token)
        return IONameResolution(
            action="merge_existing",
            normalized_name=target,
            confidence=0.5,
            reason="Vocabulary is full; merged to the closest existing term.",
            forced_merge=True,
        )

    def _is_non_runtime(self, candidate: IONameCandidate) -> bool:
        text = f"{candidate.token} {candidate.description}".lower()
        return any(hint in text for hint in self._NON_RUNTIME_HINTS)


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

    def resolve(
        self,
        candidate: IONameCandidate,
        vocabulary: IONameVocabulary,
    ) -> IONameResolution:
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
            print("call llm")
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
            print(context)
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
        self._emit_progress("done", candidate, resolution)
        return resolution

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

    target = payload.get("target") or payload.get("normalized_name")
    normalized_name = str(target).strip() if target is not None else None
    if action == "exclude_non_runtime":
        normalized_name = None
    elif action in {"alias_existing", "merge_existing"}:
        if normalized_name not in vocabulary.term_names():
            normalized_name = vocabulary.closest_term(normalized_name or "") or normalized_name
    elif action == "create_new" and vocabulary.is_full():
        action = "merge_existing"
        normalized_name = vocabulary.closest_term(normalized_name or "")

    return IONameResolution(
        action=action,
        normalized_name=normalized_name,
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


def _term_similarity(left: str, right: str) -> float:
    left_parts = set(left.split("_"))
    right_parts = set(right.split("_"))
    overlap = len(left_parts & right_parts) / max(1, len(left_parts | right_parts))
    ratio = SequenceMatcher(None, left, right).ratio()
    return max(overlap, ratio)


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
