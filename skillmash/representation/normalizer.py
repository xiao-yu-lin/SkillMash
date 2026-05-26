"""Deterministic normalization for LLM-extracted Skill schemas."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, List, Optional, Union

from skillmash.representation.base_vocab import term_similarity
from skillmash.representation.io_name_vocab import (
    HeuristicIONameResolver,
    IONameCandidate,
    IONameResolver,
    IONameResolution,
    IONameVocabulary,
)
from skillmash.representation.models import (
    ArtifactSpec,
    ExtractedSkillSchema,
    ExtractionDiagnostic,
    NormalizationConfig,
    NormalizationDecision,
    NormalizationResult,
    ParameterSpec,
    RawSkillManifest,
    SkillRepresentation,
)
from skillmash.representation.utils import (
    normalize_human_name,
    normalize_parameter_name,
    normalize_slug,
    normalize_token,
    to_dict,
)


@dataclass(frozen=True)
class _Identity:
    id: str
    name: str
    description: str
    version: str


class SkillRepresentationNormalizer:
    """Convert an ExtractedSkillSchema into SkillRepresentation v1."""

    def __init__(
        self,
        config: Optional[NormalizationConfig] = None,
        io_name_vocabulary: Optional[IONameVocabulary] = None,
        io_name_resolver: Optional[IONameResolver] = None,
    ) -> None:
        self.config = config or NormalizationConfig()
        self.io_name_vocabulary = (
            io_name_vocabulary
            or IONameVocabulary.from_config(self.config)
        )
        self.io_name_resolver = io_name_resolver or HeuristicIONameResolver()
        self._io_name_resolution_cache: Dict[str, IONameResolution] = {}
        self._io_name_resolution_cache_lock = RLock()

    def normalize(
        self,
        manifest: RawSkillManifest,
        extracted: ExtractedSkillSchema,
    ) -> NormalizationResult:
        diagnostics = list(manifest.diagnostics)
        decisions: List[NormalizationDecision] = []

        identity = self._normalize_identity(manifest, extracted)
        self._prime_io_name_resolutions(manifest, extracted, identity.id)
        inputs = self._normalize_inputs(
            extracted.inputs,
            manifest,
            identity.id,
            diagnostics,
            decisions,
        )
        outputs = self._normalize_outputs(
            extracted.outputs,
            manifest,
            identity.id,
            diagnostics,
            decisions,
        )

        representation = SkillRepresentation(
            id=identity.id,
            name=identity.name,
            description=identity.description,
            version=identity.version,
            inputs=inputs,
            outputs=outputs,
        )
        self._validate(representation, manifest, diagnostics)
        return NormalizationResult(
            representation=representation,
            diagnostics=diagnostics,
            decisions=decisions,
        )

    def _normalize_identity(
        self,
        manifest: RawSkillManifest,
        extracted: ExtractedSkillSchema,
    ) -> _Identity:
        frontmatter = manifest.frontmatter
        raw_name = str(frontmatter.get("name") or manifest.folder.id_hint)
        skill_id = normalize_slug(raw_name) or normalize_slug(manifest.folder.relative_path)
        name = normalize_human_name(raw_name) or skill_id
        version = str(
            frontmatter.get("version")
            or self.config.default_version
        )
        description = str(extracted.description or frontmatter.get("description") or "").strip()
        return _Identity(
            id=skill_id,
            name=name,
            description=description,
            version=version,
        )

    def _prime_io_name_resolutions(
        self,
        manifest: RawSkillManifest,
        extracted: ExtractedSkillSchema,
        skill_id: str,
    ) -> None:
        candidates: List[IONameCandidate] = []
        seen: set = set()
        fields = [
            ("input", extracted.inputs, self.config.default_input_name, self.config.default_input_type),
            ("output", extracted.outputs, self.config.default_output_name, self.config.unknown_type),
        ]
        for direction, items, default_name, default_type in fields:
            for raw in items:
                data = to_dict(raw)
                raw_name = str(data.get("name") or default_name)
                raw_type = str(data.get("type") or default_type)
                token = normalize_parameter_name(raw_name)
                if (
                    not token
                    or token in seen
                    or self.io_name_vocabulary.lookup(token) is not None
                ):
                    continue
                with self._io_name_resolution_cache_lock:
                    cached = token in self._io_name_resolution_cache
                if cached:
                    continue
                seen.add(token)
                candidates.append(
                    IONameCandidate(
                        raw_value=raw_name,
                        token=token,
                        direction=direction,
                        data_type=raw_type,
                        description=str(data.get("description") or ""),
                        skill_id=skill_id,
                        path=str(manifest.folder.path),
                    )
                )
        if not candidates:
            return

        resolve_many = getattr(self.io_name_resolver, "resolve_many", None)
        if callable(resolve_many):
            resolutions = resolve_many(candidates, self.io_name_vocabulary)
        else:
            resolutions = {
                candidate.token: self.io_name_resolver.resolve(
                    candidate,
                    self.io_name_vocabulary,
                )
                for candidate in candidates
            }
        with self._io_name_resolution_cache_lock:
            self._io_name_resolution_cache.update(resolutions)

    def _normalize_inputs(
        self,
        raw_inputs: List[Union[ParameterSpec, Dict[str, Any]]],
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: List[ExtractionDiagnostic],
        decisions: List[NormalizationDecision],
    ) -> List[ParameterSpec]:
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
            self._record_decision(
                decisions,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                direction="input",
                field="name",
                raw_value=self.config.default_input_name,
                token=self.config.default_input_name,
                normalized_value=self.config.default_input_name,
                method="default",
                vocab="io_name_vocab",
                vocab_version=self.config.io_name_vocab_version,
                confidence=1.0,
            )
            self._record_decision(
                decisions,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                direction="input",
                field="type",
                raw_value=self.config.default_input_type,
                token=self.config.default_input_type,
                normalized_value=self.config.default_input_type,
                method="default",
                vocab="data_type_vocab",
                vocab_version=self.config.data_type_vocab_version,
                confidence=1.0,
            )
            return [
                ParameterSpec(
                    name=self.config.default_input_name,
                    type=self.config.default_input_type,
                    required=True,
                    description="Default text input",
                    default=None,
                    schema_ref=None,
                )
            ]

        inputs: List[ParameterSpec] = []
        for raw in raw_inputs:
            data = to_dict(raw)
            raw_type = str(data.get("type") or self.config.default_input_type)
            raw_name = str(data.get("name") or self.config.default_input_name)
            name = self._normalize_io_name(
                raw_name,
                raw_type,
                str(data.get("description") or ""),
                manifest,
                skill_id,
                "input",
                diagnostics,
                decisions,
            )
            if name is None:
                continue
            data_type = self._normalize_data_type(
                raw_type,
                manifest,
                skill_id,
                "input",
                diagnostics,
                decisions,
            )
            inputs.append(
                ParameterSpec(
                    name=name,
                    type=data_type,
                    required=bool(data.get("required", True)),
                    description=str(data.get("description") or ""),
                    default=data.get("default"),
                    schema_ref=data.get("schema_ref"),
                )
            )
        return self._deduplicate_inputs(inputs, manifest, skill_id, diagnostics)

    def _normalize_outputs(
        self,
        raw_outputs: List[Union[ArtifactSpec, Dict[str, Any]]],
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: List[ExtractionDiagnostic],
        decisions: List[NormalizationDecision],
    ) -> List[ArtifactSpec]:
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
            self._record_decision(
                decisions,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                direction="output",
                field="name",
                raw_value=self.config.default_output_name,
                token=self.config.default_output_name,
                normalized_value=self.config.default_output_name,
                method="default",
                vocab="io_name_vocab",
                vocab_version=self.config.io_name_vocab_version,
                confidence=1.0,
            )
            self._record_decision(
                decisions,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                direction="output",
                field="type",
                raw_value=self.config.unknown_type,
                token=self.config.unknown_type,
                normalized_value=self.config.unknown_type,
                method="default_unknown",
                vocab="data_type_vocab",
                vocab_version=self.config.data_type_vocab_version,
                confidence=0.0,
            )
            return [
                ArtifactSpec(
                    name=self.config.default_output_name,
                    type=self.config.unknown_type,
                    description="Unknown output",
                    schema_ref=None,
                )
            ]

        outputs: List[ArtifactSpec] = []
        for raw in raw_outputs:
            data = to_dict(raw)
            raw_type = str(data.get("type") or self.config.unknown_type)
            raw_name = str(data.get("name") or self.config.default_output_name)
            name = self._normalize_io_name(
                raw_name,
                raw_type,
                str(data.get("description") or ""),
                manifest,
                skill_id,
                "output",
                diagnostics,
                decisions,
            )
            if name is None:
                continue
            data_type = self._normalize_data_type(
                raw_type,
                manifest,
                skill_id,
                "output",
                diagnostics,
                decisions,
            )
            outputs.append(
                ArtifactSpec(
                    name=name,
                    type=data_type,
                    description=str(data.get("description") or ""),
                    schema_ref=data.get("schema_ref"),
                )
            )
        return self._deduplicate_outputs(outputs, manifest, skill_id, diagnostics)

    def _deduplicate_inputs(
        self,
        inputs: List[ParameterSpec],
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: List[ExtractionDiagnostic],
    ) -> List[ParameterSpec]:
        merged: List[ParameterSpec] = []
        by_name: Dict[str, int] = {}
        for item in inputs:
            existing_index = by_name.get(item.name)
            if existing_index is None:
                by_name[item.name] = len(merged)
                merged.append(item)
                continue

            existing = merged[existing_index]
            merged_item, details = self._merge_input(existing, item)
            merged[existing_index] = merged_item
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="normalization",
                    severity="warning",
                    code="duplicate_input_merged",
                    message="duplicate normalized input name was merged",
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    details={
                        "name": item.name,
                        "merged_type": merged_item.type,
                        **details,
                    },
                )
            )
        return merged

    def _deduplicate_outputs(
        self,
        outputs: List[ArtifactSpec],
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: List[ExtractionDiagnostic],
    ) -> List[ArtifactSpec]:
        merged: List[ArtifactSpec] = []
        by_name: Dict[str, int] = {}
        for item in outputs:
            existing_index = by_name.get(item.name)
            if existing_index is None:
                by_name[item.name] = len(merged)
                merged.append(item)
                continue

            existing = merged[existing_index]
            merged_item, details = self._merge_output(existing, item)
            merged[existing_index] = merged_item
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="normalization",
                    severity="warning",
                    code="duplicate_output_merged",
                    message="duplicate normalized output name was merged",
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    details={
                        "name": item.name,
                        "merged_type": merged_item.type,
                        **details,
                    },
                )
            )
        return merged

    def _merge_input(
        self,
        existing: ParameterSpec,
        incoming: ParameterSpec,
    ) -> Tuple[ParameterSpec, Dict[str, Any]]:
        merged_type, details = self._merge_type(existing.type, incoming.type)
        default, default_conflict = self._merge_optional_value(
            existing.default,
            incoming.default,
        )
        schema_ref, schema_ref_conflict = self._merge_optional_value(
            existing.schema_ref,
            incoming.schema_ref,
        )
        details.update(
            {
                "required_values": [existing.required, incoming.required],
                "default_conflict": default_conflict,
                "schema_ref_conflict": schema_ref_conflict,
            }
        )
        return (
            ParameterSpec(
                name=existing.name,
                type=merged_type,
                required=existing.required or incoming.required,
                description=self._merge_description(
                    existing.description,
                    incoming.description,
                ),
                default=default,
                schema_ref=schema_ref,
            ),
            details,
        )

    def _merge_output(
        self,
        existing: ArtifactSpec,
        incoming: ArtifactSpec,
    ) -> Tuple[ArtifactSpec, Dict[str, Any]]:
        merged_type, details = self._merge_type(existing.type, incoming.type)
        schema_ref, schema_ref_conflict = self._merge_optional_value(
            existing.schema_ref,
            incoming.schema_ref,
        )
        details["schema_ref_conflict"] = schema_ref_conflict
        return (
            ArtifactSpec(
                name=existing.name,
                type=merged_type,
                description=self._merge_description(
                    existing.description,
                    incoming.description,
                ),
                schema_ref=schema_ref,
            ),
            details,
        )

    def _merge_type(self, existing: str, incoming: str) -> Tuple[str, Dict[str, Any]]:
        if existing == incoming:
            return existing, {"type_conflict": False, "type_values": [existing]}
        if existing == self.config.unknown_type:
            return incoming, {
                "type_conflict": True,
                "type_values": [existing, incoming],
            }
        return existing, {
            "type_conflict": True,
            "type_values": [existing, incoming],
        }

    def _merge_optional_value(
        self,
        existing: Any,
        incoming: Any,
    ) -> Tuple[Any, bool]:
        if existing in (None, ""):
            return incoming, False
        if incoming in (None, "") or incoming == existing:
            return existing, False
        return existing, True

    def _merge_description(self, existing: str, incoming: str) -> str:
        parts: List[str] = []
        seen: set = set()
        for value in [existing, incoming]:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            parts.append(text)
            seen.add(text)
        return " ".join(parts)

    def _normalize_io_name(
        self,
        raw_name: str,
        raw_type: str,
        description: str,
        manifest: RawSkillManifest,
        skill_id: str,
        direction: str,
        diagnostics: List[ExtractionDiagnostic],
        decisions: List[NormalizationDecision],
    ) -> Optional[str]:
        token = normalize_parameter_name(raw_name)
        existing = self.io_name_vocabulary.lookup(token)
        if existing is not None:
            method = "vocab_alias" if existing != token else "vocab_exact"
            confidence = 0.95 if existing != token else 1.0
            self._record_decision(
                decisions,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                direction=direction,
                field="name",
                raw_value=raw_name,
                token=token,
                normalized_value=existing,
                method=method,
                vocab="io_name_vocab",
                vocab_version=self.io_name_vocabulary.version,
                confidence=confidence,
            )
            return existing

        with self._io_name_resolution_cache_lock:
            resolution = self._io_name_resolution_cache.get(token)
        if resolution is None:
            resolution = self.io_name_resolver.resolve(
                IONameCandidate(
                    raw_value=raw_name,
                    token=token,
                    direction=direction,
                    data_type=raw_type,
                    description=description,
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                ),
                self.io_name_vocabulary,
            )
            with self._io_name_resolution_cache_lock:
                self._io_name_resolution_cache[token] = resolution
        normalized = self._apply_io_name_resolution(
            token,
            raw_type,
            description,
            resolution,
            manifest,
            skill_id,
            direction,
            diagnostics,
        )
        self._record_decision(
            decisions,
            skill_id=skill_id,
            path=str(manifest.folder.path),
            direction=direction,
            field="name",
            raw_value=raw_name,
            token=token,
            normalized_value=normalized or "",
            method=resolution.action,
            vocab="io_name_vocab",
            vocab_version=self.io_name_vocabulary.version,
            confidence=resolution.confidence,
            details={
                "reason": resolution.reason,
                "forced_merge": resolution.forced_merge,
                "vocab_size": self.io_name_vocabulary.size(),
                "max_vocab_size": self.io_name_vocabulary.max_vocab_size,
            },
        )
        return normalized

    def _apply_io_name_resolution(
        self,
        token: str,
        raw_type: str,
        description: str,
        resolution: IONameResolution,
        manifest: RawSkillManifest,
        skill_id: str,
        direction: str,
        diagnostics: List[ExtractionDiagnostic],
    ) -> Optional[str]:
        if resolution.action == "exclude_non_runtime":
            return None

        if resolution.action == "create_new" and not self.io_name_vocabulary.is_full():
            self._warn_possible_duplicate_io_name(
                token,
                manifest,
                skill_id,
                direction,
                diagnostics,
            )
            return self.io_name_vocabulary.create_term(
                resolution.normalized_value or token,
                alias=token,
                data_type=raw_type,
                example=description,
            )

        target = resolution.normalized_value or self.io_name_vocabulary.closest_term(token)
        if target is None:
            return self.io_name_vocabulary.create_term(
                token,
                alias=token,
                data_type=raw_type,
                example=description,
            )
        if target not in self.io_name_vocabulary.term_names():
            target = self.io_name_vocabulary.closest_term(target) or target
        self.io_name_vocabulary.add_alias(
            token,
            target,
            data_type=raw_type,
            example=description,
        )
        return target

    def _warn_possible_duplicate_io_name(
        self,
        token: str,
        manifest: RawSkillManifest,
        skill_id: str,
        direction: str,
        diagnostics: List[ExtractionDiagnostic],
    ) -> None:
        threshold = self.config.possible_duplicate_name_similarity_threshold
        if threshold <= 0:
            return
        term_names = self.io_name_vocabulary.term_names()
        if not term_names:
            return
        closest = max(term_names, key=lambda name: term_similarity(token, name))
        score = term_similarity(token, closest)
        if score < threshold:
            return
        diagnostics.append(
            ExtractionDiagnostic(
                stage="normalization",
                severity="warning",
                code="possible_duplicate_io_name",
                message="new I/O name is similar to an existing vocabulary term; review aliasing",
                skill_id=skill_id,
                path=str(manifest.folder.path),
                details={
                    "direction": direction,
                    "token": token,
                    "closest_term": closest,
                    "similarity": round(score, 4),
                    "threshold": threshold,
                },
            )
        )

    def _normalize_data_type(
        self,
        raw_type: str,
        manifest: RawSkillManifest,
        skill_id: str,
        direction: str,
        diagnostics: List[ExtractionDiagnostic],
        decisions: List[NormalizationDecision],
    ) -> str:
        token = normalize_token(raw_type)
        normalized = self.config.data_type_aliases.get(token, token)
        if normalized in self.config.data_type_vocab:
            self._record_decision(
                decisions,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                direction=direction,
                field="type",
                raw_value=raw_type,
                token=token,
                normalized_value=normalized,
                method="alias_map" if normalized != token else "exact",
                vocab="data_type_vocab",
                vocab_version=self.config.data_type_vocab_version,
                confidence=0.95 if normalized != token else 1.0,
            )
            return normalized

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
        self._record_decision(
            decisions,
            skill_id=skill_id,
            path=str(manifest.folder.path),
            direction=direction,
            field="type",
            raw_value=raw_type,
            token=token,
            normalized_value=self.config.unknown_type,
            method="unknown",
            vocab="data_type_vocab",
            vocab_version=self.config.data_type_vocab_version,
            confidence=0.0,
        )
        return self.config.unknown_type

    def _record_decision(
        self,
        decisions: List[NormalizationDecision],
        *,
        skill_id: str,
        path: Optional[str],
        direction: str,
        field: str,
        raw_value: str,
        token: str,
        normalized_value: str,
        method: str,
        vocab: str,
        vocab_version: str,
        confidence: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        decisions.append(
            NormalizationDecision(
                skill_id=skill_id,
                path=path,
                direction=direction,
                field=field,
                raw_value=raw_value,
                token=token,
                normalized_value=normalized_value,
                method=method,
                vocab=vocab,
                vocab_version=vocab_version,
                confidence=confidence,
                details=details or {},
            )
        )

    def _validate(
        self,
        representation: SkillRepresentation,
        manifest: RawSkillManifest,
        diagnostics: List[ExtractionDiagnostic],
    ) -> None:
        if not representation.outputs:
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="normalization",
                    severity="error",
                    code="schema_validation_failed",
                    message="outputs must not be empty",
                    skill_id=representation.id,
                    path=str(manifest.folder.path),
                )
            )
        for item in [*representation.inputs, *representation.outputs]:
            if item.type not in self.config.data_type_vocab:
                diagnostics.append(
                    ExtractionDiagnostic(
                        stage="normalization",
                        severity="error",
                        code="schema_validation_failed",
                        message="type is outside DataType vocabulary",
                        skill_id=representation.id,
                        path=str(manifest.folder.path),
                        details={"type": item.type},
                    )
                )
