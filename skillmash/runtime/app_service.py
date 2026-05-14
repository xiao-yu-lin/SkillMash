"""Application service boundary shared by CLI, HTTP API, and browser UI."""

from __future__ import annotations

from typing import Any

from skillmash.core.decomposer import AtomicDecomposer
from skillmash.core.graph import CapabilityGraph
from skillmash.core.matcher import CompositionMatcher
from skillmash.core.models import SkillDefinition
from skillmash.core.planner import SkillPlanner
from skillmash.core.registry import SkillRegistry
from skillmash.core.scoring import PlanScorer
from skillmash.core.serialization import skill_to_dict
from skillmash.runtime.online import BuildArtifactLoader, LoadedBuildArtifact, SkillRetriever
from skillmash.samples.examples import build_sample_registry


class SkillMashService:
    """Facade that hides whether data came from samples or offline artifacts."""

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        index_dir: str | None = None,
    ) -> None:
        self.loaded_artifact: LoadedBuildArtifact | None = None
        if index_dir:
            # Production-style path: load the offline build once, then serve
            # retrieval, graph, and planning requests from memory.
            self.loaded_artifact = BuildArtifactLoader(index_dir).load()
            self.registry = self.loaded_artifact.registry
            self.graph = self.loaded_artifact.graph
            self.retriever = SkillRetriever(self.registry, self.loaded_artifact.skill_index)
        else:
            # Developer/demo path: keep the UI usable before a real Skill folder
            # collection has been built.
            self.registry = registry or build_sample_registry()
            self.graph = CapabilityGraph(self.registry)
            self.retriever = None
        self.decomposer = AtomicDecomposer(self.registry, self.graph)
        self.matcher = CompositionMatcher(self.registry)
        self.scorer = PlanScorer(self.registry)
        self.planner = SkillPlanner(
            self.registry,
            self.graph,
            self.matcher,
            self.decomposer,
            self.scorer,
        )

    def list_skills(self) -> list[dict[str, Any]]:
        return [self._skill_to_dict(skill) for skill in self.registry.all()]

    def get_skill(self, skill_id: str) -> dict[str, Any]:
        return self._skill_to_dict(self.registry.get(skill_id))

    def decompose(self, skill_id: str) -> dict[str, Any]:
        return {
            "skill_id": skill_id,
            "atomic_skills": self.decomposer.atomic_skills(skill_id),
            "tree": self.decomposer.tree(skill_id),
        }

    def match(self, source_skill_id: str, target_skill_id: str) -> dict[str, Any]:
        result = self.matcher.match(source_skill_id, target_skill_id)
        return {
            "source_skill_id": result.source_skill_id,
            "target_skill_id": result.target_skill_id,
            "composable": result.composable,
            "operator": result.operator.value if result.operator else None,
            "compatibility": result.compatibility,
            "score": result.score,
            "input_mapping": result.input_mapping,
            "notes": list(result.notes),
        }

    def plan(self, task: str) -> dict[str, Any]:
        goal = self.planner.infer_goal(task)
        retrieved_skill_ids = (
            self.retriever.retrieve(goal) if self.retriever is not None else []
        )
        plans = self.planner.plan(goal)
        return {
            "goal": {
                "task": goal.task,
                "required_outputs": sorted(goal.required_outputs),
                "required_capabilities": sorted(goal.required_capabilities),
                "known_artifacts": sorted(goal.known_artifacts),
                "constraints": goal.constraints,
            },
            "retrieved_skill_ids": retrieved_skill_ids,
            "plans": [plan.to_dict() for plan in plans],
        }

    def build_summary(self) -> dict[str, Any]:
        if self.loaded_artifact is None:
            return {
                "mode": "sample",
                "message": "No offline build artifact loaded; using sample skills.",
                "skill_count": len(self.registry.all()),
                "edge_count": len(self.graph.edges),
            }
        index = self.loaded_artifact.skill_index
        return {
            "mode": "offline_artifact",
            "index_dir": str(self.loaded_artifact.index_dir),
            "manifest": self.loaded_artifact.manifest,
            "diagnostics": self.loaded_artifact.diagnostics,
            "index_stats": {
                group: sum(len(values) for values in mapping.values())
                for group, mapping in index.items()
            },
        }

    def graph_summary(self) -> dict[str, Any]:
        return {
            "nodes": {
                "skills": len(self.registry.all()),
                "artifacts": len(
                    {
                        edge.target
                        for edge in self.graph.edges
                        if edge.target.startswith("artifact:")
                    }
                    | {
                        edge.source
                        for edge in self.graph.edges
                        if edge.source.startswith("artifact:")
                    }
                ),
            },
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.type,
                    "metadata": edge.metadata,
                }
                for edge in self.graph.edges
            ],
        }

    @staticmethod
    def _skill_to_dict(skill: SkillDefinition) -> dict[str, Any]:
        data = skill_to_dict(skill)
        data["capability_tags"] = data["skill_tags"]
        return data
