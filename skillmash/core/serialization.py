"""Serialization boundary for SkillMash build artifacts.

The dataclasses are convenient inside the planner, while offline artifacts need
plain JSON that stays stable across releases. This module is the only place that
should know how the internal model maps to the external artifact schema.
"""

from __future__ import annotations

from typing import Any

from skillmash.core.models import (
    ArtifactSpec,
    Composition,
    CompositionOperator,
    Condition,
    ParameterSpec,
    SkillDefinition,
    SkillKind,
)


def skill_to_dict(skill: SkillDefinition) -> dict[str, Any]:
    """Serialize a SkillDefinition into the v1 artifact JSON shape."""

    return {
        "id": skill.id,
        "name": skill.name,
        "kind": skill.kind.value,
        "description": skill.description,
        "version": skill.version,
        "inputs": [param.__dict__ for param in skill.inputs],
        "outputs": [output.__dict__ for output in skill.outputs],
        "preconditions": [condition.__dict__ for condition in skill.preconditions],
        "postconditions": [condition.__dict__ for condition in skill.postconditions],
        "skill_tags": sorted(skill.capability_tags),
        "data_tags": sorted(skill.data_tags),
        "contains": skill.contains,
        "composition": composition_to_dict(skill.composition),
        "cost": skill.cost,
        "quality": skill.quality,
        "source": skill.source,
        "metadata": skill.metadata,
    }


def skill_from_dict(data: dict[str, Any]) -> SkillDefinition:
    """Deserialize a SkillDefinition from v1 artifact JSON."""

    return SkillDefinition(
        id=data["id"],
        name=data.get("name", data["id"]),
        kind=SkillKind(data.get("kind", SkillKind.WRAPPED.value)),
        description=data.get("description", ""),
        version=str(data.get("version", "1.0.0")),
        inputs=[ParameterSpec(**item) for item in data.get("inputs", [])],
        outputs=[ArtifactSpec(**item) for item in data.get("outputs", [])],
        preconditions=[Condition(**item) for item in data.get("preconditions", [])],
        postconditions=[Condition(**item) for item in data.get("postconditions", [])],
        capability_tags=set(data.get("skill_tags", data.get("capability_tags", []))),
        data_tags=set(data.get("data_tags", [])),
        contains=list(data.get("contains", [])),
        composition=composition_from_dict(data.get("composition")),
        cost=dict(data.get("cost", {})),
        quality=dict(data.get("quality", {})),
        source=dict(data.get("source", {})),
        metadata=dict(data.get("metadata", {})),
    )


def composition_to_dict(composition: Composition | None) -> dict[str, Any] | None:
    """Serialize optional composition metadata."""

    if composition is None:
        return None
    return {
        "operator": composition.operator.value,
        "steps": list(composition.steps),
    }


def composition_from_dict(data: dict[str, Any] | None) -> Composition | None:
    """Deserialize optional composition metadata."""

    if not data:
        return None
    return Composition(
        operator=CompositionOperator(data.get("operator", "sequential")),
        steps=tuple(data.get("steps", [])),
    )
