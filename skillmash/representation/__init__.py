"""Skill representation extraction.

This module provides a stage-based pipeline for extracting structured
representations from Skill folders containing SKILL.md files.

Stages:
- scan: Discover Skill folders containing SKILL.md entrypoints
- parse: Parse SKILL.md into frontmatter and body
- extract: LLM schema extraction from parsed content
- normalize: Normalize I/O names, types, and identities via vocabularies
- write: Write extraction artifacts to disk
"""

# Models - core data contracts
from skillmash.representation.models import (
    ArtifactSpec,
    ExtractedSkillSchema,
    ExtractionDiagnostic,
    NormalizationConfig,
    NormalizationDecision,
    NormalizationResult,
    ParameterSpec,
    RawSkillManifest,
    RepresentationExtractionResult,
    SkillFolder,
    SkillRepresentation,
)

# Stage: scan
from skillmash.representation.scan import SkillFolderScanner

# Stage: parse
from skillmash.representation.parse import SkillManifestParser

# Stage: extract
from skillmash.representation.extract import (
    LLMSchemaExtractor,
    schema_from_llm_payload,
)

# Stage: normalize
from skillmash.representation.normalize import (
    # Metadata normalization
    MetadataNormalizer,
    # Base vocabulary
    BaseCandidate,
    BaseResolution,
    BaseResolver,
    BaseVocabTerm,
    BaseVocabulary,
    HeuristicBaseResolver,
    NON_RUNTIME_HINTS,
    term_similarity,
    # I/O name vocabulary
    HeuristicIONameResolver,
    IONameCandidate,
    IONameResolution,
    IONameResolver,
    IONameVocabTerm,
    IONameVocabulary,
    LLMIONameResolver,
    # Normalizer
    SkillRepresentationNormalizer,
)

# Stage: write
from skillmash.representation.write import (
    write_extraction_result,
    write_json_file,
)

# Pipeline
from skillmash.representation.pipeline import RepresentationExtractor

# LLM config
from skillmash.common.llm import LLMConfig

__all__ = [
    # Models
    "ArtifactSpec",
    "MetadataNormalizer",
    "ExtractedSkillSchema",
    "ExtractionDiagnostic",
    "NormalizationConfig",
    "NormalizationDecision",
    "NormalizationResult",
    "ParameterSpec",
    "RawSkillManifest",
    "RepresentationExtractionResult",
    "SkillFolder",
    "SkillRepresentation",
    # Stage: scan
    "SkillFolderScanner",
    # Stage: parse
    "SkillManifestParser",
    # Stage: extract
    "LLMSchemaExtractor",
    "schema_from_llm_payload",
    # Stage: normalize - base vocabulary
    "BaseCandidate",
    "BaseResolution",
    "BaseResolver",
    "BaseVocabTerm",
    "BaseVocabulary",
    "HeuristicBaseResolver",
    "NON_RUNTIME_HINTS",
    "term_similarity",
    # Stage: normalize - I/O name vocabulary
    "HeuristicIONameResolver",
    "IONameCandidate",
    "IONameResolution",
    "IONameResolver",
    "IONameVocabTerm",
    "IONameVocabulary",
    "LLMIONameResolver",
    # Stage: normalize - normalizer
    "SkillRepresentationNormalizer",
    # Stage: write
    "write_extraction_result",
    "write_json_file",
    # Pipeline
    "RepresentationExtractor",
    # LLM config
    "LLMConfig",
]