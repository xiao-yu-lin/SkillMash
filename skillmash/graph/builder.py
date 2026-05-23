"""Build typed Skill relation graph nodes and edges."""

from __future__ import annotations

from typing import Dict, Iterable, List

from skillmash.graph.models import (
    GraphEdge,
    GraphNode,
    LLMMatch,
    SkillGraph,
    SkillRegistry,
)


class SkillGraphBuilder:
    """Build a Skill-only relation graph from accepted LLM matches."""

    def build(
        self,
        registry: SkillRegistry,
        llm_matches: Iterable[LLMMatch],
    ) -> SkillGraph:
        nodes: Dict[str, GraphNode] = {}
        edges: Dict[str, GraphEdge] = {}

        for skill in registry.ordered_skills():
            self._add_node(
                nodes,
                GraphNode(
                    id=f"skill:{skill.id}",
                    type="skill",
                    label=skill.name or skill.id,
                    properties={
                        "skill_id": skill.id,
                        "name": skill.name,
                        "description": skill.description,
                        "version": skill.version,
                        "tasks": list(skill.tasks),
                        "inputs": [item.to_dict() for item in skill.inputs],
                        "outputs": [item.to_dict() for item in skill.outputs],
                    },
                ),
            )

        for match in llm_matches:
            if not match.accepted:
                continue
            edge = GraphEdge(
                source=f"skill:{match.source_id}",
                target=f"skill:{match.target_id}",
                type=match.relation_type,
                confidence=match.confidence,
                method=match.method,
                evidence={
                    "candidate_id": match.candidate_id,
                    "reasons": match.reasons,
                    "supporting_fields": match.supporting_fields,
                },
            )
            self._add_edge(edges, edge)
            if match.relation_type == "similar_to":
                self._add_edge(
                    edges,
                    GraphEdge(
                        source=f"skill:{match.target_id}",
                        target=f"skill:{match.source_id}",
                        type=match.relation_type,
                        confidence=match.confidence,
                        method=match.method,
                        evidence=edge.evidence,
                    ),
                )

        return SkillGraph(
            nodes=[nodes[node_id] for node_id in sorted(nodes)],
            edges=sorted(edges.values(), key=lambda edge: edge.key),
        )

    def _add_node(self, nodes: Dict[str, GraphNode], node: GraphNode) -> None:
        nodes.setdefault(node.id, node)

    def _add_edge(self, edges: Dict[str, GraphEdge], edge: GraphEdge) -> None:
        edges.setdefault(edge.key, edge)
