"""Shared lexical seam for artifact names and text terms."""

from __future__ import annotations

import re
import unicodedata
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

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_CHUNK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


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
            stop_terms=frozenset(normalize_text(term) for term in stop_terms),
            min_token_length=max(1, int(min_token_length)),
            generic_io_names=frozenset(
                normalize_text(name) for name in generic_io_names
            ),
        )

    def tokenize(self, text: str) -> set[str]:
        normalized = normalize_text(text)
        tokens = set()

        for token in _ASCII_TOKEN_RE.findall(normalized):
            if len(token) >= self.min_token_length and token not in self.stop_terms:
                tokens.add(token)

        for chunk in _CJK_CHUNK_RE.findall(normalized):
            if len(chunk) < 2:
                continue
            tokens.update(_cjk_terms(chunk))

        return tokens

    def is_generic_io_name(self, name: str) -> bool:
        return normalize_text(name) in self.generic_io_names


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text)).lower()


def _cjk_terms(chunk: str) -> set[str]:
    terms = {chunk}
    if len(chunk) == 2:
        return terms
    for index in range(len(chunk) - 1):
        terms.add(chunk[index : index + 2])
    return terms
