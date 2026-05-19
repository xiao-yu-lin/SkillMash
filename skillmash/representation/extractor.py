"""LLM-backed schema extraction."""

from __future__ import annotations

import json
from typing import Any

from skillmash.representation.llm import (
    LLMConfig,
    create_openai_client,
    extract_message_content,
    safe_model_dump,
)
from skillmash.representation.models import (
    ArtifactSpec,
    ExtractedSkillSchema,
    ParameterSpec,
    RawSkillManifest,
)


class OpenAICompatibleSchemaExtractor:
    """Extract Skill IO schema through an OpenAI-compatible chat endpoint."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.client = create_openai_client(config)

    def extract(self, manifest: RawSkillManifest) -> ExtractedSkillSchema:
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=self.config.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": _SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            _build_llm_context(manifest),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    },
                ],
            )
        except Exception as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        choice = response.choices[0]
        content = extract_message_content(choice.message)
        if not content:
            raise RuntimeError(
                "LLM response content is empty. "
                f"finish_reason={getattr(choice, 'finish_reason', None)!r}; "
                f"message={safe_model_dump(choice.message)}"
            )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "LLM response is not valid JSON. "
                f"content_prefix={content[:1000]!r}"
            ) from exc
        return schema_from_llm_payload(payload)

def schema_from_llm_payload(payload: dict[str, Any]) -> ExtractedSkillSchema:
    """Convert a raw LLM JSON payload into ExtractedSkillSchema."""

    return ExtractedSkillSchema(
        description=str(payload.get("description") or ""),
        inputs=[_parameter_from_payload(item) for item in payload.get("inputs", [])],
        outputs=[_artifact_from_payload(item) for item in payload.get("outputs", [])],
        constraints=[str(item) for item in payload.get("constraints", [])],
        confidence=payload.get("confidence"),
        warnings=[str(item) for item in payload.get("warnings", [])],
    )

def _parameter_from_payload(payload: dict[str, Any]) -> ParameterSpec:
    return ParameterSpec(
        name=str(payload.get("name") or "input"),
        type=_combined_type_from_payload(payload, "text"),
        required=bool(payload.get("required", True)),
        description=str(payload.get("description") or ""),
        default=payload.get("default"),
        schema_ref=payload.get("schema_ref"),
    )


def _artifact_from_payload(payload: dict[str, Any]) -> ArtifactSpec:
    return ArtifactSpec(
        name=str(payload.get("name") or "result"),
        type=_combined_type_from_payload(payload, "unknown"),
        description=str(payload.get("description") or ""),
        schema_ref=payload.get("schema_ref"),
    )


def _combined_type_from_payload(payload: dict[str, Any], default: str) -> str:
    return str(payload.get("format") or payload.get("type") or default)


def _build_llm_context(manifest: RawSkillManifest) -> dict[str, Any]:
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
- inputs: array of {name, type, required, description, optional schema_ref}
- outputs: array of {name, type, description, optional schema_ref}
- constraints: array of strings
- confidence: number between 0 and 1
- warnings: array of strings

Use name for the semantic role and type for the data representation that is
passed between Skills.

Prefer these type values:
text, markdown, json, csv, pdf, html, docx, pptx, xlsx, png, jpg, svg,
audio, video, file, path, url, unknown.

For example, a PDF paper should use name=paper and type=pdf. A markdown
summary should use name=summary and type=markdown.

Use input and output names as canonical semantic vocab terms for graph linking. Prefer
short noun roles such as query, topic, url, paper, summary, report, table,
image, code, file, path, or result when they fit. Put details in description
instead of making highly specific parameter names.

Do not emit duplicate inputs for the same caller-provided value. Omit logging,
analytics, telemetry, statistics, tracing, or original-copy fields unless the
Skill truly needs a separate value from the caller to run.

If unsure, use unknown and add a warning.
"""
