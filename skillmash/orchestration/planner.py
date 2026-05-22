"""Forward Skill orchestration over can_feed graphs."""

from __future__ import annotations

import re
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.representation.llm import LLMConfig, create_llm_client


DEFAULT_STOP_TERMS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "the",
    "this",
    "that",
    "to",
    "with",
}
DEFAULT_USER_ARTIFACTS = {
    ("goal", "text"),
    ("query", "text"),
}
_LLM_GROUNDING_SYSTEM_PROMPT = """You map a user request to an existing Skill vocabulary.
Return strict JSON only.

Rules:
- Select available_artifacts only when the user explicitly says they have, provide,
  attach, uploaded, or want to use that artifact. Do not invent artifacts.
- Select goal_terms from the provided task/output/vocabulary terms only.
- Preserve canonical artifact names and types exactly as provided.
- If uncertain, omit the item.

Schema:
{
  "available_artifacts": [{"name": "api_spec", "type": "yaml"}],
  "goal_terms": ["review", "audit"]
}
"""


@dataclass(frozen=True)
class ArtifactRef:
    """A normalized runtime artifact available to the orchestrator."""

    name: str
    type: str = "unknown"
    source: str = "user_query"

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.type)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "source": self.source}


@dataclass(frozen=True)
class GroundedQuery:
    """User query grounded into known artifacts and goal terms."""

    query: str
    query_terms: set[str]
    available_artifacts: list[ArtifactRef]
    goal_terms: set[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "query_terms": sorted(self.query_terms),
            "available_artifacts": [
                artifact.to_dict() for artifact in self.available_artifacts
            ],
            "goal_terms": sorted(self.goal_terms),
        }


@dataclass(frozen=True)
class PlanStep:
    """One Skill call in a candidate orchestration plan."""

    skill_id: str
    name: str
    tasks: list[str]
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    missing_inputs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "tasks": self.tasks,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "missing_inputs": self.missing_inputs,
        }


@dataclass(frozen=True)
class OrchestrationPlan:
    """A candidate Skill orchestration plan."""

    steps: list[PlanStep]
    produced_artifacts: list[ArtifactRef]
    missing_inputs: list[dict[str, Any]]
    can_feed_edges: list[dict[str, Any]]
    goal_score: float
    edge_confidence: float
    consumed_user_artifacts: int
    status: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "goal_score": round(self.goal_score, 3),
            "edge_confidence": round(self.edge_confidence, 3),
            "consumed_user_artifacts": self.consumed_user_artifacts,
            "stages": _plan_stages(self.steps, self.can_feed_edges),
            "steps": [
                {"step": index + 1, **step.to_dict()}
                for index, step in enumerate(self.steps)
            ],
            "produced_artifacts": [
                artifact.to_dict() for artifact in self.produced_artifacts
            ],
            "missing_inputs": self.missing_inputs,
            "can_feed_edges": self.can_feed_edges,
            "reasons": self.reasons,
        }


class GroundingClient(Protocol):
    """Minimal JSON completion interface used by Skill orchestration."""

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        timeout: int | None = None,
        error_context: str = "LLM",
    ) -> str:
        ...


@dataclass(frozen=True)
class _State:
    skill_ids: tuple[str, ...]
    available: frozenset[tuple[str, str]]
    edges: tuple[int, ...]


class SkillOrchestrator:
    """Build candidate plans from user inputs, intent, and can_feed edges."""

    def __init__(
        self,
        artifacts: BuildArtifacts,
        *,
        llm_config: LLMConfig | None = None,
        llm_client: GroundingClient | None = None,
        min_edge_confidence: float = 0.7,
        max_depth: int = 4,
        max_plans: int = 20,
        max_branch: int = 8,
    ) -> None:
        self.artifacts = artifacts
        self.min_edge_confidence = _clamp(min_edge_confidence)
        self.max_depth = max(1, max_depth)
        self.max_plans = max(1, max_plans)
        self.max_branch = max(1, max_branch)
        self.skill_by_id = artifacts.skill_by_id
        self.llm_config = llm_config
        if llm_client is not None:
            self.llm_client = llm_client
        elif llm_config is not None:
            self.llm_client = create_llm_client(llm_config)
        else:
            raise ValueError("SkillOrchestrator requires llm_config or llm_client.")
        self.can_feed_edges = [
            edge
            for edge in artifacts.graph.get("edges", [])
            if edge.get("type") == "can_feed"
            and float(edge.get("confidence") or 0.0) >= self.min_edge_confidence
        ]
        self.outgoing_edges = self._build_outgoing_edges(self.can_feed_edges)

    def plan(self, query: str) -> dict[str, Any]:
        grounded = self.ground_query(query)
        plans = self._search_plans(grounded)
        ranked = sorted(
            plans,
            key=lambda plan: (
                plan.status != "ready",
                len(plan.missing_inputs),
                -plan.consumed_user_artifacts,
                len(plan.steps),
                -plan.goal_score,
                -plan.edge_confidence,
            ),
        )[: self.max_plans]
        return {
            "query": query,
            "build_dir": str(self.artifacts.build_dir),
            "grounded_query": grounded.to_dict(),
            "plans": [plan.to_dict() for plan in ranked],
        }

    def ground_query(self, query: str) -> GroundedQuery:
        llm_grounding = self._ground_query_with_llm(query)
        query_terms = set(llm_grounding.get("goal_terms", set()))
        available = _merge_artifacts(
            self._implicit_artifacts(),
            llm_grounding.get("available_artifacts", []),
        )
        goal_terms = self._ground_goal_terms(query_terms)
        return GroundedQuery(
            query=query,
            query_terms=query_terms,
            available_artifacts=available,
            goal_terms=goal_terms,
        )

    def _implicit_artifacts(self) -> list[ArtifactRef]:
        artifacts = [
            ArtifactRef(name=name, type=type_, source="implicit_query")
            for name, type_ in DEFAULT_USER_ARTIFACTS
        ]
        return sorted(artifacts, key=lambda item: (item.name, item.type, item.source))

    def _ground_query_with_llm(self, query: str) -> dict[str, Any]:
        payload = {
            "query": query,
            "artifact_vocabulary": _artifact_vocab_payload(self.artifacts),
            "task_vocabulary": _task_vocab_payload(self.artifacts),
            "output_vocabulary": sorted(self.artifacts.index.get("by_output", {}))[:200],
        }
        raw = self.llm_client.complete_json(
            system_prompt=_LLM_GROUNDING_SYSTEM_PROMPT,
            user_content=json.dumps(payload, ensure_ascii=False),
            error_context="orchestration query grounding",
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid query grounding JSON: {raw[:500]}") from exc
        return _normalize_llm_grounding(parsed, self._known_artifact_refs())

    def _known_artifact_refs(self) -> dict[tuple[str, str], ArtifactRef]:
        refs: dict[tuple[str, str], ArtifactRef] = {}
        for name, type_, _ in _artifact_phrases(self.artifacts):
            ref = ArtifactRef(
                name=name,
                type=type_,
                source="llm_grounding",
            )
            refs[ref.key] = ref
        return refs

    def _ground_goal_terms(self, query_terms: set[str]) -> set[str]:
        goal_terms = set(query_terms)
        for bucket_name in ("by_task", "by_output", "by_text_term"):
            bucket = self.artifacts.index.get(bucket_name, {})
            for key in bucket:
                key_terms = _tokenize(key)
                if query_terms & key_terms:
                    goal_terms.update(key_terms)
        return goal_terms

    def _search_plans(self, grounded: GroundedQuery) -> list[OrchestrationPlan]:
        initial_available = frozenset(artifact.key for artifact in grounded.available_artifacts)
        entry_ids = self._entry_skill_ids(initial_available, grounded.goal_terms)
        queue = deque(
            _State(skill_ids=(skill_id,), available=initial_available, edges=())
            for skill_id in entry_ids
        )
        plans: list[OrchestrationPlan] = []
        seen: set[tuple[tuple[str, ...], frozenset[tuple[str, str]]]] = set()

        while queue and len(plans) < self.max_plans * 4:
            state = queue.popleft()
            key = (state.skill_ids, state.available)
            if key in seen:
                continue
            seen.add(key)

            plan = self._state_to_plan(state, grounded)
            if plan.goal_score > 0:
                plans.append(plan)
            if len(state.skill_ids) >= self.max_depth:
                continue

            last_id = state.skill_ids[-1]
            next_ids = self._next_skill_ids(last_id, state, grounded.goal_terms)
            for edge_index, next_id in next_ids[: self.max_branch]:
                if next_id in state.skill_ids:
                    continue
                next_skill = self.skill_by_id.get(next_id)
                last_skill = self.skill_by_id.get(last_id)
                if not next_skill or not last_skill:
                    continue
                queue.append(
                    _State(
                        skill_ids=(*state.skill_ids, next_id),
                        available=(
                            state.available
                            | _output_keys(last_skill)
                            | _output_keys(next_skill)
                        ),
                        edges=(*state.edges, edge_index),
                    )
                )

        if not plans:
            return [
                self._state_to_plan(
                    _State(skill_ids=(skill_id,), available=initial_available, edges=()),
                    grounded,
                )
                for skill_id in entry_ids[: self.max_plans]
            ]
        return _compose_dag_plans(_dedupe_plans(plans), max_plans=self.max_plans)

    def _entry_skill_ids(
        self,
        available: frozenset[tuple[str, str]],
        goal_terms: set[str],
    ) -> list[str]:
        scored: list[tuple[float, str]] = []
        for skill in self.artifacts.skills:
            skill_id = skill.get("id", "")
            if not skill_id:
                continue
            input_score = _input_coverage_score(skill, available)
            goal_score = _skill_goal_score(skill, goal_terms)
            if input_score <= 0 and goal_score <= 0:
                continue
            scored.append((input_score * 4.0 + goal_score, skill_id))
        return [
            skill_id
            for _, skill_id in sorted(scored, key=lambda item: (-item[0], item[1]))
        ][: self.max_branch]

    def _next_skill_ids(
        self,
        last_id: str,
        state: _State,
        goal_terms: set[str],
    ) -> list[tuple[int, str]]:
        candidates: list[tuple[float, int, str]] = []
        for edge_index in self.outgoing_edges.get(last_id, []):
            edge = self.can_feed_edges[edge_index]
            target_id = _skill_id(edge.get("target"))
            if target_id in state.skill_ids:
                continue
            target = self.skill_by_id.get(target_id)
            if not target:
                continue
            score = (
                float(edge.get("confidence") or 0.0) * 4.0
                + _input_coverage_score(target, state.available) * 2.0
                + _skill_goal_score(target, goal_terms)
            )
            candidates.append((score, edge_index, target_id))
        return [
            (edge_index, target_id)
            for _, edge_index, target_id in sorted(
                candidates,
                key=lambda item: (-item[0], item[2]),
            )
        ]

    def _state_to_plan(self, state: _State, grounded: GroundedQuery) -> OrchestrationPlan:
        available = set(state.available)
        steps: list[PlanStep] = []
        all_missing: list[dict[str, Any]] = []
        produced: set[tuple[str, str]] = set()
        reasons: list[str] = []

        for skill_id in state.skill_ids:
            skill = self.skill_by_id[skill_id]
            missing = _missing_inputs(skill, available)
            all_missing.extend({**item, "skill_id": skill_id} for item in missing)
            outputs = _output_keys(skill)
            produced.update(outputs)
            available.update(outputs)
            steps.append(
                PlanStep(
                    skill_id=skill_id,
                    name=skill.get("name", skill_id),
                    tasks=list(skill.get("tasks", [])),
                    inputs=[
                        {"name": item.get("name"), "type": item.get("type")}
                        for item in skill.get("inputs", [])
                    ],
                    outputs=[
                        {"name": item.get("name"), "type": item.get("type")}
                        for item in skill.get("outputs", [])
                    ],
                    missing_inputs=missing,
                )
            )
            score = _skill_goal_score(skill, grounded.goal_terms)
            if score > 0:
                reasons.append(f"{skill_id} matches goal terms")

        edges = [self.can_feed_edges[index] for index in state.edges]
        edge_confidence = (
            sum(float(edge.get("confidence") or 0.0) for edge in edges) / len(edges)
            if edges
            else 1.0
        )
        goal_score = _plan_goal_score(
            state.skill_ids,
            produced,
            self.skill_by_id,
            grounded.goal_terms,
        )
        status = "ready" if not all_missing else "needs_input"
        return OrchestrationPlan(
            steps=steps,
            produced_artifacts=[
                ArtifactRef(name=name, type=type_, source="skill_output")
                for name, type_ in sorted(produced)
            ],
            missing_inputs=all_missing,
            can_feed_edges=[_edge_plan_item(edge) for edge in edges],
            goal_score=goal_score,
            edge_confidence=edge_confidence,
            consumed_user_artifacts=_consumed_user_artifact_count(
                steps,
                grounded.available_artifacts,
            ),
            status=status,
            reasons=reasons[:8],
        )

    @staticmethod
    def _build_outgoing_edges(edges: list[dict[str, Any]]) -> dict[str, list[int]]:
        outgoing: dict[str, list[int]] = defaultdict(list)
        for index, edge in enumerate(edges):
            outgoing[_skill_id(edge.get("source"))].append(index)
        return outgoing


def _artifact_phrases(artifacts: BuildArtifacts) -> list[tuple[str, str, list[str]]]:
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

    for term in _vocab_terms(artifacts.io_name_vocab):
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


def _artifact_vocab_payload(artifacts: BuildArtifacts) -> list[dict[str, Any]]:
    payload = []
    for name, type_, phrases in _artifact_phrases(artifacts):
        payload.append(
            {
                "name": name,
                "type": type_,
                "aliases_or_examples": phrases[:6],
            }
        )
    return payload[:300]


def _task_vocab_payload(artifacts: BuildArtifacts) -> list[str]:
    terms = {
        str(term.get("name"))
        for term in _vocab_terms(artifacts.task_vocab)
        if term.get("name")
    }
    terms.update(str(key) for key in artifacts.index.get("by_task", {}))
    return sorted(terms)[:200]


def _normalize_llm_grounding(
    payload: dict[str, Any],
    known_refs: dict[tuple[str, str], ArtifactRef],
) -> dict[str, Any]:
    artifacts = []
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
            artifacts.append(ref)

    goal_terms = set()
    for term in payload.get("goal_terms", []):
        goal_terms.update(_tokenize(str(term)))
    return {
        "available_artifacts": artifacts,
        "goal_terms": goal_terms,
    }


def _merge_artifacts(
    base: list[ArtifactRef],
    extra: Iterable[ArtifactRef],
) -> list[ArtifactRef]:
    merged = {artifact.key: artifact for artifact in base}
    for artifact in extra:
        merged.setdefault(artifact.key, artifact)
    return sorted(merged.values(), key=lambda item: (item.name, item.type, item.source))


def _vocab_terms(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    terms = payload.get("terms", [])
    return [term for term in terms if isinstance(term, dict)]


def _input_coverage_score(skill: dict[str, Any], available: Iterable[tuple[str, str]]) -> float:
    required = [
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("inputs", [])
        if item.get("required", True) and item.get("name")
    ]
    if not required:
        return 1.0
    available_set = set(available)
    matched = sum(1 for item in required if _artifact_matches(item, available_set))
    return matched / len(required)


def _consumed_user_artifact_count(
    steps: list[PlanStep],
    artifacts: list[ArtifactRef],
) -> int:
    explicit = {
        artifact.key
        for artifact in artifacts
        if artifact.source != "implicit_query"
    }
    if not steps or not explicit:
        return 0
    consumed = 0
    for item in steps[0].inputs:
        expected = (str(item.get("name")), str(item.get("type") or "unknown"))
        if _artifact_matches(expected, explicit):
            consumed += 1
    return consumed


def _missing_inputs(
    skill: dict[str, Any],
    available: Iterable[tuple[str, str]],
) -> list[dict[str, Any]]:
    available_set = set(available)
    missing = []
    for item in skill.get("inputs", []):
        if not item.get("required", True):
            continue
        expected = (str(item.get("name")), str(item.get("type") or "unknown"))
        if not _artifact_matches(expected, available_set):
            missing.append({"name": expected[0], "type": expected[1]})
    return missing


def _artifact_matches(
    expected: tuple[str, str],
    available: set[tuple[str, str]],
) -> bool:
    name, type_ = expected
    if expected in available:
        return True
    return any(
        candidate_name == name and (candidate_type == "unknown" or type_ == "unknown")
        for candidate_name, candidate_type in available
    )


def _output_keys(skill: dict[str, Any]) -> frozenset[tuple[str, str]]:
    return frozenset(
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("outputs", [])
        if item.get("name")
    )


def _plan_goal_score(
    skill_ids: tuple[str, ...],
    produced: set[tuple[str, str]],
    skill_by_id: dict[str, dict[str, Any]],
    goal_terms: set[str],
) -> float:
    skill_score = sum(
        _skill_goal_score(skill_by_id[skill_id], goal_terms)
        for skill_id in skill_ids
        if skill_id in skill_by_id
    )
    output_score = sum(
        len(_tokenize(name) & goal_terms) * 2.0 + len(_tokenize(type_) & goal_terms)
        for name, type_ in produced
    )
    return skill_score + output_score


def _skill_goal_score(skill: dict[str, Any], goal_terms: set[str]) -> float:
    terms = _skill_terms(skill)
    task_terms = set()
    for task in skill.get("tasks", []):
        task_terms.update(_tokenize(task))
    output_terms = set()
    for output in skill.get("outputs", []):
        output_terms.update(_tokenize(output.get("name", "")))
        output_terms.update(_tokenize(output.get("description", "")))
    return (
        len(terms & goal_terms)
        + len(task_terms & goal_terms) * 3.0
        + len(output_terms & goal_terms) * 2.0
    )


def _skill_terms(skill: dict[str, Any]) -> set[str]:
    chunks = [
        skill.get("id", ""),
        skill.get("name", ""),
        skill.get("description", ""),
    ]
    chunks.extend(skill.get("tasks", []))
    for item in skill.get("inputs", []):
        chunks.extend(
            [item.get("name", ""), item.get("type", ""), item.get("description", "")]
        )
    for item in skill.get("outputs", []):
        chunks.extend(
            [item.get("name", ""), item.get("type", ""), item.get("description", "")]
        )
    terms: set[str] = set()
    for chunk in chunks:
        terms.update(_tokenize(str(chunk)))
    return terms


def _dedupe_plans(plans: list[OrchestrationPlan]) -> list[OrchestrationPlan]:
    deduped: dict[tuple[str, ...], OrchestrationPlan] = {}
    for plan in plans:
        key = tuple(step.skill_id for step in plan.steps)
        existing = deduped.get(key)
        if existing is None or plan.goal_score > existing.goal_score:
            deduped[key] = plan
    return list(deduped.values())


def _compose_dag_plans(
    path_plans: list[OrchestrationPlan],
    *,
    max_plans: int,
) -> list[OrchestrationPlan]:
    composed: list[OrchestrationPlan] = []
    groups: dict[str, list[OrchestrationPlan]] = defaultdict(list)
    for plan in path_plans:
        if plan.steps:
            groups[plan.steps[0].skill_id].append(plan)

    for group in groups.values():
        branch_plans = [
            plan
            for plan in group
            if len(plan.steps) > 1
        ]
        if len(branch_plans) < 2:
            continue
        composed.append(_compose_plan_group(branch_plans))

    return [*composed, *path_plans][: max_plans * 2]


def _compose_plan_group(plans: list[OrchestrationPlan]) -> OrchestrationPlan:
    steps_by_id: dict[str, PlanStep] = {}
    edges_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    produced_by_key: dict[tuple[str, str], ArtifactRef] = {}
    missing_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    reasons: list[str] = []

    for plan in plans:
        for step in plan.steps:
            steps_by_id.setdefault(step.skill_id, step)
        for edge in plan.can_feed_edges:
            key = (str(edge.get("source_id") or ""), str(edge.get("target_id") or ""))
            if key[0] and key[1]:
                edges_by_key.setdefault(key, edge)
        for artifact in plan.produced_artifacts:
            produced_by_key.setdefault(artifact.key, artifact)
        for item in plan.missing_inputs:
            key = (
                str(item.get("skill_id") or ""),
                str(item.get("name") or ""),
                str(item.get("type") or "unknown"),
            )
            missing_by_key.setdefault(key, dict(item))
        for reason in plan.reasons:
            if reason not in reasons:
                reasons.append(reason)

    ordered_step_ids = _topological_step_ids(
        set(steps_by_id),
        list(edges_by_key.values()),
    )
    steps = [steps_by_id[skill_id] for skill_id in ordered_step_ids]
    missing_inputs = list(missing_by_key.values())
    edge_confidence = (
        sum(float(edge.get("confidence") or 0.0) for edge in edges_by_key.values())
        / len(edges_by_key)
        if edges_by_key
        else 1.0
    )
    return OrchestrationPlan(
        steps=steps,
        produced_artifacts=list(produced_by_key.values()),
        missing_inputs=missing_inputs,
        can_feed_edges=list(edges_by_key.values()),
        goal_score=sum(plan.goal_score for plan in plans),
        edge_confidence=edge_confidence,
        consumed_user_artifacts=max(plan.consumed_user_artifacts for plan in plans),
        status="ready" if not missing_inputs else "needs_input",
        reasons=reasons[:8],
    )


def _topological_step_ids(
    skill_ids: set[str],
    edges: list[dict[str, Any]],
) -> list[str]:
    incoming = {skill_id: 0 for skill_id in skill_ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if source_id not in skill_ids or target_id not in skill_ids:
            continue
        outgoing[source_id].append(target_id)
        incoming[target_id] += 1

    queue = deque(sorted(skill_id for skill_id, count in incoming.items() if count == 0))
    ordered: list[str] = []
    while queue:
        skill_id = queue.popleft()
        ordered.append(skill_id)
        for target_id in sorted(outgoing.get(skill_id, [])):
            incoming[target_id] -= 1
            if incoming[target_id] == 0:
                queue.append(target_id)
    ordered.extend(sorted(skill_ids - set(ordered)))
    return ordered


def _plan_stages(
    steps: list[PlanStep],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    step_by_id = {step.skill_id: step for step in steps}
    remaining = set(step_by_id)
    incoming: dict[str, set[str]] = {skill_id: set() for skill_id in remaining}
    for edge in edges:
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if source_id in remaining and target_id in remaining:
            incoming[target_id].add(source_id)

    stages = []
    completed: set[str] = set()
    while remaining:
        ready = sorted(
            skill_id
            for skill_id in remaining
            if incoming[skill_id] <= completed
        )
        if not ready:
            ready = sorted(remaining)
        stages.append(
            {
                "stage": len(stages) + 1,
                "skills": [step_by_id[skill_id].to_dict() for skill_id in ready],
            }
        )
        completed.update(ready)
        remaining.difference_update(ready)
    return stages


def _edge_plan_item(edge: dict[str, Any]) -> dict[str, Any]:
    evidence = edge.get("evidence") or {}
    supporting_fields = evidence.get("supporting_fields") or {}
    return {
        "source_id": _skill_id(edge.get("source")),
        "target_id": _skill_id(edge.get("target")),
        "confidence": edge.get("confidence"),
        "method": edge.get("method"),
        "source_outputs": supporting_fields.get("source_outputs", [])[:3],
        "target_inputs": supporting_fields.get("target_inputs", [])[:3],
        "reasons": evidence.get("reasons", [])[:3],
    }


def _tokenize(
    text: str,
    *,
    stop_terms: set[str] = DEFAULT_STOP_TERMS,
) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(text).lower())
        if len(token) >= 2 and token not in stop_terms
    }


def _skill_id(node_id: Any) -> str:
    text = str(node_id or "")
    return text.removeprefix("skill:")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
