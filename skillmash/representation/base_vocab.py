"""Base classes and utilities for dynamic vocabularies."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Protocol, Set, Union


# Shared constant for non-runtime field detection.
NON_RUNTIME_HINTS = frozenset({
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
})


def term_similarity(left: str, right: str) -> float:
    """Compute similarity between two vocabulary terms.

    Uses both word overlap (Jaccard on split parts) and sequence matching.
    """
    left_parts = set(left.split("_"))
    right_parts = set(right.split("_"))
    overlap = len(left_parts & right_parts) / max(1, len(left_parts | right_parts))
    ratio = SequenceMatcher(None, left, right).ratio()
    return max(overlap, ratio)


@dataclass(frozen=True)
class BaseCandidate:
    """Base class for vocabulary resolution candidates."""

    raw_value: str
    token: str
    description: str
    skill_id: str
    path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_value": self.raw_value,
            "token": self.token,
            "description": self.description,
            "skill_id": self.skill_id,
            "path": self.path,
        }


@dataclass(frozen=True)
class BaseResolution:
    """Base class for vocabulary resolution results."""

    action: str
    normalized_value: Optional[str]
    confidence: float
    reason: str = ""
    forced_merge: bool = False


class BaseResolver(Protocol):
    """Protocol for vocabulary resolvers."""

    def resolve(self, candidate: Any, vocabulary: Any) -> BaseResolution:
        ...

    def resolve_many(
        self,
        candidates: List[Any],
        vocabulary: Any,
    ) -> Dict[str, BaseResolution]:
        ...


@dataclass
class BaseVocabTerm:
    """Base class for vocabulary terms."""

    name: str
    aliases: Set[str] = field(default_factory=set)
    definition: str = ""
    examples: List[str] = field(default_factory=list)
    count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "definition": self.definition,
            "aliases": sorted(alias for alias in self.aliases if alias != self.name),
            "examples": list(self.examples),
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseVocabTerm":
        name = str(data.get("name") or "").strip()
        aliases = {
            str(alias).strip()
            for alias in data.get("aliases", [])
            if str(alias).strip()
        }
        return cls(
            name=name,
            aliases=aliases,
            definition=str(data.get("definition") or ""),
            examples=[str(item) for item in data.get("examples", [])],
            count=int(data.get("count") or 0),
        )


class BaseVocabulary:
    """Thread-safe mutable bounded vocabulary for semantic fields."""

    def __init__(
        self,
        *,
        version: str,
        max_vocab_size: int,
        terms: Optional[List[BaseVocabTerm]] = None,
    ) -> None:
        self.version = version
        self.max_vocab_size = max(1, max_vocab_size)
        self._terms: Dict[str, BaseVocabTerm] = {}
        self._aliases: Dict[str, str] = {}
        self._lock = RLock()
        for term in terms or []:
            self.add_term(term)

    @classmethod
    def from_aliases(
        cls,
        *,
        version: str,
        max_vocab_size: int,
        aliases: Dict[str, str],
    ) -> "BaseVocabulary":
        """Build vocabulary from an alias mapping."""
        terms: Dict[str, BaseVocabTerm] = {}
        for alias, name in aliases.items():
            term = terms.setdefault(name, BaseVocabTerm(name=name))
            term.aliases.add(alias)
        for name, term in list(terms.items()):
            term.aliases.discard(name)
        return cls(
            version=version,
            max_vocab_size=max_vocab_size,
            terms=list(terms.values()),
        )

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        default_version: str,
        default_max_vocab_size: int,
    ) -> "BaseVocabulary":
        return cls(
            version=str(data.get("version") or default_version),
            max_vocab_size=int(data.get("max_vocab_size") or default_max_vocab_size),
            terms=[
                BaseVocabTerm.from_dict(item)
                for item in data.get("terms", [])
                if isinstance(item, dict)
            ],
        )

    @classmethod
    def load(
        cls,
        path: Union[Path, str],
        *,
        default_version: str,
        default_max_vocab_size: int,
    ) -> "BaseVocabulary":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(
            data,
            default_version=default_version,
            default_max_vocab_size=default_max_vocab_size,
        )

    def save(self, path: Union[Path, str]) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_term(self, term: BaseVocabTerm) -> None:
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

    def add_alias(self, alias: str, target: str, *, example: str = "") -> None:
        with self._lock:
            term = self._terms.get(target)
            if term is None:
                return
            if alias and alias != target:
                term.aliases.add(alias)
                self._aliases[alias] = target
            if example and example not in term.examples:
                term.examples.append(example)
            term.count += 1

    def create_term(self, name: str, *, alias: str = "", example: str = "") -> str:
        with self._lock:
            if name not in self._terms and len(self._terms) >= self.max_vocab_size:
                return self.closest_term(name) or name
            term = self._terms.get(name)
            if term is None:
                term = BaseVocabTerm(name=name)
                self._terms[name] = term
                self._aliases[name] = name
            if alias and alias != name:
                term.aliases.add(alias)
                self._aliases[alias] = name
            if example:
                term.examples.append(example)
            term.count += 1
            return name

    def closest_term(self, token: str) -> Optional[str]:
        with self._lock:
            if not self._terms:
                return None
            return max(self._terms, key=lambda name: term_similarity(token, name))

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


class HeuristicBaseResolver:
    """Deterministic fallback resolver for vocabulary terms."""

    def is_non_runtime(self, token: str, description: str) -> bool:
        """Check if the term is likely a non-runtime field."""
        text = f"{token} {description}".lower()
        return any(hint in text for hint in NON_RUNTIME_HINTS)

    def resolve_base(
        self,
        token: str,
        description: str,
        vocabulary: BaseVocabulary,
    ) -> BaseResolution:
        """Common resolution logic for heuristic resolvers."""
        if self.is_non_runtime(token, description):
            return BaseResolution(
                action="exclude_non_runtime",
                normalized_value=None,
                confidence=0.88,
                reason="Value or description indicates logging, analytics, or original-copy data.",
            )
        if not vocabulary.is_full():
            return BaseResolution(
                action="create_new",
                normalized_value=token,
                confidence=0.7,
                reason="No existing alias matched and vocabulary has remaining capacity.",
            )
        target = vocabulary.closest_term(token)
        return BaseResolution(
            action="merge_existing",
            normalized_value=target,
            confidence=0.5,
            reason="Vocabulary is full; merged to the closest existing term.",
            forced_merge=True,
        )

    def resolve_many_base(
        self,
        candidates: List[Any],
        vocabulary: BaseVocabulary,
    ) -> Dict[str, BaseResolution]:
        """Resolve a batch locally while preserving one resolution per token."""
        resolutions: Dict[str, BaseResolution] = {}
        for candidate in candidates:
            token = str(getattr(candidate, "token", "") or "")
            if not token or token in resolutions:
                continue
            resolutions[token] = self.resolve_base(
                token=token,
                description=str(getattr(candidate, "description", "") or ""),
                vocabulary=vocabulary,
            )
        return resolutions
