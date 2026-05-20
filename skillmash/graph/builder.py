"""Build typed Skill graph nodes and edges."""

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
    """Build graph nodes and edges from registry and accepted LLM matches."""

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
                        "description": skill.description,
                        "version": skill.version,
                    },
                ),
            )
            for output in skill.outputs:
                artifact_id = f"artifact:{output.name}"
                self._add_node(
                    nodes,
                    GraphNode(
                        id=artifact_id,
                        type="artifact",
                        label=output.name,
                        properties={"name": output.name, "data_type": output.type},
                    ),
                )
                self._add_edge(
                    edges,
                    GraphEdge(
                        source=f"skill:{skill.id}",
                        target=artifact_id,
                        type="produces",
                        evidence={"output": output.to_dict()},
                    ),
                )
                self._add_data_type(nodes, edges, f"skill:{skill.id}", output.type)

            for parameter in skill.inputs:
                artifact_id = f"artifact:{parameter.name}"
                self._add_node(
                    nodes,
                    GraphNode(
                        id=artifact_id,
                        type="artifact",
                        label=parameter.name,
                        properties={
                            "name": parameter.name,
                            "data_type": parameter.type,
                        },
                    ),
                )
                self._add_edge(
                    edges,
                    GraphEdge(
                        source=artifact_id,
                        target=f"skill:{skill.id}",
                        type="consumes",
                        evidence={"input": parameter.to_dict()},
                    ),
                )
                self._add_data_type(nodes, edges, f"skill:{skill.id}", parameter.type)

            for task in skill.tasks:
                task_id = f"task:{task}"
                self._add_node(
                    nodes,
                    GraphNode(
                        id=task_id,
                        type="task",
                        label=task,
                        properties={"task": task},
                    ),
                )
                self._add_edge(
                    edges,
                    GraphEdge(
                        source=f"skill:{skill.id}",
                        target=task_id,
                        type="has_task",
                        evidence={"task": task},
                    ),
                )

        for match in llm_matches:
            if not match.accepted:
                continue
            self._add_edge(
                edges,
                GraphEdge(
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
                ),
            )

        return SkillGraph(
            nodes=[nodes[node_id] for node_id in sorted(nodes)],
            edges=sorted(edges.values(), key=lambda edge: edge.key),
        )

    def _add_data_type(
        self,
        nodes: Dict[str, GraphNode],
        edges: Dict[str, GraphEdge],
        skill_node_id: str,
        data_type: str,
    ) -> None:
        type_id = f"type:{data_type}"
        self._add_node(
            nodes,
            GraphNode(
                id=type_id,
                type="data_type",
                label=data_type,
                properties={"data_type": data_type},
            ),
        )
        self._add_edge(
            edges,
            GraphEdge(
                source=skill_node_id,
                target=type_id,
                type="uses_data_type",
                evidence={"data_type": data_type},
            ),
        )

    def _add_node(self, nodes: Dict[str, GraphNode], node: GraphNode) -> None:
        nodes.setdefault(node.id, node)

    def _add_edge(self, edges: Dict[str, GraphEdge], edge: GraphEdge) -> None:
        edges.setdefault(edge.key, edge)
