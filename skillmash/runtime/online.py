"""Online loading and retrieval for SkillMash planning.

The online side consumes the offline build artifacts instead of walking Skill
folders directly. That keeps request handling fast and makes the planner depend
on a versioned artifact contract rather than filesystem layout details.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillmash.core.graph import CapabilityGraph
from skillmash.core.planner import Goal
from skillmash.core.registry import SkillRegistry
from skillmash.core.serialization import skill_from_dict


@dataclass
class LoadedBuildArtifact:
    """In-memory representation of one loaded offline build."""

    index_dir: Path
    manifest: dict[str, Any]
    registry: SkillRegistry
    graph: CapabilityGraph
    skill_index: dict[str, dict[str, list[str]]]
    diagnostics: dict[str, Any]


class BuildArtifactLoader:
    """Load and validate v1 build artifacts from an index directory."""

    def __init__(self, index_dir: str | Path) -> None:
        self.index_dir = Path(index_dir)

    def load(self) -> LoadedBuildArtifact:
        manifest_path = self.index_dir / "build_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Missing build manifest: {manifest_path}")
        manifest = read_json(manifest_path)
        if str(manifest.get("version")) != "1":
            raise ValueError(f"Unsupported build artifact version: {manifest.get('version')}")

        skills_data = read_json(self.index_dir / manifest["skills"])
        registry = SkillRegistry()
        registry.register_many([skill_from_dict(item) for item in skills_data.get("skills", [])])
        graph = CapabilityGraph(registry)
        skill_index = read_json(self.index_dir / manifest["indexes"])
        diagnostics = read_json(self.index_dir / manifest["diagnostics"])

        return LoadedBuildArtifact(
            index_dir=self.index_dir,
            manifest=manifest,
            registry=registry,
            graph=graph,
            skill_index=skill_index,
            diagnostics=diagnostics,
        )


class SkillRetriever:
    """Rank candidate Skills for a goal using the offline inverted indexes."""

    def __init__(self, registry: SkillRegistry, skill_index: dict[str, dict[str, list[str]]]) -> None:
        self.registry = registry
        self.skill_index = skill_index

    def retrieve(self, goal: Goal, limit: int = 20) -> list[str]:
        scores: dict[str, float] = {}

        # Retrieval is a cheap first pass before planning. Output matches matter
        # most, skill tags capture intent, known artifacts capture available
        # inputs, and text terms provide a weak fallback for long-tail Skills.
        for output in goal.required_outputs:
            self._add_scores(scores, self._lookup("by_output", output), 3.0)
        for tag in goal.required_capabilities:
            self._add_scores(scores, self._lookup("by_skill_tag", tag), 2.0)
        for artifact in goal.known_artifacts:
            self._add_scores(scores, self._lookup("by_input", artifact), 1.0)
        for term in goal.task.lower().split():
            self._add_scores(scores, self._lookup("by_text_term", term), 0.5)

        return [
            skill_id
            for skill_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    def retrieve_skills(self, goal: Goal, limit: int = 20):
        return [self.registry.get(skill_id) for skill_id in self.retrieve(goal, limit)]

    def _lookup(self, group: str, key: str) -> list[str]:
        return list(self.skill_index.get(group, {}).get(key, []))

    @staticmethod
    def _add_scores(scores: dict[str, float], skill_ids: list[str], weight: float) -> None:
        for skill_id in skill_ids:
            scores[skill_id] = scores.get(skill_id, 0.0) + weight


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
