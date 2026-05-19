"""Dynamic vocabularies for semantic representation fields.

This module provides specialized vocabulary classes for normalizing
semantic fields like Skill tasks/capabilities. It builds on the
shared base vocabulary infrastructure from base_vocab.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from skillmash.representation.base_vocab import (
    BaseCandidate,
    BaseResolution,
    BaseResolver,
    BaseVocabTerm,
    BaseVocabulary,
    HeuristicBaseResolver,
)


@dataclass(frozen=True)
class SemanticCandidate(BaseCandidate):
    """Candidate for semantic vocabulary resolution with field context."""

    field: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["field"] = self.field
        return data


# SemanticResolution is identical to BaseResolution, use it as alias.
SemanticResolution = BaseResolution

# SemanticResolver protocol is identical to BaseResolver, use it as alias.
SemanticResolver = BaseResolver

# SemanticVocabTerm is identical to BaseVocabTerm, use it as alias.
SemanticVocabTerm = BaseVocabTerm


class SemanticVocabulary(BaseVocabulary):
    """Mutable bounded vocabulary for semantic fields like task/capability terms.

    This class extends BaseVocabulary without additional fields, providing
    a type-specific name for clarity in the representation extraction pipeline.
    """

    pass


class HeuristicSemanticResolver(HeuristicBaseResolver):
    """Deterministic fallback resolver for semantic vocabularies."""

    def resolve(
        self,
        candidate: SemanticCandidate,
        vocabulary: SemanticVocabulary,
    ) -> SemanticResolution:
        return self.resolve_base(
            token=candidate.token,
            description=candidate.description,
            vocabulary=vocabulary,
        )