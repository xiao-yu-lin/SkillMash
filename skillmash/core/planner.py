"""Goal inference and simple graph-backed planning."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from skillmash.core.decomposer import AtomicDecomposer
from skillmash.core.graph import CapabilityGraph
from skillmash.core.matcher import CompositionMatcher
from skillmash.core.models import CompositionOperator, ExecutionPlan, PlanStep, SkillKind
from skillmash.core.registry import SkillRegistry
from skillmash.core.scoring import PlanScorer


@dataclass(frozen=True)
class Goal:
    """Planner-friendly interpretation of a user task."""

    task: str
    required_outputs: set[str]
    required_capabilities: set[str] = field(default_factory=set)
    known_artifacts: set[str] = field(default_factory=set)
    constraints: dict[str, bool | str | int | float] = field(default_factory=dict)


class SkillPlanner:
    """Generate candidate sequential plans from the Skill graph."""

    def __init__(
        self,
        registry: SkillRegistry,
        graph: CapabilityGraph,
        matcher: CompositionMatcher,
        decomposer: AtomicDecomposer,
        scorer: PlanScorer,
    ) -> None:
        self.registry = registry
        self.graph = graph
        self.matcher = matcher
        self.decomposer = decomposer
        self.scorer = scorer

    def plan(self, goal: Goal, max_depth: int = 8, max_plans: int = 20) -> list[ExecutionPlan]:
        candidates: list[ExecutionPlan] = []

        for output in sorted(goal.required_outputs):
            for producer_id in self.graph.get_producers(output):
                candidates.extend(
                    self._build_plans_for_skill(
                        goal=goal,
                        target_skill_id=producer_id,
                        max_depth=max_depth,
                    )
                )

        candidates = self._deduplicate(candidates)
        for index, plan in enumerate(candidates, start=1):
            plan.id = f"plan_{index:03d}"
            self.scorer.score(plan, goal.required_capabilities, goal.constraints)

        return sorted(candidates, key=lambda plan: plan.score, reverse=True)[:max_plans]

    def infer_goal(self, task: str) -> Goal:
        """Infer a first-pass goal from user text.

        V1 uses keyword rules so the planner is deterministic and easy to
        inspect. This is the main replacement point for a future LLM-based task
        parser.
        """

        lowered = task.lower()
        required_outputs: set[str] = set()
        capabilities: set[str] = set()
        constraints: dict[str, bool | str] = {}

        if "ppt" in lowered or "幻灯片" in task or "演示文稿" in task:
            required_outputs.add("pptx")
            capabilities.add("slide_generation")
        if "搜索" in task or "联网" in task or "search" in lowered or "调研" in task:
            capabilities.add("web_search")
            constraints["fresh_information"] = True
        if "总结" in task or "摘要" in task or "调研" in task:
            capabilities.add("summarization")
        if "报告" in task or "report" in lowered:
            required_outputs.add("report")
            capabilities.add("report_generation")

        if not required_outputs:
            required_outputs.add("answer")

        return Goal(
            task=task,
            required_outputs=required_outputs,
            required_capabilities=capabilities,
            known_artifacts={"topic"},
            constraints=constraints,
        )

    def _build_plans_for_skill(
        self, goal: Goal, target_skill_id: str, max_depth: int
    ) -> list[ExecutionPlan]:
        queue = deque([([target_skill_id], set(goal.known_artifacts), 0)])
        raw_plans: list[list[str]] = []
        incomplete: list[ExecutionPlan] = []

        while queue and len(raw_plans) < 30:
            skill_ids, known, depth = queue.popleft()
            if depth > max_depth:
                continue

            missing = self._missing_inputs(skill_ids, known)
            if not missing:
                raw_plans.append(skill_ids)
                continue

            # Pick the next missing artifact and search backward for producer
            # Skills. This keeps planning explainable while we do not yet have a
            # full HTN/constraint solver.
            requirement = missing[0]
            producers = [
                producer
                for producer in self.graph.get_producers(requirement)
                if producer not in skill_ids
            ]
            if not producers:
                incomplete.append(
                    self._to_execution_plan(
                        goal,
                        list(reversed(skill_ids)),
                        [requirement],
                    )
                )
                continue

            for producer_id in producers[:5]:
                producer_outputs = set(self.graph.get_output_artifacts(producer_id))
                queue.append(
                    ([producer_id] + skill_ids, known | producer_outputs, depth + 1)
                )

        plans = [
            self._to_execution_plan(goal, list(reversed(list(reversed(ids)))))
            for ids in raw_plans
        ]
        if not plans and incomplete:
            plans.extend(incomplete)
        return plans

    def _missing_inputs(self, skill_ids: list[str], known: set[str]) -> list[str]:
        available = set(known)
        missing: list[str] = []
        for skill_id in skill_ids:
            skill = self.registry.get(skill_id)
            for param in skill.inputs:
                if param.required and param.type not in available:
                    if param.type not in missing:
                        missing.append(param.type)
            available.update(output.type for output in skill.outputs)
        return missing

    def _to_execution_plan(
        self,
        goal: Goal,
        skill_ids: list[str],
        missing_requirements: list[str] | None = None,
    ) -> ExecutionPlan:
        ordered = skill_ids
        steps: list[PlanStep] = []
        produced: list[str] = []
        available_sources = {artifact: "task" for artifact in goal.known_artifacts}

        for skill_id in ordered:
            skill = self.registry.get(skill_id)
            input_mapping = {
                param.name: available_sources.get(param.type, f"<missing:{param.type}>")
                for param in skill.inputs
                if param.required
            }
            output_mapping = {}
            for output in skill.outputs:
                output_mapping[output.name] = output.type
                available_sources[output.type] = f"{skill.id}.{output.name}"
                if output.type not in produced:
                    produced.append(output.type)

            steps.append(
                PlanStep(
                    skill_id=skill.id,
                    operator=CompositionOperator.SEQUENTIAL,
                    input_mapping=input_mapping,
                    output_mapping=output_mapping,
                )
            )

        return ExecutionPlan(
            id="plan_pending",
            task=goal.task,
            steps=steps,
            required_outputs=sorted(goal.required_outputs),
            produced_artifacts=produced,
            atomic_skills=self._atomic_for_steps(ordered),
            missing_requirements=missing_requirements or [],
        )

    def _expand_composites(self, skill_ids: list[str]) -> list[str]:
        expanded: list[str] = []
        for skill_id in skill_ids:
            skill = self.registry.get(skill_id)
            if skill.kind == SkillKind.ATOMIC:
                expanded.append(skill_id)
            else:
                expanded.extend(self.decomposer.atomic_skills(skill_id))
        return self._unique_preserving_order(expanded)

    def _atomic_for_steps(self, skill_ids: list[str]) -> list[str]:
        atoms: list[str] = []
        for skill_id in skill_ids:
            atoms.extend(self.decomposer.atomic_skills(skill_id))
        return self._unique_preserving_order(atoms)

    def _deduplicate(self, plans: list[ExecutionPlan]) -> list[ExecutionPlan]:
        seen: set[tuple[str, ...]] = set()
        result: list[ExecutionPlan] = []
        for plan in plans:
            key = tuple(step.skill_id for step in plan.steps)
            if key not in seen:
                seen.add(key)
                result.append(plan)
        return result

    @staticmethod
    def _unique_preserving_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
