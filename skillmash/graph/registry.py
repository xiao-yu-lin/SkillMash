"""Skill registry validation for graph construction."""

from __future__ import annotations

from typing import Iterable, List

from skillmash.graph.models import GraphDiagnostic, SkillRegistry
from skillmash.representation.models import SkillRepresentation


class SkillRegistryBuilder:
    """Register normalized Skill representations by stable ID."""

    def register(self, representations: Iterable[SkillRepresentation]) -> SkillRegistry:
        skills = {}
        diagnostics: List[GraphDiagnostic] = []

        for representation in sorted(representations, key=lambda item: item.id):
            if not representation.id:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="registry",
                        severity="error",
                        code="missing_skill_id",
                        message="Skill representation is missing an id.",
                        details={"skill": representation.to_dict()},
                    )
                )
                continue

            if representation.id in skills:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="registry",
                        severity="error",
                        code="duplicate_skill_id",
                        message=f"Duplicate Skill id: {representation.id}",
                        skill_id=representation.id,
                    )
                )
                continue

            if not representation.outputs:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="registry",
                        severity="warning",
                        code="missing_outputs",
                        message="Skill has no declared outputs.",
                        skill_id=representation.id,
                    )
                )

            skills[representation.id] = representation

        return SkillRegistry(skills=skills, diagnostics=diagnostics)
