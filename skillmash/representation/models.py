"""Data contracts for Skill representation extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union


@dataclass(frozen=True)
class SkillFolder:
    """A discovered Skill folder with a SKILL.md entrypoint."""

    id_hint: str
    path: Path
    entry: Path
    relative_path: str


@dataclass(frozen=True)
class ExtractionDiagnostic:
    """Structured diagnostic emitted during representation extraction."""

    stage: str
    severity: str
    code: str
    message: str
    skill_id: Optional[str] = None
    path: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "path": self.path,
            "stage": self.stage,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class RawSkillManifest:
    """Parsed SKILL.md content before LLM schema extraction."""

    folder: SkillFolder
    frontmatter: Dict[str, Any]
    body: str
    body_sha256: str
    diagnostics: List[ExtractionDiagnostic] = field(default_factory=list)


@dataclass(frozen=True)
class ParameterSpec:
    """A Skill input parameter."""

    name: str
    type: str
    required: bool = True
    description: str = ""
    default: Any = None
    schema_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
            "default": self.default,
            "schema_ref": self.schema_ref,
        }


@dataclass(frozen=True)
class ArtifactSpec:
    """A Skill output artifact."""

    name: str
    type: str
    description: str = ""
    schema_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "schema_ref": self.schema_ref,
        }


@dataclass(frozen=True)
class Condition:
    """A precondition or postcondition."""

    type: str
    expression: str
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "expression": self.expression,
            "description": self.description,
        }


@dataclass(frozen=True)
class ExtractedSkillSchema:
    """LLM-extracted candidate schema before deterministic normalization."""

    description: str = ""
    tasks: List[str] = field(default_factory=list)
    inputs: List[Union[ParameterSpec, Dict[str, Any]]] = field(default_factory=list)
    outputs: List[Union[ArtifactSpec, Dict[str, Any]]] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    preconditions: List[Union[Condition, Dict[str, Any]]] = field(default_factory=list)
    postconditions: List[Union[Condition, Dict[str, Any]]] = field(default_factory=list)
    confidence: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillRepresentation:
    """Normalized Skill representation v1."""

    id: str
    name: str
    description: str
    version: str
    tasks: List[str]
    inputs: List[ParameterSpec]
    outputs: List[ArtifactSpec]
    preconditions: List[Condition]
    postconditions: List[Condition]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "tasks": list(self.tasks),
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "preconditions": [item.to_dict() for item in self.preconditions],
            "postconditions": [item.to_dict() for item in self.postconditions],
        }


@dataclass(frozen=True)
class NormalizationConfig:
    """Configuration and vocabularies used by the deterministic normalizer.

    This object owns the normalizer defaults, vocabulary versions, and optional
    alias maps. `SkillRepresentation` keeps only normalized `name` and `type`
    values; the evidence for each choice is stored in `NormalizationDecision`.

    `name` and `type` have different roles:
    - `name` is the I/O semantic term used by graph construction.
    - `type` is the data representation or carrier, such as text, pdf, csv,
      or markdown.
    """

    # Representation contract version consumed by downstream graph builders.
    schema_version: str = "skill-representation-v1"

    # Dynamic vocabulary version for normalized I/O semantic names.
    io_name_vocab_version: str = "io-name-vocab-v1"

    # Dynamic vocabulary for normalized Skill task/capability terms.
    task_vocab_version: str = "task-vocab-v1"

    # Controlled DataType vocabulary version.
    data_type_vocab_version: str = "data-type-v1"

    # Maximum number of canonical I/O name terms.
    max_vocab_size: int = 8

    # task_vocab has its own capacity so graph-facing capability terms can grow
    # independently from I/O role names.
    max_task_vocab_size: int = 32

    # Default Skill version when frontmatter does not declare one.
    default_version: str = "1.0.0"

    # Default input created when the extractor returns no inputs.
    default_input_name: str = "input"

    # Default type for generated fallback inputs.
    default_input_type: str = "text"

    # Default output created when the extractor returns no outputs.
    default_output_name: str = "result"

    # Type used when a data representation cannot be recognized.
    unknown_type: str = "unknown"

    # Controlled DataType vocabulary for representation formats/carriers.
    data_type_vocab: frozenset = frozenset(
        {
            "text",
            "markdown",
            "json",
            "csv",
            "pdf",
            "html",
            "docx",
            "pptx",
            "xlsx",
            "png",
            "jpg",
            "svg",
            "url",
            "file",
            "path",
            "audio",
            "video",
            "unknown",
        }
    )

    # DataType aliases collapse free-form LLM types into data_type_vocab.
    data_type_aliases: Dict[str, str] = field(
        default_factory=lambda: {
            "natural_language": "text",
            "natural_language_query": "text",
            "plain_text": "text",
            "query": "text",
            "summary": "text",
            "report": "markdown",
            "link": "url",
            "uri": "url",
            "webpage": "url",
            "spreadsheet": "csv",
            "dataframe": "csv",
            "md": "markdown",
            "json_object": "json",
            "slides": "pptx",
            "presentation": "pptx",
            "powerpoint": "pptx",
            "ppt": "pptx",
            "jpeg": "jpg",
            "source_code": "text",
            "script": "text",
            "program": "text",
            "chart": "png",
            "flowchart": "svg",
            "mermaid": "text",
            "paper": "pdf",
            "academic_paper": "pdf",
            "publication": "pdf",
        }
    )

    # I/O name aliases start empty so the configured resolver can grow io_name_vocab.
    io_name_aliases: Dict[str, str] = field(default_factory=dict)

    # Seed task/capability aliases. These are semantic actions used for
    # retrieval and planning, not data carriers.
    task_aliases: Dict[str, str] = field(
        default_factory=lambda: {
            "web_search": "search",
            "search_web": "search",
            "find": "search",
            "lookup": "search",
            "research": "search",
            "summarise": "summarize",
            "summarisation": "summarize",
            "summarization": "summarize",
            "summary": "summarize",
            "translate_text": "translate",
            "translation": "translate",
            "extract_data": "extract",
            "data_extraction": "extract",
            "parse": "extract",
            "analyze_data": "analyze",
            "analysis": "analyze",
            "generate_report": "write",
            "write_report": "write",
            "draft": "write",
            "create": "generate",
            "render": "generate",
            "convert_format": "convert",
            "format_conversion": "convert",
        }
    )


@dataclass(frozen=True)
class NormalizationDecision:
    """Trace record for a normalization choice kept outside representations."""

    skill_id: str
    path: Optional[str]
    direction: str
    field: str
    raw_value: str
    token: str
    normalized_value: str
    method: str
    vocab: str
    vocab_version: str
    confidence: float
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "path": self.path,
            "direction": self.direction,
            "field": self.field,
            "raw_value": self.raw_value,
            "token": self.token,
            "normalized_value": self.normalized_value,
            "method": self.method,
            "vocab": self.vocab,
            "vocab_version": self.vocab_version,
            "confidence": self.confidence,
            "details": self.details,
        }


@dataclass(frozen=True)
class NormalizationResult:
    representation: SkillRepresentation
    diagnostics: List[ExtractionDiagnostic]
    decisions: List[NormalizationDecision] = field(default_factory=list)


@dataclass(frozen=True)
class RepresentationExtractionResult:
    representations: List[SkillRepresentation]
    diagnostics: List[ExtractionDiagnostic]
    normalization_decisions: List[NormalizationDecision] = field(default_factory=list)
    io_name_vocab: Dict[str, Any] = field(default_factory=dict)
    task_vocab: Dict[str, Any] = field(default_factory=dict)


class SkillSchemaExtractor(Protocol):
    """Protocol for LLM-backed schema extractors."""

    def extract(self, manifest: RawSkillManifest) -> ExtractedSkillSchema:
        ...
