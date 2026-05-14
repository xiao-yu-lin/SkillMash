from __future__ import annotations

from skillmash.core.models import CompositionOperator, MatchResult, SkillDefinition
from skillmash.core.registry import SkillRegistry


class CompositionMatcher:
    """Determines whether two skills can be composed."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def match(self, source_id: str, target_id: str) -> MatchResult:
        source = self.registry.get(source_id)
        target = self.registry.get(target_id)
        mapping = self._build_mapping(source, target)
        missing_required = [
            param.name
            for param in target.inputs
            if param.required and param.name not in mapping
        ]

        tag_overlap = bool(source.data_tags & target.data_tags)
        capability_repeated = bool(source.capability_tags & target.capability_tags)

        if not missing_required:
            compatibility = "exact_match"
            score = 1.0
            notes = ("all required inputs can be mapped from source outputs",)
            composable = True
        elif mapping and tag_overlap:
            compatibility = "transform_match"
            score = 0.65
            notes = ("some inputs require lightweight transformation or user context",)
            composable = True
        else:
            compatibility = "no_match"
            score = 0.0
            notes = ("required inputs cannot be satisfied by source outputs",)
            composable = False

        if composable and capability_repeated and not tag_overlap:
            score -= 0.2
            notes = notes + ("capability tags overlap but data flow is weak",)

        return MatchResult(
            source_skill_id=source_id,
            target_skill_id=target_id,
            composable=composable,
            operator=CompositionOperator.SEQUENTIAL if composable else None,
            compatibility=compatibility,
            score=max(score, 0.0),
            input_mapping=mapping,
            notes=notes,
        )

    def _build_mapping(
        self, source: SkillDefinition, target: SkillDefinition
    ) -> dict[str, str]:
        source_outputs = source.outputs
        mapping: dict[str, str] = {}

        for param in target.inputs:
            exact = next(
                (output for output in source_outputs if output.type == param.type),
                None,
            )
            if exact:
                mapping[param.name] = f"{source.id}.{exact.name}"
                continue

            if param.type in source.data_tags:
                first_output = source_outputs[0] if source_outputs else None
                if first_output:
                    mapping[param.name] = f"{source.id}.{first_output.name}"

        return mapping
