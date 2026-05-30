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
)


class LLMSchemaExtractor:
    """Extract Skill IO schema using LLM."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.client = create_llm_client(config)
        self.use_batch = config.backend == "vllm"

    def extract(self, manifest: RawSkillManifest) -> ExtractedSkillSchema:
        content = self.client.complete_json(
            system_prompt=_SCHEMA_EXTRACTION_PROMPT,
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
                    "system_prompt": _SCHEMA_EXTRACTION_PROMPT,
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

    warnings = [str(item) for item in payload.get("warnings", [])]
    raw_output_notes = payload.get("raw_output_notes", [])
    if isinstance(raw_output_notes, str):
        raw_output_notes = [raw_output_notes]
    warnings.extend(str(item) for item in raw_output_notes if str(item).strip())

    return ExtractedSkillSchema(
        description=str(payload.get("description") or ""),
        inputs=[_parameter_from_payload(item) for item in payload.get("inputs", [])],
        outputs=[_artifact_from_payload(item) for item in payload.get("outputs", [])],
        confidence=payload.get("confidence"),
        warnings=warnings,
    )

def _parameter_from_payload(payload: Dict[str, Any]) -> ParameterSpec:
    return ParameterSpec(
        name=str(payload.get("name") or "input"),
        type=_combined_type_from_payload(payload, "text"),
        required=bool(payload.get("required", True)),
        description=str(payload.get("description") or ""),
        default=payload.get("default"),
    )


def _artifact_from_payload(payload: Dict[str, Any]) -> ArtifactSpec:
    return ArtifactSpec(
        name=str(payload.get("name") or "result"),
        type=_combined_type_from_payload(payload, "unknown"),
        description=str(payload.get("description") or ""),
    )


def _combined_type_from_payload(payload: Dict[str, Any], default: str) -> str:
    return str(payload.get("format") or payload.get("type") or default)

def _build_llm_context(manifest: RawSkillManifest) -> Dict[str, Any]:
    return {
        "source": {
            "relative_path": manifest.folder.relative_path,
            "entry": "SKILL.md",
        },
        "frontmatter": manifest.frontmatter,
        "body": manifest.body[:12000],
    }

_SCHEMA_EXTRACTION_PROMPT = """You extract structured Skill IO representations from SKILL.md files.

Return JSON only. Do not include markdown.

Required JSON object fields:
- description: concise string
- inputs: array of {name, type, required, description}
- outputs: array of {name, type, description}
- confidence: number between 0 and 1
- warnings: array of strings
Optional JSON object fields:
- raw_output_notes: array of strings describing raw API or script return fields
  that helped your reasoning but should not be recorded as output artifacts.

Read the entire SKILL.md, not only input/output tables. Sections such as
"when to use", "output example", "return format", "notes", "summary", and final
instructions may enrich or override formal API field tables.

Outputs must represent user-facing/downstream deliverables: artifacts the Skill
promises to hand to the user or to another Skill. Do not emit raw API/control fields as outputs.
This includes errorCode, errorMsg, status, logs, debug fields, or internal JSON
containers such as raw result, imageResult, or textResult, unless the document
says that raw structure is the actual deliverable.

If a raw API response contains a useful deliverable inside a nested field,
extract the deliverable as a semantic output instead of the raw container. For
example, textResult[].translateText may become translated_text with type text.
If the document says to send, show, return, or provide something in markdown,
markdown delivery instructions must be represented as markdown outputs.

Use name for the semantic role and type for the data representation that is
passed between Skills.

Prefer these type values:
text, markdown, json, csv, pdf, html, docx, pptx, xlsx, png, jpg, svg,
audio, video, file, path, url, yaml, code, unknown.

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
