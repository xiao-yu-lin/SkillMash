"""Dynamic vocabularies for semantic representation fields."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Protocol, Set, Union


@dataclass(frozen=True)
class SemanticCandidate:
    raw_value: str
    token: str
    field: str
    description: str
    skill_id: str
    path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_value": self.raw_value,
            "token": self.token,
            "field": self.field,
            "description": self.description,
            "skill_id": self.skill_id,
            "path": self.path,
        }


@dataclass(frozen=True)
class SemanticResolution:
    action: str
    normalized_value: Optional[str]
    confidence: float
    reason: str = ""
    forced_merge: bool = False


class SemanticResolver(Protocol):
    def resolve(
        self,
        candidate: SemanticCandidate,
        vocabulary: "SemanticVocabulary",
    ) -> SemanticResolution:
        ...


@dataclass
class SemanticVocabTerm:
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
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticVocabTerm":
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


class SemanticVocabulary:
    """Mutable bounded vocabulary for a semantic field."""

    def __init__(
        self,
        *,
        version: str,
        max_vocab_size: int,
        terms: Optional[List[SemanticVocabTerm]] = None,
    ) -> None:
        self.version = version
        self.max_vocab_size = max(1, max_vocab_size)
        self._terms: Dict[str, SemanticVocabTerm] = {}
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
    ) -> "SemanticVocabulary":
        terms: Dict[str, SemanticVocabTerm] = {}
        for alias, name in aliases.items():
            term = terms.setdefault(name, SemanticVocabTerm(name=name))
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
    ) -> "SemanticVocabulary":
        return cls(
            version=str(data.get("version") or default_version),
            max_vocab_size=int(data.get("max_vocab_size") or default_max_vocab_size),
            terms=[
                SemanticVocabTerm.from_dict(item)
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
    ) -> "SemanticVocabulary":
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

    def add_term(self, term: SemanticVocabTerm) -> None:
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
                term = SemanticVocabTerm(name=name)
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
            return max(self._terms, key=lambda name: _term_similarity(token, name))

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


class HeuristicSemanticResolver:
    """Deterministic fallback for semantic vocabularies."""

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
        candidate: SemanticCandidate,
        vocabulary: SemanticVocabulary,
    ) -> SemanticResolution:
        if self._is_non_runtime(candidate):
            return SemanticResolution(
                action="exclude_non_runtime",
                normalized_value=None,
                confidence=0.88,
                reason="Value or description indicates logging, analytics, or original-copy data.",
            )
        if not vocabulary.is_full():
            return SemanticResolution(
                action="create_new",
                normalized_value=candidate.token,
                confidence=0.7,
                reason="No existing alias matched and vocabulary has remaining capacity.",
            )
        target = vocabulary.closest_term(candidate.token)
        return SemanticResolution(
            action="merge_existing",
            normalized_value=target,
            confidence=0.5,
            reason="Vocabulary is full; merged to the closest existing term.",
            forced_merge=True,
        )

    def _is_non_runtime(self, candidate: SemanticCandidate) -> bool:
        text = f"{candidate.token} {candidate.description}".lower()
        return any(hint in text for hint in self._NON_RUNTIME_HINTS)


def _term_similarity(left: str, right: str) -> float:
    left_parts = set(left.split("_"))
    right_parts = set(right.split("_"))
    overlap = len(left_parts & right_parts) / max(1, len(left_parts | right_parts))
    ratio = SequenceMatcher(None, left, right).ratio()
    return max(overlap, ratio)
