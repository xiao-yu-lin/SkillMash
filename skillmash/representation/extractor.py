"""LLM-backed schema extraction."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillmash.representation.models import (
    ArtifactSpec,
    ExtractedSkillSchema,
    ParameterSpec,
    RawSkillManifest,
)


@dataclass(frozen=True)
class LLMConfig:
    """OpenAI-compatible chat completions configuration."""

    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    temperature: float = 0.0
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls, env_path: Path | str = ".env") -> "LLMConfig":
        values = _load_env_file(Path(env_path))
        merged = {**values, **os.environ}

        api_key = (
            merged.get("SKILLMASH_LLM_API_KEY")
            or merged.get("OPENAI_API_KEY")
            or merged.get("LLM_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "Missing LLM API key. Set OPENAI_API_KEY in .env or environment."
            )

        model = (
            merged.get("SKILLMASH_LLM_MODEL")
            or merged.get("OPENAI_MODEL")
            or merged.get("LLM_MODEL")
        )
        if not model:
            raise RuntimeError(
                "Missing LLM model. Set OPENAI_MODEL in .env or environment."
            )

        base_url = (
            merged.get("SKILLMASH_LLM_BASE_URL")
            or merged.get("OPENAI_BASE_URL")
            or merged.get("LLM_BASE_URL")
            or cls.base_url
        )
        temperature = float(merged.get("SKILLMASH_LLM_TEMPERATURE") or 0)
        timeout_seconds = int(merged.get("SKILLMASH_LLM_TIMEOUT_SECONDS") or 60)
        return cls(
            api_key=api_key,
            model=model,
            base_url=base_url.rstrip("/"),
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )


class OpenAICompatibleSchemaExtractor:
    """Extract Skill IO schema through an OpenAI-compatible chat endpoint."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def extract(self, manifest: RawSkillManifest) -> ExtractedSkillSchema:
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
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
            "response_format": {"type": "json_object"},
        }
        response = self._post_chat_completions(payload)
        content = response["choices"][0]["message"]["content"]
        return schema_from_llm_payload(json.loads(content))

    def _post_chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url=f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.config.timeout_seconds,
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"LLM request failed with HTTP {exc.code}: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        return json.loads(body)


def schema_from_llm_payload(payload: dict[str, Any]) -> ExtractedSkillSchema:
    """Convert a raw LLM JSON payload into ExtractedSkillSchema."""

    return ExtractedSkillSchema(
        description=str(payload.get("description") or ""),
        inputs=[_parameter_from_payload(item) for item in payload.get("inputs", [])],
        outputs=[_artifact_from_payload(item) for item in payload.get("outputs", [])],
        skill_tags=[str(item) for item in payload.get("skill_tags", [])],
        data_tags=[str(item) for item in payload.get("data_tags", [])],
        constraints=[str(item) for item in payload.get("constraints", [])],
        cost=dict(payload.get("cost") or {}),
        quality=dict(payload.get("quality") or {}),
        confidence=payload.get("confidence"),
        warnings=[str(item) for item in payload.get("warnings", [])],
    )


def _parameter_from_payload(payload: dict[str, Any]) -> ParameterSpec:
    return ParameterSpec(
        name=str(payload.get("name") or "input"),
        type=str(payload.get("type") or "text"),
        required=bool(payload.get("required", True)),
        description=str(payload.get("description") or ""),
        default=payload.get("default"),
    )


def _artifact_from_payload(payload: dict[str, Any]) -> ArtifactSpec:
    return ArtifactSpec(
        name=str(payload.get("name") or "result"),
        type=str(payload.get("type") or "unknown"),
        description=str(payload.get("description") or ""),
    )


def _build_llm_context(manifest: RawSkillManifest) -> dict[str, Any]:
    return {
        "source": {
            "relative_path": manifest.folder.relative_path,
            "entry": "SKILL.md",
        },
        "frontmatter": manifest.frontmatter,
        "body": manifest.body[:12000],
    }


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


_SYSTEM_PROMPT = """You extract structured Skill representations from SKILL.md files.

Return JSON only. Do not include markdown.

Required JSON object fields:
- description: concise string
- inputs: array of {name, type, required, description}
- outputs: array of {name, type, description}
- skill_tags: array of short capability tags
- data_tags: array of short data/artifact tags
- constraints: array of strings
- confidence: number between 0 and 1
- warnings: array of strings

Use semantic artifact types for inputs and outputs. Prefer:
text, url, file, path, paper, dataset, image, audio, video, table, code,
json, report, summary, diagram, pptx, unknown.

If unsure, use unknown and add a warning.
"""
