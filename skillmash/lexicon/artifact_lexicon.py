"""Shared lexical seam for artifact names and text terms."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


DEFAULT_GRAPH_STOP_TERMS = frozenset(
    {
        "and",
        "are",
        "for",
        "from",
        "into",
        "the",
        "this",
        "that",
        "with",
    }
)

DEFAULT_PLANNING_STOP_TERMS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "the",
        "this",
        "that",
        "to",
        "with",
    }
)

DEFAULT_GRAPH_CANDIDATE_GENERIC_IO_NAMES = frozenset(
    {
        "dependencies",
        "code",
        "existing_apis",
        "path",
        "review_report",
        "use_case_description",
    }
)

DEFAULT_GRAPH_INDEX_GENERIC_IO_NAMES = frozenset(
    {
        "dependencies",
        "existing_apis",
        "review_report",
        "use_case_description",
    }
)


@dataclass(frozen=True)
class ArtifactLexicon:
    """Tokenization and generic-name rules for one lexical profile."""

    stop_terms: frozenset[str]
    min_token_length: int
    generic_io_names: frozenset[str] = frozenset()

    @classmethod
    def create(
        cls,
        *,
        stop_terms: Iterable[str],
        min_token_length: int,
        generic_io_names: Iterable[str] = (),
    ) -> "ArtifactLexicon":
        return cls(
            stop_terms=frozenset(str(term).lower() for term in stop_terms),
            min_token_length=max(1, int(min_token_length)),
            generic_io_names=frozenset(str(name).lower() for name in generic_io_names),
        )

    def tokenize(self, text: str) -> set[str]:
        return {
            token
            for token in re.split(r"[^a-z0-9]+", str(text).lower())
            if len(token) >= self.min_token_length and token not in self.stop_terms
        }

    def is_generic_io_name(self, name: str) -> bool:
        return str(name).lower() in self.generic_io_names
