"""Data contracts for Skill representation extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


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
    skill_id: str | None = None
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
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
    frontmatter: dict[str, Any]
    body: str
    body_sha256: str
    diagnostics: list[ExtractionDiagnostic] = field(default_factory=list)


@dataclass(frozen=True)
class ParameterSpec:
    """A Skill input parameter."""

    name: str
    type: str
    required: bool = True
    description: str = ""
    default: Any = None
    format: str | None = None
    schema_ref: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    normalization: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
            "default": self.default,
            "format": self.format,
            "schema_ref": self.schema_ref,
            "raw": self.raw,
            "normalization": self.normalization,
        }


@dataclass(frozen=True)
class ArtifactSpec:
    """A Skill output artifact."""

    name: str
    type: str
    description: str = ""
    format: str | None = None
    schema_ref: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    normalization: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "format": self.format,
            "schema_ref": self.schema_ref,
            "raw": self.raw,
            "normalization": self.normalization,
        }


@dataclass(frozen=True)
class Condition:
    """A precondition or postcondition."""

    type: str
    expression: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "expression": self.expression,
            "description": self.description,
        }


@dataclass(frozen=True)
class ExtractedSkillSchema:
    """LLM-extracted candidate schema before deterministic normalization."""

    description: str = ""
    inputs: list[ParameterSpec | dict[str, Any]] = field(default_factory=list)
    outputs: list[ArtifactSpec | dict[str, Any]] = field(default_factory=list)
    skill_tags: list[str] = field(default_factory=list)
    data_tags: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    preconditions: list[Condition | dict[str, Any]] = field(default_factory=list)
    postconditions: list[Condition | dict[str, Any]] = field(default_factory=list)
    cost: dict[str, float] = field(default_factory=dict)
    quality: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillRepresentation:
    """Normalized Skill representation v1."""

    id: str
    name: str
    kind: str
    description: str
    version: str
    inputs: list[ParameterSpec]
    outputs: list[ArtifactSpec]
    preconditions: list[Condition]
    postconditions: list[Condition]
    skill_tags: list[str]
    data_tags: list[str]
    contains: list[str]
    composition: dict[str, Any] | None
    cost: dict[str, float]
    quality: dict[str, float]
    source: dict[str, Any]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "version": self.version,
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "preconditions": [item.to_dict() for item in self.preconditions],
            "postconditions": [item.to_dict() for item in self.postconditions],
            "skill_tags": self.skill_tags,
            "data_tags": self.data_tags,
            "contains": self.contains,
            "composition": self.composition,
            "cost": self.cost,
            "quality": self.quality,
            "source": self.source,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class NormalizationConfig:
    """Configuration and vocabularies used by the deterministic normalizer."""

    schema_version: str = "skill-representation-v1"
    normalizer_version: str = "representation-normalizer-v1"
    artifact_type_vocab_version: str = "artifact-type-v1"
    tag_vocab_version: str = "tag-v1-light"
    default_kind: str = "wrapped"
    default_version: str = "1.0.0"
    default_input_name: str = "input"
    default_input_type: str = "text"
    default_output_name: str = "result"
    unknown_type: str = "unknown"
    artifact_type_vocab: frozenset[str] = frozenset(
        {
            "text",
            "url",
            "file",
            "path",
            "paper",
            "dataset",
            "image",
            "audio",
            "video",
            "table",
            "code",
            "json",
            "report",
            "summary",
            "diagram",
            "pptx",
            "unknown",
        }
    )
    artifact_type_aliases: dict[str, str] = field(
        default_factory=lambda: {
            "natural_language": "text",
            "natural_language_query": "text",
            "plain_text": "text",
            "markdown": "text",
            "query": "text",
            "link": "url",
            "uri": "url",
            "webpage": "url",
            "pdf": "paper",
            "academic_paper": "paper",
            "publication": "paper",
            "spreadsheet": "table",
            "csv": "table",
            "dataframe": "table",
            "slides": "pptx",
            "presentation": "pptx",
            "powerpoint": "pptx",
            "source_code": "code",
            "script": "code",
            "program": "code",
            "chart": "diagram",
            "flowchart": "diagram",
            "mermaid": "diagram",
        }
    )
    artifact_format_aliases: dict[str, str] = field(
        default_factory=lambda: {
            "pdf": "pdf",
            "csv": "csv",
            "spreadsheet": "csv",
            "markdown": "markdown",
            "md": "markdown",
            "json_object": "json",
            "json": "json",
            "slides": "pptx",
            "presentation": "pptx",
            "powerpoint": "pptx",
            "ppt": "ppt",
            "pptx": "pptx",
            "png": "png",
            "jpg": "jpg",
            "jpeg": "jpg",
            "svg": "svg",
        }
    )


@dataclass(frozen=True)
class NormalizationResult:
    representation: SkillRepresentation
    diagnostics: list[ExtractionDiagnostic]


@dataclass(frozen=True)
class RepresentationExtractionResult:
    representations: list[SkillRepresentation]
    diagnostics: list[ExtractionDiagnostic]


class SkillSchemaExtractor(Protocol):
    """Protocol for LLM-backed schema extractors."""

    def extract(self, manifest: RawSkillManifest) -> ExtractedSkillSchema:
        ...
