"""Deterministic normalization for LLM-extracted Skill schemas."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from skillmash.representation.models import (
    ArtifactSpec,
    Condition,
    ExtractedSkillSchema,
    ExtractionDiagnostic,
    NormalizationConfig,
    NormalizationResult,
    ParameterSpec,
    RawSkillManifest,
    SkillRepresentation,
)


@dataclass(frozen=True)
class _Identity:
    id: str
    name: str
    kind: str
    description: str
    version: str
    contains: list[str]
    composition: dict[str, Any] | None


class SkillRepresentationNormalizer:
    """Convert an ExtractedSkillSchema into SkillRepresentation v1."""

    def __init__(self, config: NormalizationConfig | None = None) -> None:
        self.config = config or NormalizationConfig()

    def normalize(
        self,
        manifest: RawSkillManifest,
        extracted: ExtractedSkillSchema,
    ) -> NormalizationResult:
        diagnostics = list(manifest.diagnostics)

        identity = self._normalize_identity(manifest, extracted)
        inputs = self._normalize_inputs(extracted.inputs, manifest, identity.id, diagnostics)
        outputs = self._normalize_outputs(extracted.outputs, manifest, identity.id, diagnostics)
        skill_tags = self._normalize_tags(extracted.skill_tags)
        data_tags = self._normalize_tags(extracted.data_tags)
        preconditions = self._normalize_conditions(extracted.preconditions)
        postconditions = self._normalize_conditions(extracted.postconditions)
        cost = self._normalize_cost(extracted.cost)
        quality = self._normalize_quality(extracted.quality, extracted.confidence)
        source = self._build_source(manifest)
        metadata = self._build_metadata(manifest, extracted)

        representation = SkillRepresentation(
            id=identity.id,
            name=identity.name,
            kind=identity.kind,
            description=identity.description,
            version=identity.version,
            inputs=inputs,
            outputs=outputs,
            preconditions=preconditions,
            postconditions=postconditions,
            skill_tags=skill_tags,
            data_tags=data_tags,
            contains=identity.contains,
            composition=identity.composition,
            cost=cost,
            quality=quality,
            source=source,
            metadata=metadata,
        )
        self._validate(representation, diagnostics)
        return NormalizationResult(representation=representation, diagnostics=diagnostics)

    def _normalize_identity(
        self,
        manifest: RawSkillManifest,
        extracted: ExtractedSkillSchema,
    ) -> _Identity:
        frontmatter = manifest.frontmatter
        raw_name = str(frontmatter.get("name") or manifest.folder.id_hint)
        skill_id = _normalize_slug(raw_name) or _normalize_slug(manifest.folder.relative_path)
        name = _normalize_human_name(raw_name) or skill_id
        kind = str(frontmatter.get("kind") or self.config.default_kind).strip().lower()
        version = str(
            frontmatter.get("version")
            or _nested_get(frontmatter, ("metadata", "version"))
            or self.config.default_version
        )
        description = str(extracted.description or frontmatter.get("description") or "").strip()
        contains = _as_string_list(frontmatter.get("contains"))
        composition = frontmatter.get("composition")
        if not isinstance(composition, dict):
            composition = None
        return _Identity(
            id=skill_id,
            name=name,
            kind=kind,
            description=description,
            version=version,
            contains=contains,
            composition=composition,
        )

    def _normalize_inputs(
        self,
        raw_inputs: list[ParameterSpec | dict[str, Any]],
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: list[ExtractionDiagnostic],
    ) -> list[ParameterSpec]:
        if not raw_inputs:
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="normalization",
                    severity="warning",
                    code="default_input_created",
                    message="inputs missing; created default text input",
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                )
            )
            return [
                ParameterSpec(
                    name=self.config.default_input_name,
                    type=self.config.default_input_type,
                    required=True,
                    description="Default text input",
                    default=None,
                    format=None,
                    schema_ref=None,
                    raw={
                        "name": self.config.default_input_name,
                        "type": self.config.default_input_type,
                    },
                    normalization={
                        "name_method": "default",
                        "type_method": "default",
                        "raw_type": self.config.default_input_type,
                        "normalized_token": self.config.default_input_type,
                        "confidence": 1.0,
                    },
                )
            ]

        inputs: list[ParameterSpec] = []
        for raw in raw_inputs:
            data = _to_dict(raw)
            raw_type = str(data.get("type") or self.config.default_input_type)
            type_result = self._normalize_artifact_type(raw_type, manifest, skill_id, diagnostics)
            raw_name = str(data.get("name") or self.config.default_input_name)
            inputs.append(
                ParameterSpec(
                    name=_normalize_parameter_name(raw_name),
                    type=type_result["type"],
                    required=bool(data.get("required", True)),
                    description=str(data.get("description") or ""),
                    default=data.get("default"),
                    format=type_result["format"],
                    schema_ref=data.get("schema_ref"),
                    raw={
                        "name": raw_name,
                        "type": raw_type,
                    },
                    normalization={
                        "name_method": "snake_case",
                        **type_result["normalization"],
                    },
                )
            )
        return inputs

    def _normalize_outputs(
        self,
        raw_outputs: list[ArtifactSpec | dict[str, Any]],
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: list[ExtractionDiagnostic],
    ) -> list[ArtifactSpec]:
        if not raw_outputs:
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="normalization",
                    severity="warning",
                    code="unknown_output_created",
                    message="outputs missing; created unknown result output",
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                )
            )
            return [
                ArtifactSpec(
                    name=self.config.default_output_name,
                    type=self.config.unknown_type,
                    description="Unknown output",
                    format=None,
                    schema_ref=None,
                    raw={
                        "name": self.config.default_output_name,
                        "type": self.config.unknown_type,
                    },
                    normalization={
                        "name_method": "default",
                        "type_method": "default_unknown",
                        "raw_type": self.config.unknown_type,
                        "normalized_token": self.config.unknown_type,
                        "confidence": 0.0,
                    },
                )
            ]

        outputs: list[ArtifactSpec] = []
        for raw in raw_outputs:
            data = _to_dict(raw)
            raw_type = str(data.get("type") or self.config.unknown_type)
            type_result = self._normalize_artifact_type(raw_type, manifest, skill_id, diagnostics)
            raw_name = str(data.get("name") or self.config.default_output_name)
            outputs.append(
                ArtifactSpec(
                    name=_normalize_parameter_name(raw_name),
                    type=type_result["type"],
                    description=str(data.get("description") or ""),
                    format=type_result["format"],
                    schema_ref=data.get("schema_ref"),
                    raw={
                        "name": raw_name,
                        "type": raw_type,
                    },
                    normalization={
                        "name_method": "snake_case",
                        **type_result["normalization"],
                    },
                )
            )
        return outputs

    def _normalize_artifact_type(
        self,
        raw_type: str,
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: list[ExtractionDiagnostic],
    ) -> dict[str, Any]:
        token = _normalize_token(raw_type)
        normalized = self.config.artifact_type_aliases.get(token, token)
        artifact_format = self.config.artifact_format_aliases.get(token)
        if normalized in self.config.artifact_type_vocab:
            return {
                "type": normalized,
                "format": artifact_format,
                "normalization": {
                    "type_method": "alias_map" if normalized != token else "exact",
                    "raw_type": raw_type,
                    "normalized_token": token,
                    "confidence": 0.95 if normalized != token else 1.0,
                },
            }

        diagnostics.append(
            ExtractionDiagnostic(
                stage="normalization",
                severity="warning",
                code="unsupported_type_normalized",
                message="artifact type is not supported; normalized to unknown",
                skill_id=skill_id,
                path=str(manifest.folder.path),
                details={"original_type": raw_type, "normalized_token": token},
            )
        )
        return {
            "type": self.config.unknown_type,
            "format": artifact_format,
            "normalization": {
                "type_method": "unknown",
                "raw_type": raw_type,
                "normalized_token": token,
                "confidence": 0.0,
            },
        }

    def _normalize_tags(self, tags: list[str]) -> list[str]:
        return sorted({token for tag in tags if (token := _normalize_token(str(tag)))})

    def _normalize_conditions(self, conditions: list[Condition | dict[str, Any]]) -> list[Condition]:
        normalized: list[Condition] = []
        for raw in conditions:
            data = _to_dict(raw)
            condition_type = _normalize_token(str(data.get("type") or "constraint"))
            expression = str(data.get("expression") or "").strip()
            if not expression:
                continue
            normalized.append(
                Condition(
                    type=condition_type,
                    expression=expression,
                    description=str(data.get("description") or ""),
                )
            )
        return normalized

    def _normalize_cost(self, raw_cost: dict[str, float]) -> dict[str, float]:
        return {
            "latency": _clamp_number(raw_cost.get("latency", 3), 1, 5),
            "money": _clamp_number(raw_cost.get("money", 1), 1, 5),
            "complexity": _clamp_number(raw_cost.get("complexity", 2), 1, 5),
        }

    def _normalize_quality(
        self,
        raw_quality: dict[str, float],
        confidence: float | None,
    ) -> dict[str, float]:
        quality = {
            "reliability": _clamp_number(raw_quality.get("reliability", 0.7), 0, 1),
        }
        if confidence is not None:
            quality["extraction_confidence"] = _clamp_number(confidence, 0, 1)
        return quality

    def _build_source(self, manifest: RawSkillManifest) -> dict[str, Any]:
        return {
            "type": "folder",
            "path": str(manifest.folder.path),
            "entry": str(manifest.folder.entry),
            "relative_path": manifest.folder.relative_path,
        }

    def _build_metadata(
        self,
        manifest: RawSkillManifest,
        extracted: ExtractedSkillSchema,
    ) -> dict[str, Any]:
        return {
            "frontmatter": manifest.frontmatter,
            "allowed_tools": _parse_allowed_tools(manifest.frontmatter.get("allowed-tools")),
            "body_sha256": manifest.body_sha256,
            "schema_version": self.config.schema_version,
            "extraction_warnings": list(extracted.warnings),
            "extraction_constraints": list(extracted.constraints),
            "normalizer": {
                "version": self.config.normalizer_version,
                "artifact_type_vocab": self.config.artifact_type_vocab_version,
                "tag_vocab": self.config.tag_vocab_version,
            },
        }

    def _validate(
        self,
        representation: SkillRepresentation,
        diagnostics: list[ExtractionDiagnostic],
    ) -> None:
        if not representation.outputs:
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="normalization",
                    severity="error",
                    code="schema_validation_failed",
                    message="outputs must not be empty",
                    skill_id=representation.id,
                    path=representation.source.get("path"),
                )
            )
        for item in [*representation.inputs, *representation.outputs]:
            if item.type not in self.config.artifact_type_vocab:
                diagnostics.append(
                    ExtractionDiagnostic(
                        stage="normalization",
                        severity="error",
                        code="schema_validation_failed",
                        message="artifact type is outside ArtifactType vocabulary",
                        skill_id=representation.id,
                        path=representation.source.get("path"),
                        details={"type": item.type},
                    )
                )
        if not representation.metadata.get("body_sha256"):
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="normalization",
                    severity="error",
                    code="schema_validation_failed",
                    message="metadata.body_sha256 is required",
                    skill_id=representation.id,
                    path=representation.source.get("path"),
                )
            )


def _normalize_token(raw: str) -> str:
    value = unicodedata.normalize("NFKC", raw)
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def _normalize_parameter_name(raw: str) -> str:
    return _normalize_token(raw) or "input"


def _normalize_slug(raw: str) -> str:
    value = unicodedata.normalize("NFKC", raw)
    value = value.strip().lower().replace("_", "-")
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def _normalize_human_name(raw: str) -> str:
    return str(raw).strip()


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _nested_get(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _clamp_number(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def _parse_allowed_tools(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip() for item in text.split(",") if item.strip()]
