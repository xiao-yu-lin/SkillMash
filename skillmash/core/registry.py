from __future__ import annotations

from collections.abc import Iterable

from skillmash.core.models import SkillDefinition, SkillKind


class SkillRegistry:
    """Stores skill definitions and provides simple retrieval indexes."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        self._validate(skill)
        self._skills[skill.id] = skill

    def register_many(self, skills: Iterable[SkillDefinition]) -> None:
        for skill in skills:
            self.register(skill)
        self._validate_contains_references()

    def get(self, skill_id: str) -> SkillDefinition:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {skill_id}") from exc

    def all(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def has(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def find_by_output(self, output_type: str) -> list[SkillDefinition]:
        return [
            skill
            for skill in self._skills.values()
            if output_type in skill.output_types()
        ]

    def find_by_input(self, input_type: str) -> list[SkillDefinition]:
        return [
            skill
            for skill in self._skills.values()
            if input_type in skill.input_types()
        ]

    def find_by_capability(self, capability: str) -> list[SkillDefinition]:
        return [
            skill
            for skill in self._skills.values()
            if capability in skill.capability_tags
        ]

    def find_by_text(self, query: str) -> list[SkillDefinition]:
        terms = {term.lower() for term in query.split() if term.strip()}
        if not terms:
            return self.all()

        scored: list[tuple[int, SkillDefinition]] = []
        for skill in self._skills.values():
            haystack = " ".join(
                [
                    skill.id,
                    skill.name,
                    skill.description,
                    " ".join(skill.capability_tags),
                    " ".join(skill.data_tags),
                ]
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, skill))

        return [skill for _, skill in sorted(scored, key=lambda item: -item[0])]

    def _validate(self, skill: SkillDefinition) -> None:
        if not skill.id:
            raise ValueError("Skill id is required")
        if skill.id in self._skills:
            raise ValueError(f"Duplicate skill id: {skill.id}")
        if not isinstance(skill.kind, SkillKind):
            raise ValueError(f"Invalid skill kind for {skill.id}")
        if not skill.inputs and skill.kind == SkillKind.ATOMIC:
            raise ValueError(f"Atomic skill {skill.id} must define inputs")
        if not skill.outputs:
            raise ValueError(f"Skill {skill.id} must define outputs")
        if skill.kind == SkillKind.COMPOSITE:
            if not skill.contains and not skill.composition:
                raise ValueError(
                    f"{skill.kind.value} skill {skill.id} must contain child skills"
                )

    def _validate_contains_references(self) -> None:
        missing: list[str] = []
        for skill in self._skills.values():
            for child_id in skill.contains:
                if child_id not in self._skills:
                    missing.append(f"{skill.id} -> {child_id}")
        if missing:
            raise ValueError("Unknown contained skills: " + ", ".join(missing))
