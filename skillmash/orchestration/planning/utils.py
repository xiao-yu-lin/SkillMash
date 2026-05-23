"""Utility helpers for orchestration planning."""

from __future__ import annotations

from typing import Any

from skillmash.lexicon import ArtifactLexicon
from skillmash.orchestration.planning.constants import DEFAULT_STOP_TERMS

_PLANNING_LEXICON = ArtifactLexicon.create(
    stop_terms=DEFAULT_STOP_TERMS,
    min_token_length=2,
)


def tokenize(
    text: str,
    *,
    stop_terms: set[str] = DEFAULT_STOP_TERMS,
) -> set[str]:
    if stop_terms == DEFAULT_STOP_TERMS:
        return _PLANNING_LEXICON.tokenize(text)
    return ArtifactLexicon.create(
        stop_terms=stop_terms,
        min_token_length=2,
    ).tokenize(text)


def skill_id(node_id: Any) -> str:
    text = str(node_id or "")
    return text.removeprefix("skill:")


def clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
