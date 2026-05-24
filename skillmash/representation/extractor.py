"""LLM-backed schema extraction."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from skillmash.common.llm import (
    LLMConfig,
    create_llm_client,
)
from skillmash.representation.models import (
    ArtifactSpec,
    ExtractedSkillSchema,
    ParameterSpec,
    RawSkillManifest,
    SlotCandidate,
)


class OpenAICompatibleSchemaExtractor:
    """Extract Skill IO schema through an OpenAI-compatible chat endpoint."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.client = create_llm_client(config)
        self.use_batch = config.backend == "vllm"

    def extract(self, manifest: RawSkillManifest) -> ExtractedSkillSchema:
        content = self.client.complete_json(
            system_prompt=_SYSTEM_PROMPT,
            user_content=json.dumps(
                _build_llm_context(manifest),
                ensure_ascii=False,
                indent=2,
            ),
            error_context="LLM schema extraction",
        )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "LLM response is not valid JSON. "
                f"content_prefix={content[:1000]!r}"
            ) from exc
        return schema_from_llm_payload(payload)

    def extract_many(
        self,
        manifests: List[RawSkillManifest],
    ) -> List[ExtractedSkillSchema]:
        contents = self.client.complete_json_many(
            [
                {
                    "system_prompt": _SYSTEM_PROMPT,
                    "user_content": json.dumps(
                        _build_llm_context(manifest),
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
                for manifest in manifests
            ],
            error_context="LLM schema extraction batch",
        )
        schemas: List[ExtractedSkillSchema] = []
        for index, content in enumerate(contents, start=1):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "LLM batch response item is not valid JSON. "
                    f"item={index} content_prefix={content[:1000]!r}"
                ) from exc
            schemas.append(schema_from_llm_payload(payload))
        return schemas

def schema_from_llm_payload(payload: Dict[str, Any]) -> ExtractedSkillSchema:
    """Convert a raw LLM JSON payload into ExtractedSkillSchema."""

    return ExtractedSkillSchema(
        description=str(payload.get("description") or ""),
        tasks=[str(item) for item in payload.get("tasks", [])],
        inputs=[_parameter_from_payload(item) for item in payload.get("inputs", [])],
        outputs=[_artifact_from_payload(item) for item in payload.get("outputs", [])],
        emits_slots=[_slot_candidate_from_payload(item) for item in payload.get("emits_slots", [])],
        consumes_slots=[_slot_candidate_from_payload(item) for item in payload.get("consumes_slots", [])],
        constraints=[str(item) for item in payload.get("constraints", [])],
        confidence=payload.get("confidence"),
        warnings=[str(item) for item in payload.get("warnings", [])],
    )

def _parameter_from_payload(payload: Dict[str, Any]) -> ParameterSpec:
    return ParameterSpec(
        name=str(payload.get("name") or "input"),
        type=_combined_type_from_payload(payload, "text"),
        required=bool(payload.get("required", True)),
        description=str(payload.get("description") or ""),
        default=payload.get("default"),
        schema_ref=payload.get("schema_ref"),
    )


def _artifact_from_payload(payload: Dict[str, Any]) -> ArtifactSpec:
    return ArtifactSpec(
        name=str(payload.get("name") or "result"),
        type=_combined_type_from_payload(payload, "unknown"),
        description=str(payload.get("description") or ""),
        schema_ref=payload.get("schema_ref"),
    )


def _combined_type_from_payload(payload: Dict[str, Any], default: str) -> str:
    return str(payload.get("format") or payload.get("type") or default)


def _slot_candidate_from_payload(payload: Dict[str, Any]) -> SlotCandidate:
    if not isinstance(payload, dict):
        payload = {}
    return SlotCandidate(
        kind=str(payload.get("kind") or ""),
        parent_guess=str(payload.get("parent_guess") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        evidence=str(payload.get("evidence") or ""),
    )


def _build_llm_context(manifest: RawSkillManifest) -> Dict[str, Any]:
    return {
        "source": {
            "relative_path": manifest.folder.relative_path,
            "entry": "SKILL.md",
        },
        "frontmatter": manifest.frontmatter,
        "body": manifest.body[:12000],
    }

_SYSTEM_PROMPT = """You extract structured Skill representations from SKILL.md files.

Return JSON only. Do not include markdown.

Required JSON object fields:
- description: concise string
- tasks: array of short capability/action terms
- inputs: array of {name, type, required, description, optional schema_ref}
- outputs: array of {name, type, description, optional schema_ref}
- constraints: array of strings
- emits_slots: array of {kind, parent_guess, confidence, evidence}
- consumes_slots: array of {kind, parent_guess, confidence, evidence}
- confidence: number between 0 and 1
- warnings: array of strings

Use name for the semantic role and type for the data representation that is
passed between Skills.

Use tasks for the Skill's normalized capabilities, such as search, summarize,
translate, extract, analyze, write, generate, convert, validate, or execute.
Prefer one to three short verb terms.

Prefer these type values:
text, markdown, json, csv, pdf, html, docx, pptx, xlsx, png, jpg, svg,
audio, video, file, path, url, yaml, code, unknown.

For example, a PDF paper should use name=paper and type=pdf. A markdown
summary should use name=summary and type=markdown.

Use input and output names as canonical semantic vocab terms for graph linking. Prefer
short noun roles such as query, topic, url, paper, summary, report, table,
image, code, file, path, or result when they fit. Put details in description
instead of making highly specific parameter names.

You must always output emits_slots and consumes_slots, even when empty.
Each slot candidate kind must be snake_case.
Each slot candidate must include concise evidence quoted from source content.
Use one of these parent_guess values when possible:
requirements_review_findings, design_review_findings, security_findings,
test_findings, delivery_brief.

Do not emit duplicate inputs for the same caller-provided value. Omit logging,
analytics, telemetry, statistics, tracing, or original-copy fields unless the
Skill truly needs a separate value from the caller to run.

If unsure, use unknown and add a warning.
"""
