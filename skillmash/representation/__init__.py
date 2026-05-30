"""Skill representation extraction."""

from skillmash.representation.base_vocab import (
    BaseCandidate,
    BaseResolution,
    BaseResolver,
    BaseVocabTerm,
    BaseVocabulary,
    HeuristicBaseResolver,
    NON_RUNTIME_HINTS,
    term_similarity,
)
from skillmash.representation.extractor import (
    LLMSchemaExtractor,
    schema_from_llm_payload,
)
from skillmash.representation.io_name_vocab import (
    HeuristicIONameResolver,
    IONameCandidate,
    IONameResolution,
    IONameResolver,
    IONameVocabTerm,
    IONameVocabulary,
    LLMIONameResolver,
)
from skillmash.common.llm import LLMConfig
from skillmash.representation.manifest import SkillManifestParser
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
from skillmash.representation.normalizer import SkillRepresentationNormalizer
from skillmash.representation.pipeline import RepresentationExtractor
from skillmash.representation.scanner import SkillFolderScanner
from skillmash.representation.semantic_vocab import (
    HeuristicSemanticResolver,
    SemanticCandidate,
    SemanticResolution,
    SemanticResolver,
    SemanticVocabTerm,
    SemanticVocabulary,
)
from skillmash.representation.writer import (
    write_extraction_result,
    write_json_file,
)

__all__ = [
    # Base vocabulary infrastructure
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
    # Models
    "ArtifactSpec",
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
    # Extractor
    "LLMSchemaExtractor",
    "schema_from_llm_payload",
    # LLM
    "LLMConfig",
    # Pipeline components
    "RepresentationExtractor",
    "SkillFolderScanner",
    "SkillManifestParser",
    "SkillRepresentationNormalizer",
    # Writer
    "write_extraction_result",
    "write_json_file",
]
