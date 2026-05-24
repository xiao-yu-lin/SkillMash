# Dual-Track Orchestration (Recall + Validation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pre-production-safe orchestration pipeline that preserves recall while enforcing strict executable-plan gating and conservative rejection behavior.

**Architecture:** Keep wide candidate generation in planning/search, add deterministic validation gate before recommendation, and only allow reranking within validated plans. Slot-level parallel candidates (e.g., `A->B` and `A->B2`) are resolved after candidate plans are formed, with reliability-first scoring and explicit can-feed adjacency checks.

**Tech Stack:** Python 3.9+, dataclasses, existing SkillMash orchestration/graph modules, pytest.

---

## File Structure Map

- `skillmash/orchestration/planning/models.py`
  - Extend planning config and payload contracts for dual-track flow.
- `skillmash/orchestration/planning/search.py`
  - Decouple entry width from branch width; keep light pruning in search.
- `skillmash/orchestration/planning/orchestrator.py`
  - Reorder to `ground -> recall -> slot_group -> hard_filter -> strategy_rank -> rerank(optional) -> conservative_decision`.
- `skillmash/orchestration/planning/slot_grouping.py` (new)
  - Detect parallel downstream slot candidates and normalize slot groups.
- `skillmash/orchestration/validation/policy.py` (new)
  - Hard gate policy and fail-code constants.
- `skillmash/orchestration/validation/validator.py` (new)
  - Deterministic plan hard filter and conservative rejection report.
- `skillmash/orchestration/strategy/interfaces.py` (new)
  - Strategy protocol (`hard_filter`, `rank_score`) and context/result models.
- `skillmash/orchestration/strategy/reliability_first.py` (new)
  - Default reliability-first strategy implementation.
- `skillmash/reranking/plan_reranker.py`
  - Keep behavior, but consume only already-validated plans from orchestrator.
- `skillmash/orchestration/artifacts.py`
  - Stop guessing representation dir; read explicit vocab paths from manifest when present.
- `skillmash/graph/models.py`
  - Add manifest artifact pointers for `io_name_vocab` and `task_vocab`.
- `tests/test_orchestration_planner.py`
  - Add P0 tests for hard gate, conservative rejection, slot candidate legality.
- `tests/test_graph.py`
  - Add P1 compatibility tests for manifest artifact pointers.
- `README.md`, `examples/representation_extraction_demo.py`
  - Align env var docs to `LLM_*` naming.

---

### Task 1: Add Dual-Track Contracts and Config Surface

**Files:**
- Modify: `skillmash/orchestration/planning/models.py`
- Test: `tests/test_orchestration_planner.py`

- [ ] **Step 1: Write the failing test for new planning knobs and conservative report fields**

```python
# tests/test_orchestration_planner.py

def test_planning_config_exposes_entry_width_and_conservative_flags() -> None:
    from skillmash.orchestration.planning.models import PlanningConfig

    cfg = PlanningConfig()

    assert hasattr(cfg, "max_entry_skills")
    assert hasattr(cfg, "conservative_reject")
    assert cfg.conservative_reject is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_planning_config_exposes_entry_width_and_conservative_flags`
Expected: FAIL with missing attributes on `PlanningConfig`.

- [ ] **Step 3: Implement minimal config contract changes**

```python
# skillmash/orchestration/planning/models.py (PlanningConfig excerpt)
@dataclass(frozen=True)
class PlanningConfig:
    min_edge_confidence: float = 0.7
    max_depth: int = 4
    max_plans: int = 20
    max_branch: int = 8
    max_entry_skills: int = 40
    top_m: int = 12
    top_k: int = 3
    include_candidates: bool = True
    conservative_reject: bool = True
    relation_feedback_path: str = ".skillmash/runtime/relation_feedback.jsonl"
    relation_feedback_window_days: int = 30
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_planning_config_exposes_entry_width_and_conservative_flags`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skillmash/orchestration/planning/models.py tests/test_orchestration_planner.py
git commit -m "feat(planning): add entry width and conservative reject config"
```

### Task 2: Decouple Entry Recall From Branch Expansion

**Files:**
- Modify: `skillmash/orchestration/planning/search.py`
- Modify: `skillmash/orchestration/planning/orchestrator.py`
- Test: `tests/test_orchestration_planner.py`

- [ ] **Step 1: Write failing test proving entry recall is not capped by max_branch**

```python
# tests/test_orchestration_planner.py

def test_entry_recall_uses_max_entry_skills_not_max_branch(tmp_path: Path) -> None:
    # build artifact with many entry-capable skills and low branch
    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient({"available_artifacts": [], "goal_terms": ["api"]}),
        planning_config=PlanningConfig(max_entry_skills=6, max_branch=2, max_plans=10),
    )
    result = planner.plan("api")
    assert len(result.get("plans", [])) >= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_entry_recall_uses_max_entry_skills_not_max_branch`
Expected: FAIL due to current entry cap bound to `max_branch`.

- [ ] **Step 3: Implement entry-width decoupling**

```python
# skillmash/orchestration/planning/search.py (signature excerpts)
def search_plans(..., max_branch: int, max_entry_skills: int) -> list[OrchestrationPlan]:
    entry_ids = entry_skill_ids(
        artifacts=artifacts,
        available=initial_available,
        goal_terms=grounded.goal_terms,
        max_entry_skills=max_entry_skills,
    )


def entry_skill_ids(*, artifacts: BuildArtifacts, available: frozenset[tuple[str, str]], goal_terms: set[str], max_entry_skills: int) -> list[str]:
    ...
    return ordered_ids[:max_entry_skills]
```

```python
# skillmash/orchestration/planning/orchestrator.py (call excerpt)
plans = search_plans(
    ...,
    max_branch=max(1, config.max_branch),
    max_entry_skills=max(1, config.max_entry_skills),
)
```

- [ ] **Step 4: Run targeted tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_entry_recall_uses_max_entry_skills_not_max_branch tests/test_orchestration_planner.py::test_orchestrator_traverses_can_feed_when_needed`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skillmash/orchestration/planning/search.py skillmash/orchestration/planning/orchestrator.py tests/test_orchestration_planner.py
git commit -m "feat(orchestration): decouple entry recall width from branch expansion"
```

### Task 3: Add Slot Grouping for Parallel Downstream Candidates

**Files:**
- Create: `skillmash/orchestration/planning/slot_grouping.py`
- Modify: `skillmash/orchestration/planning/orchestrator.py`
- Test: `tests/test_orchestration_planner.py`

- [ ] **Step 1: Write failing test for `A->B` and `A->B2` slot grouping**

```python
# tests/test_orchestration_planner.py

def test_slot_grouping_collects_parallel_downstream_candidates() -> None:
    from skillmash.orchestration.planning.slot_grouping import build_slot_groups

    plans = [{"steps": [{"skill_id": "A"}, {"skill_id": "B"}]}, {"steps": [{"skill_id": "A"}, {"skill_id": "B2"}]}]
    relation_edges = [{"source": "skill:B2", "target": "skill:B", "type": "substitute_for"}]

    grouped = build_slot_groups(plans, relation_edges)
    assert grouped[0]["slots"][0]["candidates"] == ["B", "B2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_slot_grouping_collects_parallel_downstream_candidates`
Expected: FAIL with missing module/function.

- [ ] **Step 3: Implement slot grouping module and orchestrator wiring**

```python
# skillmash/orchestration/planning/slot_grouping.py
from __future__ import annotations

from collections import defaultdict
from typing import Any


def build_slot_groups(plans: list[dict[str, Any]], relation_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    substitute = defaultdict(set)
    for edge in relation_edges:
        if edge.get("type") == "substitute_for":
            src = str(edge.get("source") or "").removeprefix("skill:")
            dst = str(edge.get("target") or "").removeprefix("skill:")
            if src and dst:
                substitute[src].add(dst)
                substitute[dst].add(src)

    grouped: list[dict[str, Any]] = []
    for plan in plans:
        steps = [str(step.get("skill_id") or "") for step in plan.get("steps", []) if step.get("skill_id")]
        slots = []
        for index in range(1, len(steps)):
            curr = steps[index]
            candidates = {curr}
            candidates.update(substitute.get(curr, set()))
            slots.append({"slot_index": index + 1, "candidates": sorted(candidates)})
        grouped.append({**plan, "slots": slots})
    return grouped
```

```python
# skillmash/orchestration/planning/orchestrator.py (plan flow excerpt)
candidate_plans = [plan.to_dict() for plan in plans]
candidate_plans = build_slot_groups(candidate_plans, self.all_relation_edges)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_slot_grouping_collects_parallel_downstream_candidates tests/test_orchestration_planner.py::test_orchestrator_applies_slot_substitute_candidates`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skillmash/orchestration/planning/slot_grouping.py skillmash/orchestration/planning/orchestrator.py tests/test_orchestration_planner.py
git commit -m "feat(orchestration): add slot grouping for parallel downstream candidates"
```

### Task 4: Introduce Deterministic Hard Validation Gate

**Files:**
- Create: `skillmash/orchestration/validation/policy.py`
- Create: `skillmash/orchestration/validation/validator.py`
- Modify: `skillmash/orchestration/planning/orchestrator.py`
- Test: `tests/test_orchestration_planner.py`

- [ ] **Step 1: Write failing tests for conservative rejection and fail codes**

```python
# tests/test_orchestration_planner.py

def test_orchestrator_returns_conservative_rejection_when_no_validated_plan(tmp_path: Path) -> None:
    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient({"available_artifacts": [], "goal_terms": ["review"]}),
        planning_config=PlanningConfig(conservative_reject=True),
    )
    result = planner.plan("review api")

    assert result["recommended_plans"] == []
    assert result["decision"]["mode"] == "conservative_reject"
    assert result["decision"]["fail_code_counts"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_orchestrator_returns_conservative_rejection_when_no_validated_plan`
Expected: FAIL because decision payload does not exist.

- [ ] **Step 3: Implement validator and orchestrator hard gate**

```python
# skillmash/orchestration/validation/policy.py
HARD_FAIL_MISSING_REQUIRED_INPUT = "missing_required_input"
HARD_FAIL_TYPE_MISMATCH = "type_mismatch"
HARD_FAIL_LOW_CONFIDENCE_EDGE = "low_confidence_edge"
HARD_FAIL_NO_EXPLICIT_CAN_FEED = "no_explicit_can_feed"


def default_policy() -> dict:
    return {
        "allow_unknown_required_types": False,
        "min_edge_confidence": 0.7,
        "require_explicit_adjacency": True,
    }
```

```python
# skillmash/orchestration/validation/validator.py
from __future__ import annotations

from collections import Counter
from typing import Any


def hard_filter_plans(plans: list[dict[str, Any]], policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, list[str]]]:
    passed: list[dict[str, Any]] = []
    fail_counts: Counter[str] = Counter()
    fail_reasons: dict[str, list[str]] = {}

    for idx, plan in enumerate(plans, start=1):
        plan_id = f"plan_{idx}"
        reasons = []
        if plan.get("missing_inputs"):
            reasons.append("missing_required_input")

        for edge in plan.get("can_feed_edges", []):
            if float(edge.get("confidence") or 0.0) < float(policy.get("min_edge_confidence", 0.7)):
                reasons.append("low_confidence_edge")

        if reasons:
            fail_reasons[plan_id] = sorted(set(reasons))
            for code in fail_reasons[plan_id]:
                fail_counts[code] += 1
            continue

        passed.append(plan)

    return passed, dict(fail_counts), fail_reasons
```

```python
# skillmash/orchestration/planning/orchestrator.py (decision excerpt)
validated, fail_counts, fail_reasons = hard_filter_plans(candidate_plans, policy={"min_edge_confidence": config.min_edge_confidence})
if not validated and config.conservative_reject:
    return {
        "query": query,
        "build_dir": str(self.artifacts.build_dir),
        "grounded_query": grounded.to_dict(),
        "plans": candidate_plans if config.include_candidates else [],
        "recommended_plans": [],
        "ranking_mode": "conservative_reject",
        "decision": {
            "mode": "conservative_reject",
            "fail_code_counts": fail_counts,
            "plan_fail_reasons": fail_reasons,
        },
    }
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_orchestrator_returns_conservative_rejection_when_no_validated_plan tests/test_orchestration_planner.py::test_orchestrator_returns_recommendations_with_ranking_trace`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skillmash/orchestration/validation/policy.py skillmash/orchestration/validation/validator.py skillmash/orchestration/planning/orchestrator.py tests/test_orchestration_planner.py
git commit -m "feat(orchestration): add deterministic hard validation and conservative reject"
```

### Task 5: Enforce Explicit Adjacency for Slot Replacements

**Files:**
- Modify: `skillmash/orchestration/planning/orchestrator.py`
- Test: `tests/test_orchestration_planner.py`

- [ ] **Step 1: Write failing test ensuring replacement fails without explicit adjacency**

```python
# tests/test_orchestration_planner.py

def test_slot_replacement_requires_explicit_can_feed_adjacency(tmp_path: Path) -> None:
    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient({"available_artifacts": [], "goal_terms": ["review", "api"]}),
    )
    result = planner.plan("Generate an api spec and review it")

    for plan in result.get("plans", []):
        assert all(edge.get("method") != "slot_replacement_chain" for edge in plan.get("can_feed_edges", []))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_slot_replacement_requires_explicit_can_feed_adjacency`
Expected: FAIL due to generated replacement chain edges.

- [ ] **Step 3: Implement explicit-adjacency requirement and remove synthetic 1.0 chain edges**

```python
# skillmash/orchestration/planning/orchestrator.py (replacement excerpt)
def _has_explicit_adjacency(source_id: str, target_id: str, edges: list[dict[str, Any]], min_conf: float) -> bool:
    for edge in edges:
        if edge.get("type") != "can_feed":
            continue
        src = str(edge.get("source") or "").removeprefix("skill:")
        dst = str(edge.get("target") or "").removeprefix("skill:")
        if src == source_id and dst == target_id and float(edge.get("confidence") or 0.0) >= min_conf:
            return True
    return False

# In slot replacement acceptance path:
# require _has_explicit_adjacency(prev, candidate_id, self.all_can_feed_edges, config.min_edge_confidence)
# and _has_explicit_adjacency(candidate_id, nxt, self.all_can_feed_edges, config.min_edge_confidence)

# In _remap_plan_edges:
# remove synthetic edge generation fallback; return [] if remap cannot be proven.
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_slot_replacement_requires_explicit_can_feed_adjacency tests/test_orchestration_planner.py::test_orchestrator_records_feedback_for_incompatible_substitute`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skillmash/orchestration/planning/orchestrator.py tests/test_orchestration_planner.py
git commit -m "fix(orchestration): require explicit can_feed adjacency for slot replacement"
```

### Task 6: Strategy Layer and Reliability-First Scoring

**Files:**
- Create: `skillmash/orchestration/strategy/interfaces.py`
- Create: `skillmash/orchestration/strategy/reliability_first.py`
- Modify: `skillmash/orchestration/planning/orchestrator.py`
- Test: `tests/test_orchestration_planner.py`

- [ ] **Step 1: Write failing test for reliability-first ordering on validated plans**

```python
# tests/test_orchestration_planner.py

def test_reliability_first_prefers_higher_confidence_validated_plan(tmp_path: Path) -> None:
    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient({"available_artifacts": [], "goal_terms": ["api", "review"]}),
        planning_config=PlanningConfig(top_k=1),
    )
    result = planner.plan("review api")

    assert result["recommended_plans"]
    assert result["decision"]["strategy"] == "reliability_first"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_reliability_first_prefers_higher_confidence_validated_plan`
Expected: FAIL because no strategy metadata exists.

- [ ] **Step 3: Implement strategy interfaces and reliability-first scorer**

```python
# skillmash/orchestration/strategy/interfaces.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any


@dataclass(frozen=True)
class PruneContext:
    query: str
    grounded_query: dict[str, Any]
    policy: dict[str, Any]
    runtime_constraints: dict[str, Any]


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    hard_fail_codes: list[str]


class PlanStrategy(Protocol):
    name: str

    def hard_filter(self, plan: dict[str, Any], ctx: PruneContext) -> FilterResult:
        ...

    def rank_score(self, plan: dict[str, Any], ctx: PruneContext) -> float:
        ...
```

```python
# skillmash/orchestration/strategy/reliability_first.py
from __future__ import annotations

from skillmash.orchestration.strategy.interfaces import PlanStrategy, PruneContext, FilterResult


class ReliabilityFirstStrategy(PlanStrategy):
    name = "reliability_first"

    def hard_filter(self, plan: dict, ctx: PruneContext) -> FilterResult:
        return FilterResult(passed=not bool(plan.get("missing_inputs")), hard_fail_codes=[])

    def rank_score(self, plan: dict, ctx: PruneContext) -> float:
        edge_conf = float(plan.get("edge_confidence") or 0.0)
        step_penalty = len(plan.get("steps") or []) * 0.01
        missing_penalty = len(plan.get("missing_inputs") or []) * 1.0
        return edge_conf - step_penalty - missing_penalty
```

```python
# skillmash/orchestration/planning/orchestrator.py (decision metadata excerpt)
result["decision"] = {
    **result.get("decision", {}),
    "strategy": "reliability_first",
}
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py::test_reliability_first_prefers_higher_confidence_validated_plan tests/test_plan_reranker.py::test_plan_reranker_only_sorts_existing_candidate_plans`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skillmash/orchestration/strategy/interfaces.py skillmash/orchestration/strategy/reliability_first.py skillmash/orchestration/planning/orchestrator.py tests/test_orchestration_planner.py
git commit -m "feat(orchestration): add reliability-first strategy layer"
```

### Task 7: Manifest Artifact Pointers and Stable Vocab Loading

**Files:**
- Modify: `skillmash/graph/models.py`
- Modify: `skillmash/orchestration/artifacts.py`
- Test: `tests/test_graph.py`

- [ ] **Step 1: Write failing test for explicit vocab artifact loading**

```python
# tests/test_graph.py

def test_load_build_artifacts_prefers_manifest_vocab_paths(tmp_path: Path) -> None:
    manifest = {
        "artifacts": {
            "skills": "skills.json",
            "graph": "skill_graph.json",
            "index": "skill_index.json",
            "io_name_vocab": "repre/io_name_vocab.json",
            "task_vocab": "repre/task_vocab.json",
        }
    }
    (tmp_path / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # write minimal required artifacts...
    artifacts = load_build_artifacts(tmp_path)
    assert artifacts.io_name_vocab is not None
    assert artifacts.task_vocab is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_graph.py::test_load_build_artifacts_prefers_manifest_vocab_paths`
Expected: FAIL because loader still guesses directory.

- [ ] **Step 3: Implement manifest pointers and loader usage**

```python
# skillmash/graph/models.py (BuildManifest default artifacts excerpt)
artifacts: Dict[str, str] = field(
    default_factory=lambda: {
        "skills": "skills.json",
        "graph": "skill_graph.json",
        "index": "skill_index.json",
        "llm_matches": "llm_matches.json",
        "diagnostics": "diagnostics.json",
        "io_name_vocab": "io_name_vocab.json",
        "task_vocab": "task_vocab.json",
    }
)
```

```python
# skillmash/orchestration/artifacts.py (loader excerpt)
artifacts_map = manifest.get("artifacts", {})
io_vocab_path = root / artifacts_map.get("io_name_vocab", "io_name_vocab.json")
task_vocab_path = root / artifacts_map.get("task_vocab", "task_vocab.json")

return BuildArtifacts(
    ...,
    io_name_vocab=_read_optional_json(io_vocab_path),
    task_vocab=_read_optional_json(task_vocab_path),
)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_graph.py::test_load_build_artifacts_prefers_manifest_vocab_paths tests/test_graph.py::test_graph_builder_pipeline_writes_expected_artifacts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skillmash/graph/models.py skillmash/orchestration/artifacts.py tests/test_graph.py
git commit -m "fix(artifacts): load vocab artifacts from manifest paths"
```

### Task 8: Align LLM Env Documentation and Examples

**Files:**
- Modify: `README.md`
- Modify: `examples/representation_extraction_demo.py`
- Test: `tests/test_representation.py`

- [ ] **Step 1: Write failing test for env var naming in docs/example snippets**

```python
# tests/test_representation.py

def test_docs_and_example_reference_llm_env_names() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    example = Path("examples/representation_extraction_demo.py").read_text(encoding="utf-8")

    assert "LLM_MODEL" in readme
    assert "LLM_API_KEY" in readme
    assert "LLM_MODEL" in example
    assert "LLM_API_KEY" in example
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_representation.py::test_docs_and_example_reference_llm_env_names`
Expected: FAIL because snippets still contain `OPENAI_*` names.

- [ ] **Step 3: Update docs/examples to `LLM_*` naming**

```markdown
# README.md (env block)
LLM_API_KEY=your_api_key_here
LLM_MODEL=gpt-4.1-mini
LLM_BASE_URL=https://api.openai.com/v1
```

```python
# examples/representation_extraction_demo.py (usage comment excerpt)
"""
The LLM configuration is read from .env or the process environment:
    LLM_API_KEY=...
    LLM_BASE_URL=https://api.openai.com/v1
    LLM_MODEL=...
"""
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_representation.py::test_docs_and_example_reference_llm_env_names tests/test_representation.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md examples/representation_extraction_demo.py tests/test_representation.py
git commit -m "docs: align configuration examples to LLM env variables"
```

### Task 9: Full Regression, Decision Trace Assertions, and Final Hygiene

**Files:**
- Modify: `tests/test_orchestration_planner.py`
- Modify: `tests/test_plan_reranker.py`

- [ ] **Step 1: Add integration assertions for decision trace contract**

```python
# tests/test_orchestration_planner.py

def test_orchestrator_decision_trace_has_mode_and_fail_aggregation(tmp_path: Path) -> None:
    planner = SkillOrchestrator(
        load_build_artifacts(tmp_path),
        llm_client=FakeGroundingClient({"available_artifacts": [], "goal_terms": ["x"]}),
        planning_config=PlanningConfig(conservative_reject=True),
    )
    result = planner.plan("x")
    assert "decision" in result
    assert "mode" in result["decision"]
    assert "fail_code_counts" in result["decision"]
```

- [ ] **Step 2: Run focused orchestration + rerank suites**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp tests/test_orchestration_planner.py tests/test_plan_reranker.py`
Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: PASS all tests.

- [ ] **Step 4: Update module docs for architecture changes**

```markdown
# docs/modules/online-pruning-ranking.md (add)
- Hard validation gate runs before ranking.
- Reranker only sorts validated plans.
- Conservative reject mode returns no recommendations when validated set is empty.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_orchestration_planner.py tests/test_plan_reranker.py docs/modules/online-pruning-ranking.md
git commit -m "test/docs: finalize dual-track validation and decision trace coverage"
```

---

## Self-Review Checklist

### Spec coverage

- Dual-track flow: covered by Tasks 2, 3, 4, 6.
- Two-stage pruning (light prune + post-plan main prune): covered by Tasks 2, 4, 6.
- Slot candidate logic for `A->B` and `A->B2`: covered by Tasks 3, 5.
- Conservative rejection: covered by Task 4 and Task 9.
- P0/P1 fixes from spec: covered by Tasks 2, 4, 5, 7, 8.

### Placeholder scan

- No `TODO/TBD/later` placeholders in tasks.
- Every coding step includes concrete code snippets.
- Every verification step includes exact commands and expected outcomes.

### Type/signature consistency

- New config fields defined once in `PlanningConfig` and referenced consistently.
- Slot grouping, validator, and strategy interfaces use stable `dict[str, Any]` payloads compatible with existing plan dictionaries.

