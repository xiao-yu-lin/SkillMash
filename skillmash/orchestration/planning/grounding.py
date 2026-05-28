"""LLM grounding for orchestration queries."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.orchestration.planning.constants import (
    DEFAULT_USER_ARTIFACTS,
    LLM_GROUNDING_SYSTEM_PROMPT,
)
from skillmash.orchestration.planning.models import (
    ArtifactRef,
    GroundedQuery,
    GroundingClient,
    InferredInput,
)
from skillmash.orchestration.planning.utils import tokenize


def implicit_artifacts() -> list[ArtifactRef]:
    artifacts = [
        ArtifactRef(name=name, type=type_, source="implicit_query")
        for name, type_ in DEFAULT_USER_ARTIFACTS
    ]
    return sorted(artifacts, key=lambda item: (item.name, item.type, item.source))


def ground_query(
    *,
    query: str,
    artifacts: BuildArtifacts,
    llm_client: GroundingClient,
) -> GroundedQuery:
    llm_grounding = ground_query_with_llm(
        query=query,
        artifacts=artifacts,
        llm_client=llm_client,
    )
    query_terms = set(llm_grounding.get("goal_terms", set()))
    query_terms.update(tokenize(query))
    available = merge_artifacts(
        implicit_artifacts(),
        llm_grounding.get("available_artifacts", []),
    )
    goal_terms = ground_goal_terms(
        query_terms=query_terms,
        artifacts=artifacts,
    )
    inferred_inputs = merge_inferred_inputs(
        llm_grounding.get("inferred_inputs", []),
        deterministic_inferred_inputs(query, artifacts),
    )
    return GroundedQuery(
        query=query,
        query_terms=query_terms,
        available_artifacts=available,
        goal_terms=goal_terms,
        inferred_inputs=inferred_inputs,
    )


def ground_query_with_llm(
    *,
    query: str,
    artifacts: BuildArtifacts,
    llm_client: GroundingClient,
) -> dict[str, Any]:
    payload = {
        "query": query,
        "artifact_vocabulary": artifact_vocab_payload(artifacts),
        "output_vocabulary": sorted(artifacts.index.get("by_output", {}))[:200],
    }
    raw = llm_client.complete_json(
        system_prompt=LLM_GROUNDING_SYSTEM_PROMPT,
        user_content=json.dumps(payload, ensure_ascii=False),
        error_context="orchestration query grounding",
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid query grounding JSON: {raw[:500]}") from exc
    return normalize_llm_grounding(
        parsed,
        known_artifact_refs(artifacts),
        known_inferred_input_refs(artifacts),
    )


def known_artifact_refs(artifacts: BuildArtifacts) -> dict[tuple[str, str], ArtifactRef]:
    refs: dict[tuple[str, str], ArtifactRef] = {}
    for name, type_, _ in artifact_phrases(artifacts):
        ref = ArtifactRef(
            name=name,
            type=type_,
            source="llm_grounding",
        )
        refs[ref.key] = ref
    return refs


def known_inferred_input_refs(
    artifacts: BuildArtifacts,
) -> dict[tuple[str, str, str], InferredInput]:
    refs = {}
    for skill in artifacts.skills:
        skill_id = str(skill.get("id") or "")
        if not skill_id:
            continue
        for item in skill.get("inputs", []):
            name = str(item.get("name") or "")
            if not name:
                continue
            type_ = str(item.get("type") or "unknown")
            refs[(skill_id, name, type_)] = InferredInput(
                skill_id=skill_id,
                name=name,
                type=type_,
            )
    return refs


def ground_goal_terms(*, query_terms: set[str], artifacts: BuildArtifacts) -> set[str]:
    goal_terms = set(query_terms)
    for bucket_name in ("by_output", "by_text_term"):
        bucket = artifacts.index.get(bucket_name, {})
        for key in bucket:
            key_terms = tokenize(key)
            if query_terms & key_terms:
                goal_terms.update(key_terms)
    return goal_terms


def artifact_phrases(artifacts: BuildArtifacts) -> list[tuple[str, str, list[str]]]:
    by_name: dict[str, set[str]] = defaultdict(set)
    phrases_by_name: dict[str, set[str]] = defaultdict(set)

    for skill in artifacts.skills:
        for item in [*skill.get("inputs", []), *skill.get("outputs", [])]:
            name = str(item.get("name") or "")
            if not name:
                continue
            type_ = str(item.get("type") or "unknown")
            by_name[name].add(type_)
            phrases_by_name[name].update(
                [
                    name,
                    str(item.get("description") or ""),
                    type_,
                ]
            )

    for term in vocab_terms(artifacts.io_name_vocab):
        name = str(term.get("name") or "")
        if not name:
            continue
        phrases_by_name[name].add(name)
        phrases_by_name[name].update(str(item) for item in term.get("aliases", []))
        phrases_by_name[name].update(str(item) for item in term.get("examples", []))
        for type_ in term.get("allowed_types", []):
            by_name[name].add(str(type_ or "unknown"))

    result: list[tuple[str, str, list[str]]] = []
    for name, types in by_name.items():
        phrases = sorted(phrase for phrase in phrases_by_name[name] if phrase)
        for type_ in sorted(types or {"unknown"}):
            result.append((name, type_, phrases))
    return result


def artifact_vocab_payload(artifacts: BuildArtifacts) -> list[dict[str, Any]]:
    payload = []
    for name, type_, phrases in artifact_phrases(artifacts):
        payload.append(
            {
                "name": name,
                "type": type_,
                "aliases_or_examples": phrases[:6],
            }
        )
    return payload[:300]


def normalize_llm_grounding(
    payload: dict[str, Any],
    known_refs: dict[tuple[str, str], ArtifactRef],
    known_inferred_refs: dict[tuple[str, str, str], InferredInput] | None = None,
) -> dict[str, Any]:
    normalized_artifacts = []
    for item in payload.get("available_artifacts", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        type_ = str(item.get("type") or "unknown")
        ref = known_refs.get((name, type_))
        if ref is None:
            matching = [
                candidate
                for key, candidate in known_refs.items()
                if key[0] == name and (type_ == "unknown" or key[1] == "unknown")
            ]
            ref = matching[0] if matching else None
        if ref is not None:
            normalized_artifacts.append(ref)

    goal_terms = set()
    for term in payload.get("goal_terms", []):
        goal_terms.update(tokenize(str(term)))
    inferred_inputs = normalize_inferred_inputs(
        payload.get("inferred_inputs", []),
        known_inferred_refs or {},
    )
    return {
        "available_artifacts": normalized_artifacts,
        "inferred_inputs": inferred_inputs,
        "goal_terms": goal_terms,
    }


INFERABLE_INPUT_NAMES = {
    "auto_emotion",
    "backend",
    "category",
    "command",
    "format",
    "html",
    "language_code",
    "model_id",
    "output_spec",
    "publish_channel",
    "song_type",
    "target_language",
    "variant_mode",
}


def normalize_inferred_inputs(
    values: Any,
    known_refs: dict[tuple[str, str, str], InferredInput],
) -> list[InferredInput]:
    if not isinstance(values, list):
        return []
    normalized = []
    for item in values:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "")
        name = str(item.get("name") or "")
        if not skill_id or not name or name not in INFERABLE_INPUT_NAMES:
            continue
        type_ = str(item.get("type") or "unknown")
        ref = known_refs.get((skill_id, name, type_))
        if ref is None:
            matching = [
                candidate
                for key, candidate in known_refs.items()
                if key[0] == skill_id and key[1] == name
            ]
            ref = matching[0] if matching else None
        if ref is None:
            continue
        value = item.get("value")
        if value is None or str(value).strip() == "":
            continue
        normalized.append(
            InferredInput(
                skill_id=ref.skill_id,
                name=ref.name,
                type=ref.type,
                value=value,
            )
        )
    return normalized


def deterministic_inferred_inputs(
    query: str,
    artifacts: BuildArtifacts,
) -> list[InferredInput]:
    """Recover obvious control inputs that are easy for LLM grounding to miss."""

    query_text = str(query or "").lower()
    refs = known_inferred_input_refs(artifacts)
    inferred: list[InferredInput] = []

    if _looks_like_send_email_request(query_text):
        inferred.extend(
            _inferred_for_input(
                refs,
                name="command",
                value="send",
                skill_predicate=_looks_like_email_skill,
            )
        )

    target_language = _deterministic_translation_target(query_text)
    if target_language:
        inferred.extend(
            _inferred_for_input(
                refs,
                name="target_language",
                value=target_language,
                skill_predicate=_looks_like_image_translation_skill,
            )
        )

    return inferred


def _inferred_for_input(
    refs: dict[tuple[str, str, str], InferredInput],
    *,
    name: str,
    value: str,
    skill_predicate,
) -> list[InferredInput]:
    output: list[InferredInput] = []
    for (skill_id, input_name, _), ref in refs.items():
        if input_name != name or not skill_predicate(skill_id):
            continue
        output.append(
            InferredInput(
                skill_id=ref.skill_id,
                name=ref.name,
                type=ref.type,
                value=value,
                source="deterministic_grounding",
            )
        )
    return output


def _looks_like_send_email_request(query_text: str) -> bool:
    send_terms = ("发邮件", "发送邮件", "send email", "email")
    return any(term in query_text for term in send_terms)


def _deterministic_translation_target(query_text: str) -> str | None:
    if "翻译" not in query_text and "translate" not in query_text:
        return None
    if any(term in query_text for term in ("翻译成英文", "译成英文", "to english")):
        return "en"
    if _contains_cjk(query_text) and any(
        term in query_text for term in ("英文", "english")
    ):
        return "zh-CHS"
    return None


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _looks_like_email_skill(skill_id: str) -> bool:
    text = skill_id.lower()
    return "email" in text or "smtp" in text or "mail" in text


def _looks_like_image_translation_skill(skill_id: str) -> bool:
    text = skill_id.lower()
    return "image" in text and ("translation" in text or "translate" in text)


def merge_inferred_inputs(
    base: list[InferredInput],
    extra: Iterable[InferredInput],
) -> list[InferredInput]:
    merged = {(item.skill_id, item.name): item for item in extra}
    for item in base:
        merged[(item.skill_id, item.name)] = item
    return sorted(
        merged.values(),
        key=lambda item: (item.skill_id, item.name, item.type, str(item.value)),
    )


def merge_artifacts(
    base: list[ArtifactRef],
    extra: Iterable[ArtifactRef],
) -> list[ArtifactRef]:
    merged = {artifact.key: artifact for artifact in base}
    for artifact in extra:
        merged.setdefault(artifact.key, artifact)
    return sorted(merged.values(), key=lambda item: (item.name, item.type, item.source))


def vocab_terms(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    terms = payload.get("terms", [])
    return [term for term in terms if isinstance(term, dict)]
