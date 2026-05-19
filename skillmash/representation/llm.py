"""Shared helpers for OpenAI-compatible LLM calls."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def create_openai_client(config: LLMConfig):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The openai package is required for LLM extraction. "
            "Install dependencies with `uv sync` or `pip install openai`."
        ) from exc

    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
    )


def extract_message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None) or getattr(item, "content", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()

    for attr in ("parsed", "json", "output_text"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)

    return ""


def safe_model_dump(value: Any) -> str:
    try:
        if hasattr(value, "model_dump"):
            data = value.model_dump()
        elif hasattr(value, "to_dict"):
            data = value.to_dict()
        elif hasattr(value, "__dict__"):
            data = dict(value.__dict__)
        else:
            data = repr(value)
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    return text[:2000]


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
