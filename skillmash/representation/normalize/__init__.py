"""Normalization infrastructure."""

from skillmash.representation.normalize.base_vocab import (
    BaseCandidate,
    BaseResolution,
    BaseResolver,
    BaseVocabTerm,
    BaseVocabulary,
    DynamicVocabulary,
    HeuristicBaseResolver,
    NON_RUNTIME_HINTS,
    StaticVocabulary,
    term_similarity,
)
from skillmash.representation.normalize.data_type_vocab import (
    DataTypeResolution,
    DataTypeVocabulary,
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
from skillmash.representation.normalize.metadata_normalizer import MetadataNormalizer
from skillmash.representation.normalize.normalizer import SkillRepresentationNormalizer

__all__ = [
    # Metadata normalization
    "MetadataNormalizer",
    # Base vocabulary
    "BaseCandidate",
    "BaseResolution",
    "BaseResolver",
    "BaseVocabTerm",
    "BaseVocabulary",
    "DynamicVocabulary",
    "HeuristicBaseResolver",
    "NON_RUNTIME_HINTS",
    "StaticVocabulary",
    "term_similarity",
    # DataType vocabulary
    "DataTypeResolution",
    "DataTypeVocabulary",
    # I/O name vocabulary
    "HeuristicIONameResolver",
    "IONameCandidate",
    "IONameResolution",
    "IONameResolver",
    "IONameVocabTerm",
    "IONameVocabulary",
    "LLMIONameResolver",
    # Normalizer
    "SkillRepresentationNormalizer",
]
