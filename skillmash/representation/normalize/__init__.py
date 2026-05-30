"""Normalization infrastructure."""

from skillmash.representation.normalize.base_vocab import (
    BaseCandidate,
    BaseResolution,
    BaseResolver,
    BaseVocabTerm,
    BaseVocabulary,
    HeuristicBaseResolver,
    NON_RUNTIME_HINTS,
    term_similarity,
)
from skillmash.representation.normalize.io_name_vocab import (
    HeuristicIONameResolver,
    IONameCandidate,
    IONameResolution,
    IONameResolver,
    IONameVocabTerm,
    IONameVocabulary,
    LLMIONameResolver,
)
from skillmash.representation.normalize.semantic_vocab import (
    HeuristicSemanticResolver,
    SemanticCandidate,
    SemanticResolution,
    SemanticResolver,
    SemanticVocabTerm,
    SemanticVocabulary,
)
from skillmash.representation.normalize.normalizer import SkillRepresentationNormalizer

__all__ = [
    # Base vocabulary
    "BaseCandidate",
    "BaseResolution",
    "BaseResolver",
    "BaseVocabTerm",
    "BaseVocabulary",
    "HeuristicBaseResolver",
    "NON_RUNTIME_HINTS",
    "term_similarity",
    # I/O name vocabulary
    "HeuristicIONameResolver",
    "IONameCandidate",
    "IONameResolution",
    "IONameResolver",
    "IONameVocabTerm",
    "IONameVocabulary",
    "LLMIONameResolver",
    # Semantic vocabulary
    "HeuristicSemanticResolver",
    "SemanticCandidate",
    "SemanticResolution",
    "SemanticResolver",
    "SemanticVocabTerm",
    "SemanticVocabulary",
    # Normalizer
    "SkillRepresentationNormalizer",
]