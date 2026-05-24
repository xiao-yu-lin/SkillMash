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
            self._add_artifact_nodes(nodes, edges, skill)
            self._add_slot_nodes(nodes, edges, skill)
            self._add_depends_on_edges(edges, skill, registry)

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

    def _add_artifact_nodes(
        self,
        nodes: Dict[str, GraphNode],
        edges: Dict[str, GraphEdge],
        skill,
    ) -> None:
        skill_node_id = f"skill:{skill.id}"
        for output in skill.outputs:
            artifact_id = _artifact_node_id(output.name, output.type)
            self._add_node(
                nodes,
                GraphNode(
                    id=artifact_id,
                    type="artifact",
                    label=output.name,
                    properties={"name": output.name, "type": output.type},
                ),
            )
            self._add_edge(
                edges,
                GraphEdge(
                    source=skill_node_id,
                    target=artifact_id,
                    type="produces",
                    method="deterministic_artifact_binding",
                ),
            )
        for parameter in skill.inputs:
            artifact_id = _artifact_node_id(parameter.name, parameter.type)
            self._add_node(
                nodes,
                GraphNode(
                    id=artifact_id,
                    type="artifact",
                    label=parameter.name,
                    properties={"name": parameter.name, "type": parameter.type},
                ),
            )
            self._add_edge(
                edges,
                GraphEdge(
                    source=artifact_id,
                    target=skill_node_id,
                    type="consumes",
                    method="deterministic_artifact_binding",
                ),
            )

    def _add_slot_nodes(
        self,
        nodes: Dict[str, GraphNode],
        edges: Dict[str, GraphEdge],
        skill,
    ) -> None:
        skill_node_id = f"skill:{skill.id}"
        emits_slots = list(getattr(skill, "emit_slot_link_keys", lambda: [])())
        consumes_slots = list(getattr(skill, "consume_slot_link_keys", lambda: [])())
        consume_ref_count = len(getattr(skill, "consumes_slots", []) or [])

        for slot_name in emits_slots:
            slot_id = _slot_node_id(slot_name)
            self._add_node(
                nodes,
                GraphNode(
                    id=slot_id,
                    type="slot",
                    label=slot_name,
                    properties={"name": slot_name},
                ),
            )
            self._add_edge(
                edges,
                GraphEdge(
                    source=skill_node_id,
                    target=slot_id,
                    type="produces",
                    method="deterministic_slot_binding",
                ),
            )

        consume_edge_type = "aggregates" if consume_ref_count > 1 else "consumes"
        for slot_name in consumes_slots:
            slot_id = _slot_node_id(slot_name)
            self._add_node(
                nodes,
                GraphNode(
                    id=slot_id,
                    type="slot",
                    label=slot_name,
                    properties={"name": slot_name},
                ),
            )
            self._add_edge(
                edges,
                GraphEdge(
                    source=slot_id,
                    target=skill_node_id,
                    type=consume_edge_type,
                    method="deterministic_slot_binding",
                ),
            )

    def _add_depends_on_edges(
        self,
        edges: Dict[str, GraphEdge],
        skill,
        registry: SkillRegistry,
    ) -> None:
        known_skill_ids = set(registry.skills)
        for condition in skill.preconditions:
            if condition.type != "depends_on_skill":
                continue
            dependency_id = str(condition.expression or "").strip()
            if not dependency_id or dependency_id not in known_skill_ids:
                continue
            self._add_edge(
                edges,
                GraphEdge(
                    source=f"skill:{dependency_id}",
                    target=f"skill:{skill.id}",
                    type="depends_on",
                    method="deterministic_precondition_dependency",
                ),
            )


def _artifact_node_id(name: str, type_: str) -> str:
    return f"artifact:{name}:{type_ or 'unknown'}"


def _slot_node_id(name: str) -> str:
    return f"slot:{name}"
