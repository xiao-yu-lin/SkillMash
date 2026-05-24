"""Deterministic normalization for LLM-extracted Skill schemas."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple, Union

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
    Condition,
    ExtractedSkillSchema,
    ExtractionDiagnostic,
    NormalizationConfig,
    NormalizationDecision,
    NormalizationResult,
    ParameterSpec,
    RawSkillManifest,
    SkillRepresentation,
    SlotCandidate,
    SlotRef,
)
from skillmash.representation.semantic_vocab import (
    HeuristicSemanticResolver,
    SemanticCandidate,
    SemanticResolver,
    SemanticResolution,
    SemanticVocabulary,
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
        task_vocabulary: Optional[SemanticVocabulary] = None,
        task_resolver: Optional[SemanticResolver] = None,
    ) -> None:
        self.config = config or NormalizationConfig()
        self.io_name_vocabulary = (
            io_name_vocabulary
            or IONameVocabulary.from_config(self.config)
        )
        self.io_name_resolver = io_name_resolver or HeuristicIONameResolver()
        self.task_vocabulary = (
            task_vocabulary
            or SemanticVocabulary.from_aliases(
                version=self.config.task_vocab_version,
                max_vocab_size=self.config.max_task_vocab_size,
                aliases=self.config.task_aliases,
            )
        )
        self.task_resolver = task_resolver or HeuristicSemanticResolver()
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
        tasks = self._normalize_tasks(
            extracted.tasks,
            manifest,
            identity.id,
            identity.description,
            decisions,
        )
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
        preconditions = self._normalize_conditions(extracted.preconditions)
        postconditions = self._normalize_conditions(extracted.postconditions)
        emits_slots, consumes_slots = self._normalize_slots(
            extracted=extracted,
            manifest=manifest,
            skill_id=identity.id,
            diagnostics=diagnostics,
        )

        representation = SkillRepresentation(
            id=identity.id,
            name=identity.name,
            description=identity.description,
            version=identity.version,
            tasks=tasks,
            inputs=inputs,
            outputs=outputs,
            preconditions=preconditions,
            postconditions=postconditions,
            emits_slots=emits_slots,
            consumes_slots=consumes_slots,
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

    def _normalize_tasks(
        self,
        raw_tasks: List[str],
        manifest: RawSkillManifest,
        skill_id: str,
        description: str,
        decisions: List[NormalizationDecision],
    ) -> List[str]:
        tasks: List[str] = []
        seen: set = set()
        for raw_task in raw_tasks:
            raw_value = str(raw_task or "").strip()
            if not raw_value:
                continue
            task = self._normalize_semantic_vocab_value(
                raw_value,
                description,
                manifest,
                skill_id,
                field="tasks",
                vocab_name="task_vocab",
                vocabulary=self.task_vocabulary,
                resolver=self.task_resolver,
                decisions=decisions,
            )
            if task is None or task in seen:
                continue
            tasks.append(task)
            seen.add(task)
        return tasks

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

    def _normalize_semantic_vocab_value(
        self,
        raw_value: str,
        description: str,
        manifest: RawSkillManifest,
        skill_id: str,
        *,
        field: str,
        vocab_name: str,
        vocabulary: SemanticVocabulary,
        resolver: SemanticResolver,
        decisions: List[NormalizationDecision],
    ) -> Optional[str]:
        token = normalize_parameter_name(raw_value)
        existing = vocabulary.lookup(token)
        if existing is not None:
            method = "vocab_alias" if existing != token else "vocab_exact"
            confidence = 0.95 if existing != token else 1.0
            self._record_decision(
                decisions,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                direction="skill",
                field=field,
                raw_value=raw_value,
                token=token,
                normalized_value=existing,
                method=method,
                vocab=vocab_name,
                vocab_version=vocabulary.version,
                confidence=confidence,
            )
            return existing

        resolution = resolver.resolve(
            SemanticCandidate(
                raw_value=raw_value,
                token=token,
                field=field,
                description=description,
                skill_id=skill_id,
                path=str(manifest.folder.path),
            ),
            vocabulary,
        )
        normalized = self._apply_semantic_resolution(token, description, resolution, vocabulary)
        self._record_decision(
            decisions,
            skill_id=skill_id,
            path=str(manifest.folder.path),
            direction="skill",
            field=field,
            raw_value=raw_value,
            token=token,
            normalized_value=normalized or "",
            method=resolution.action,
            vocab=vocab_name,
            vocab_version=vocabulary.version,
            confidence=resolution.confidence,
            details={
                "reason": resolution.reason,
                "forced_merge": resolution.forced_merge,
                "vocab_size": vocabulary.size(),
                "max_vocab_size": vocabulary.max_vocab_size,
            },
        )
        return normalized

    def _apply_semantic_resolution(
        self,
        token: str,
        description: str,
        resolution: SemanticResolution,
        vocabulary: SemanticVocabulary,
    ) -> Optional[str]:
        if resolution.action == "exclude_non_runtime":
            return None

        if resolution.action == "create_new" and not vocabulary.is_full():
            return vocabulary.create_term(
                resolution.normalized_value or token,
                alias=token,
                example=description,
            )

        target = resolution.normalized_value or vocabulary.closest_term(token)
        if target is None:
            return vocabulary.create_term(token, alias=token, example=description)
        if target not in vocabulary.term_names():
            target = vocabulary.closest_term(target) or target
        vocabulary.add_alias(token, target, example=description)
        return target

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

    def _normalize_conditions(
        self, conditions: List[Union[Condition, Dict[str, Any]]]
    ) -> List[Condition]:
        normalized: List[Condition] = []
        for raw in conditions:
            data = to_dict(raw)
            condition_type = normalize_token(str(data.get("type") or "constraint"))
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

    def _normalize_slots(
        self,
        *,
        extracted: ExtractedSkillSchema,
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: List[ExtractionDiagnostic],
    ) -> Tuple[List[SlotRef], List[SlotRef]]:
        emits = self._run_slot_gate_pipeline(
            direction="emits",
            raw_candidates=list(extracted.emits_slots),
            manifest=manifest,
            skill_id=skill_id,
            diagnostics=diagnostics,
        )
        consumes = self._run_slot_gate_pipeline(
            direction="consumes",
            raw_candidates=list(extracted.consumes_slots),
            manifest=manifest,
            skill_id=skill_id,
            diagnostics=diagnostics,
        )
        self._role_shape_gate(
            skill_id=skill_id,
            manifest=manifest,
            emits=emits,
            consumes=consumes,
            diagnostics=diagnostics,
        )
        return emits, consumes

    def _run_slot_gate_pipeline(
        self,
        *,
        direction: str,
        raw_candidates: List[SlotCandidate],
        manifest: RawSkillManifest,
        skill_id: str,
        diagnostics: List[ExtractionDiagnostic],
    ) -> List[SlotRef]:
        # 1) schema gate
        schema_passed: List[Dict[str, Any]] = []
        for raw_candidate in raw_candidates:
            candidate = self._slot_schema_gate(raw_candidate)
            if candidate is None:
                self._record_slot_status(
                    diagnostics=diagnostics,
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    kind="",
                    direction=direction,
                    candidate_snapshot=self._slot_candidate_snapshot(raw_candidate),
                    status="dropped_invalid_schema",
                    reason="candidate must include snake_case kind/parent and confidence in [0,1]",
                )
                continue
            schema_passed.append(candidate)

        # 2) parent gate
        parent_passed: List[Dict[str, Any]] = []
        whitelist = set(self.config.slot_parent_whitelist)
        for candidate in schema_passed:
            if candidate["parent"] not in whitelist:
                self._record_slot_status(
                    diagnostics=diagnostics,
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    kind=candidate["name"],
                    direction=direction,
                    candidate_snapshot=candidate,
                    status="dropped_invalid_parent",
                    reason="parent is outside slot_parent_whitelist",
                )
                continue
            parent_passed.append(candidate)

        # 3) confidence gate
        confidence_passed: List[Dict[str, Any]] = []
        for candidate in parent_passed:
            if candidate["confidence"] < self.config.slot_confidence_threshold:
                self._record_slot_status(
                    diagnostics=diagnostics,
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    kind=candidate["name"],
                    direction=direction,
                    candidate_snapshot=candidate,
                    status="dropped_low_confidence",
                    reason=(
                        "confidence below threshold "
                        f"{self.config.slot_confidence_threshold:.2f}"
                    ),
                )
                continue
            confidence_passed.append(candidate)

        # 4) direction gate
        direction_passed: List[Dict[str, Any]] = []
        for candidate in confidence_passed:
            if self._slot_direction_mismatch(direction=direction, candidate=candidate):
                self._record_slot_status(
                    diagnostics=diagnostics,
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    kind=candidate["name"],
                    direction=direction,
                    candidate_snapshot=candidate,
                    status="dropped_direction_mismatch",
                    reason="direction-specific naming guard rejected candidate",
                )
                continue
            direction_passed.append(candidate)

        # 5) duplicate gate
        deduped: List[Dict[str, Any]] = []
        seen_by_key: Dict[Tuple[str, str], int] = {}
        for candidate in direction_passed:
            key = (candidate["name"], candidate["parent"])
            existing_index = seen_by_key.get(key)
            if existing_index is None:
                seen_by_key[key] = len(deduped)
                deduped.append(candidate)
                continue
            existing = deduped[existing_index]
            if candidate["confidence"] > existing["confidence"]:
                self._record_slot_status(
                    diagnostics=diagnostics,
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    kind=existing["name"],
                    direction=direction,
                    candidate_snapshot=existing,
                    status="dropped_duplicate",
                    reason="lower confidence duplicate",
                )
                deduped[existing_index] = candidate
            else:
                self._record_slot_status(
                    diagnostics=diagnostics,
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    kind=candidate["name"],
                    direction=direction,
                    candidate_snapshot=candidate,
                    status="dropped_duplicate",
                    reason="lower confidence duplicate",
                )

        # 6) parent conflict gate (margin-aware)
        parent_resolved: List[Dict[str, Any]] = []
        by_name: Dict[str, List[Dict[str, Any]]] = {}
        for candidate in deduped:
            by_name.setdefault(candidate["name"], []).append(candidate)
        for candidates in by_name.values():
            if len(candidates) == 1:
                parent_resolved.extend(candidates)
                continue
            ordered = sorted(candidates, key=lambda item: item["confidence"], reverse=True)
            leader = ordered[0]
            runner_up = ordered[1]
            if (leader["confidence"] - runner_up["confidence"]) < self.config.slot_parent_conflict_margin:
                for candidate in ordered:
                    self._record_slot_status(
                        diagnostics=diagnostics,
                        skill_id=skill_id,
                        path=str(manifest.folder.path),
                        kind=candidate["name"],
                        direction=direction,
                        candidate_snapshot=candidate,
                        status="dropped_ambiguous_parent",
                        reason=(
                            "multiple parent guesses within confidence margin "
                            f"{self.config.slot_parent_conflict_margin:.2f}"
                        ),
                    )
                continue
            parent_resolved.append(leader)
            for candidate in ordered[1:]:
                self._record_slot_status(
                    diagnostics=diagnostics,
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    kind=candidate["name"],
                    direction=direction,
                    candidate_snapshot=candidate,
                    status="dropped_parent_conflict",
                    reason="lower confidence parent for the same slot name",
                )

        # 7) cardinality gate (max per direction kind)
        ordered = sorted(
            parent_resolved,
            key=lambda item: (
                -item["confidence"],
                item["name"],
                item["parent"],
            ),
        )
        kept: List[Dict[str, Any]] = ordered[: self.config.slot_max_per_kind]
        dropped = ordered[self.config.slot_max_per_kind :]
        for candidate in dropped:
            self._record_slot_status(
                diagnostics=diagnostics,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                kind=candidate["name"],
                direction=direction,
                candidate_snapshot=candidate,
                status="dropped_overflow",
                reason=(
                    "cardinality gate overflow: "
                    f"max={self.config.slot_max_per_kind} per kind"
                ),
            )

        accepted: List[SlotRef] = []
        for candidate in kept:
            slot_ref = SlotRef(
                name=candidate["name"],
                parent=candidate["parent"],
                confidence=float(candidate["confidence"]),
                source="llm_slot_candidate",
                status="accepted",
                evidence=str(candidate.get("evidence") or ""),
            )
            accepted.append(slot_ref)
            self._record_slot_status(
                diagnostics=diagnostics,
                skill_id=skill_id,
                path=str(manifest.folder.path),
                kind=slot_ref.name,
                direction=direction,
                candidate_snapshot=candidate,
                status="accepted",
                reason="passed all slot gates",
            )
        return accepted

    def _slot_schema_gate(
        self,
        raw_candidate: Union[SlotCandidate, Dict[str, Any], Any],
    ) -> Optional[Dict[str, Any]]:
        data = to_dict(raw_candidate)
        raw_kind = str(data.get("kind") or "").strip()
        raw_parent = str(data.get("parent_guess") or "").strip()
        raw_evidence = str(data.get("evidence") or "").strip()
        if not raw_kind or not raw_parent:
            return None
        normalized_kind = normalize_parameter_name(raw_kind)
        normalized_parent = normalize_parameter_name(raw_parent)
        if normalized_kind != raw_kind or normalized_parent != raw_parent:
            return None
        try:
            confidence = float(data.get("confidence"))
        except (TypeError, ValueError):
            return None
        if confidence < 0.0 or confidence > 1.0:
            return None
        return {
            "name": normalized_kind,
            "parent": normalized_parent,
            "confidence": confidence,
            "evidence": raw_evidence,
        }

    def _slot_direction_mismatch(
        self,
        *,
        direction: str,
        candidate: Dict[str, Any],
    ) -> bool:
        name = str(candidate.get("name") or "")
        if direction == "emits":
            return name.startswith("consumes_") or name.startswith("input_")
        if direction == "consumes":
            return name.startswith("emits_") or name.startswith("output_")
        return False

    def _role_shape_gate(
        self,
        *,
        skill_id: str,
        manifest: RawSkillManifest,
        emits: List[SlotRef],
        consumes: List[SlotRef],
        diagnostics: List[ExtractionDiagnostic],
    ) -> None:
        emit_parents = {slot.parent for slot in emits}
        consume_parents = {slot.parent for slot in consumes}
        if consumes and not emits:
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="slot_gate",
                    severity="warning",
                    code="slot_role_shape_warning",
                    message="skill consumes slots but emits none",
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    details={
                        "emit_parents": sorted(emit_parents),
                        "consume_parents": sorted(consume_parents),
                    },
                )
            )
        if "delivery_brief" in consume_parents and "delivery_brief" not in emit_parents:
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="slot_gate",
                    severity="warning",
                    code="slot_role_shape_warning",
                    message="skill consumes delivery_brief without re-emitting delivery_brief",
                    skill_id=skill_id,
                    path=str(manifest.folder.path),
                    details={
                        "emit_parents": sorted(emit_parents),
                        "consume_parents": sorted(consume_parents),
                    },
                )
            )

    def _slot_candidate_snapshot(
        self,
        raw_candidate: Union[SlotCandidate, Dict[str, Any], Any],
    ) -> Dict[str, Any]:
        data = to_dict(raw_candidate)
        return {
            "kind": str(data.get("kind") or ""),
            "parent_guess": str(data.get("parent_guess") or ""),
            "confidence": data.get("confidence"),
            "evidence": str(data.get("evidence") or ""),
        }

    def _record_slot_status(
        self,
        *,
        diagnostics: List[ExtractionDiagnostic],
        skill_id: str,
        path: str,
        kind: str,
        direction: str,
        candidate_snapshot: Dict[str, Any],
        status: str,
        reason: str,
    ) -> None:
        diagnostics.append(
            ExtractionDiagnostic(
                stage="slot_gate",
                severity="info" if status == "accepted" else "warning",
                code="slot_candidate_processed",
                message=f"slot candidate {status}",
                skill_id=skill_id,
                path=path,
                details={
                    "skill_id": skill_id,
                    "kind": kind,
                    "direction": direction,
                    "candidate": candidate_snapshot,
                    "status": status,
                    "reason": reason,
                },
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
