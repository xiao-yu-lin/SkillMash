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
from skillmash.orchestration.planning.models import ArtifactRef, GroundedQuery, GroundingClient
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
    available = merge_artifacts(
        implicit_artifacts(),
        llm_grounding.get("available_artifacts", []),
    )
    goal_terms = ground_goal_terms(
        query_terms=query_terms,
        artifacts=artifacts,
    )
    return GroundedQuery(
        query=query,
        query_terms=query_terms,
        available_artifacts=available,
        goal_terms=goal_terms,
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
    return normalize_llm_grounding(parsed, known_artifact_refs(artifacts))


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
    return {
        "available_artifacts": normalized_artifacts,
        "goal_terms": goal_terms,
    }


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
