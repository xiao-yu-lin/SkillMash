"""Orchestration facade built on planning helpers."""

from __future__ import annotations

from typing import Any

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.orchestration.planning.grounding import ground_query
from skillmash.orchestration.planning.models import GroundedQuery, GroundingClient
from skillmash.orchestration.planning.search import build_outgoing_edges, search_plans
from skillmash.orchestration.planning.utils import clamp
from skillmash.representation.llm import LLMConfig, create_llm_client


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
        self.min_edge_confidence = clamp(min_edge_confidence)
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
        self.outgoing_edges = build_outgoing_edges(self.can_feed_edges)

    def plan(self, query: str) -> dict[str, Any]:
        grounded = self.ground_query(query)
        plans = search_plans(
            artifacts=self.artifacts,
            skill_by_id=self.skill_by_id,
            can_feed_edges=self.can_feed_edges,
            outgoing_edges=self.outgoing_edges,
            grounded=grounded,
            max_depth=self.max_depth,
            max_plans=self.max_plans,
            max_branch=self.max_branch,
        )
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
        return ground_query(
            query=query,
            artifacts=self.artifacts,
            llm_client=self.llm_client,
        )
