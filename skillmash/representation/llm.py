"""Shared helpers for API and local vLLM-backed LLM calls."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Protocol, Union


@dataclass(frozen=True)
class LLMConfig:
    """LLM chat configuration.

    If model resolves to an existing local path, SkillMash uses vLLM in-process.
    Otherwise it uses an OpenAI-compatible chat completions API.
    """

    api_key: str = ""
    model: str = ""
    base_url: str = "https://api.openai.com/v1"
    temperature: float = 0.0
    timeout_seconds: int = 60
    max_tokens: int = 2048
    batch_size: int = 32

    @classmethod
    def from_env(cls, env_path: Union[Path, str] = ".env") -> "LLMConfig":
        values = _load_env_file(Path(env_path))
        merged = {**values, **os.environ}

        model = merged.get("LLM_MODEL")
        if not model:
            raise RuntimeError(
                "Missing LLM model. Set LLM_MODEL in .env or environment."
            )

        api_key = merged.get("LLM_API_KEY") or ""
        if not api_key and not is_local_model_path(model):
            raise RuntimeError(
                "Missing LLM API key for API mode. Set LLM_API_KEY in .env "
                "or environment, or set LLM_MODEL to an "
                "existing local model path to use vLLM offline mode."
            )

        base_url = merged.get("LLM_BASE_URL") or cls.base_url
        temperature = float(merged.get("LLM_TEMPERATURE") or 0)
        timeout_seconds = int(merged.get("LLM_TIMEOUT_SECONDS") or 60)
        max_tokens = int(merged.get("LLM_MAX_TOKENS") or 2048)
        batch_size = int(merged.get("LLM_BATCH_SIZE") or 32)
        return cls(
            model=model,
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            batch_size=max(1, batch_size),
        )

    @property
    def backend(self) -> str:
        return "vllm" if is_local_model_path(self.model) else "api"


class ChatLLMClient(Protocol):
    """Minimal shared chat client used by extraction and graph building."""

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        timeout: Optional[int] = None,
        error_context: str = "LLM",
    ) -> str:
        ...

    def complete_json_many(
        self,
        requests: List[Dict[str, str]],
        *,
        timeout: Optional[int] = None,
        error_context: str = "LLM",
    ) -> List[str]:
        ...


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


def create_llm_client(config: LLMConfig) -> ChatLLMClient:
    if config.backend == "vllm":
        return VLLMOfflineChatClient(config)
    return OpenAICompatibleChatClient(config)


def is_local_model_path(model: str) -> bool:
    if not model:
        return False
    return Path(model).expanduser().exists()


class OpenAICompatibleChatClient:
    """Shared wrapper around OpenAI-compatible chat completions."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.client = create_openai_client(config)

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        timeout: Optional[int] = None,
        error_context: str = "LLM",
    ) -> str:
        request: Dict[str, Any] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if timeout is not None:
            request["timeout"] = timeout
        try:
            response = self.client.chat.completions.create(**request)
        except Exception as exc:
            raise RuntimeError(f"{error_context} request failed: {exc}") from exc

        choice = response.choices[0]
        content = extract_message_content(choice.message)
        if not content:
            raise RuntimeError(
                f"{error_context} response content is empty. "
                f"finish_reason={getattr(choice, 'finish_reason', None)!r}; "
                f"message={safe_model_dump(choice.message)}"
            )
        return content

    def complete_json_many(
        self,
        requests: List[Dict[str, str]],
        *,
        timeout: Optional[int] = None,
        error_context: str = "LLM",
    ) -> List[str]:
        return [
            self.complete_json(
                system_prompt=request["system_prompt"],
                user_content=request["user_content"],
                timeout=timeout,
                error_context=f"{error_context} item {index}",
            )
            for index, request in enumerate(requests, start=1)
        ]


class VLLMOfflineChatClient:
    """In-process vLLM chat client for local model paths."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._llm = None
        self._sampling_params = None
        self._lock = Lock()

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        timeout: Optional[int] = None,
        error_context: str = "LLM",
    ) -> str:
        del timeout
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        with self._lock:
            llm, sampling_params = self._engine()
            try:
                outputs = llm.generate(
                    [_messages_to_prompt(llm, messages)],
                    sampling_params=sampling_params,
                    use_tqdm=False,
                )
            except Exception as exc:
                raise RuntimeError(f"{error_context} vLLM request failed: {exc}") from exc

        if not outputs or not outputs[0].outputs:
            raise RuntimeError(f"{error_context} vLLM response content is empty.")
        content = outputs[0].outputs[0].text.strip()
        if not content:
            raise RuntimeError(f"{error_context} vLLM response content is empty.")
        return _strip_json_fences(content)

    def complete_json_many(
        self,
        requests: List[Dict[str, str]],
        *,
        timeout: Optional[int] = None,
        error_context: str = "LLM",
    ) -> List[str]:
        del timeout
        if not requests:
            return []

        with self._lock:
            llm, sampling_params = self._engine()
            prompts = [
                _messages_to_prompt(
                    llm,
                    [
                        {
                            "role": "system",
                            "content": request["system_prompt"],
                        },
                        {
                            "role": "user",
                            "content": request["user_content"],
                        },
                    ],
                )
                for request in requests
            ]
            try:
                outputs = llm.generate(
                    prompts,
                    sampling_params=sampling_params,
                    use_tqdm=False,
                )
            except Exception as exc:
                raise RuntimeError(f"{error_context} vLLM batch request failed: {exc}") from exc

        if len(outputs) != len(requests):
            raise RuntimeError(
                f"{error_context} vLLM batch returned {len(outputs)} outputs for "
                f"{len(requests)} requests."
            )

        contents: List[str] = []
        for index, output in enumerate(outputs, start=1):
            if not output.outputs:
                raise RuntimeError(
                    f"{error_context} vLLM batch response item {index} is empty."
                )
            content = output.outputs[0].text.strip()
            if not content:
                raise RuntimeError(
                    f"{error_context} vLLM batch response item {index} is empty."
                )
            contents.append(_strip_json_fences(content))
        return contents

    def _engine(self):
        if self._llm is None or self._sampling_params is None:
            try:
                from vllm import LLM, SamplingParams
            except ImportError as exc:
                raise RuntimeError(
                    "The vllm package is required when the LLM model is a local "
                    "path. Install vllm or use an API model name instead."
                ) from exc

            self._llm = LLM(model=self.config.model)
            self._sampling_params = SamplingParams(
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
        return self._llm, self._sampling_params


def _messages_to_prompt(llm: Any, messages: List[Dict[str, str]]) -> str:
    try:
        tokenizer = llm.get_tokenizer()
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    except Exception:
        sections = []
        for message in messages:
            role = message["role"].upper()
            sections.append(f"{role}:\n{message['content']}")
        sections.append("ASSISTANT:\n")
        return "\n\n".join(sections)


def _strip_json_fences(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def extract_message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: List[str] = []
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


def _load_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}

    values: Dict[str, str] = {}
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
