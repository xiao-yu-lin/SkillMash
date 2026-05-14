from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from skillmash.core.models import SkillDefinition
from skillmash.core.registry import SkillRegistry


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    type: str
    metadata: dict[str, Any] = field(default_factory=dict)


class CapabilityGraph:
    """Typed graph over skills and artifacts."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry
        self.edges: list[Edge] = []
        self._out: dict[str, list[Edge]] = defaultdict(list)
        self._in: dict[str, list[Edge]] = defaultdict(list)
        self.rebuild()

    def rebuild(self) -> None:
        self.edges.clear()
        self._out.clear()
        self._in.clear()
        for skill in self.registry.all():
            self._add_skill_edges(skill)
        self.assert_no_contains_cycles()

    def add_edge(
        self, source: str, target: str, edge_type: str, metadata: dict[str, Any] | None = None
    ) -> None:
        edge = Edge(source=source, target=target, type=edge_type, metadata=metadata or {})
        self.edges.append(edge)
        self._out[source].append(edge)
        self._in[target].append(edge)

    def get_children(self, skill_id: str) -> list[str]:
        return [edge.target for edge in self._out[skill_id] if edge.type == "contains"]

    def get_parents(self, skill_id: str) -> list[str]:
        return [edge.source for edge in self._in[skill_id] if edge.type == "contains"]

    def get_producers(self, artifact_type: str) -> list[str]:
        artifact_id = self._artifact_id(artifact_type)
        return [edge.source for edge in self._in[artifact_id] if edge.type == "produces"]

    def get_consumers(self, artifact_type: str) -> list[str]:
        artifact_id = self._artifact_id(artifact_type)
        return [edge.target for edge in self._out[artifact_id] if edge.type == "consumes"]

    def get_output_artifacts(self, skill_id: str) -> list[str]:
        return [
            edge.target.removeprefix("artifact:")
            for edge in self._out[skill_id]
            if edge.type == "produces"
        ]

    def get_input_artifacts(self, skill_id: str) -> list[str]:
        return [
            edge.source.removeprefix("artifact:")
            for edge in self._in[skill_id]
            if edge.type == "consumes"
        ]

    def assert_no_contains_cycles(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(skill_id: str, path: list[str]) -> None:
            if skill_id in visiting:
                cycle = " -> ".join(path + [skill_id])
                raise ValueError(f"Contains cycle detected: {cycle}")
            if skill_id in visited:
                return
            visiting.add(skill_id)
            for child_id in self.get_children(skill_id):
                visit(child_id, path + [skill_id])
            visiting.remove(skill_id)
            visited.add(skill_id)

        for skill in self.registry.all():
            visit(skill.id, [])

    def _add_skill_edges(self, skill: SkillDefinition) -> None:
        for child_id in skill.contains:
            self.add_edge(skill.id, child_id, "contains")
        for input_spec in skill.inputs:
            self.add_edge(
                self._artifact_id(input_spec.type),
                skill.id,
                "consumes",
                {"name": input_spec.name, "required": input_spec.required},
            )
        for output_spec in skill.outputs:
            self.add_edge(
                skill.id,
                self._artifact_id(output_spec.type),
                "produces",
                {"name": output_spec.name},
            )

    @staticmethod
    def _artifact_id(artifact_type: str) -> str:
        return f"artifact:{artifact_type}"
