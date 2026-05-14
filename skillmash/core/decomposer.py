from __future__ import annotations

from skillmash.core.graph import CapabilityGraph
from skillmash.core.models import SkillKind
from skillmash.core.registry import SkillRegistry


class AtomicDecomposer:
    def __init__(self, registry: SkillRegistry, graph: CapabilityGraph) -> None:
        self.registry = registry
        self.graph = graph

    def atomic_skills(self, skill_id: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []

        def walk(current_id: str, path: set[str]) -> None:
            if current_id in path:
                raise ValueError(f"Contains cycle detected at {current_id}")
            skill = self.registry.get(current_id)
            children = self.graph.get_children(current_id)
            if skill.kind == SkillKind.ATOMIC or not children:
                if current_id not in seen:
                    seen.add(current_id)
                    result.append(current_id)
                return
            for child_id in children:
                walk(child_id, path | {current_id})

        walk(skill_id, set())
        return result

    def tree(self, skill_id: str) -> dict:
        skill = self.registry.get(skill_id)
        children = self.graph.get_children(skill_id)
        return {
            "id": skill.id,
            "name": skill.name,
            "kind": skill.kind.value,
            "children": [self.tree(child_id) for child_id in children],
        }
