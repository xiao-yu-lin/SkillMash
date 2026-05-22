# Orchestration Forward Planning

## 1. Purpose

Skill orchestration turns a user query into candidate Skill execution plans by
using offline build artifacts plus an LLM grounding step. It does not rescan
Skill folders, re-extract representations, or rebuild the graph.

The current implementation lives under `skillmash.orchestration`:

- `artifacts.py`: loads `build_manifest.json`, `skills.json`,
  `skill_graph.json`, `skill_index.json`, and optional vocabulary files.
- `planner.py`: asks the LLM to ground the query into the offline vocabulary,
  validates that grounding, then searches candidate plans over `can_feed` edges.
- `skillmash.reranking`: asks the LLM to rank candidate paths into Top-K
  recommended plans.
- `examples/graph_online_demo.py`: thin command-line entrypoint.

## 2. Planning Flow

```text
user query
  -> LLM grounding against offline artifact/task/output vocabulary
  -> validate grounded artifacts against known normalized names and types
  -> find entry Skills that consume grounded artifacts or match goal terms
  -> run forward search over can_feed graph
  -> compose shared-upstream paths into DAG candidate plans
  -> LLM rerank candidate plans into Top-K recommended plans
```

The LLM is part of the orchestration path. It is responsible for natural-language
understanding, cross-language mapping, and implicit intent recognition. Local
code is responsible for validation and graph legality.

## 3. Query Grounding

`SkillOrchestrator.ground_query()` calls the configured LLM and asks it to return:

```json
{
  "available_artifacts": [
    {"name": "api_spec", "type": "yaml"}
  ],
  "goal_terms": ["review", "audit", "security"]
}
```

The prompt provides only offline vocabulary:

- normalized Skill inputs and outputs from `skills.json`;
- output vocabulary from `skill_index.json`;
- task vocabulary from `task_vocab.json` and `skill_index.json`;
- aliases and examples from `io_name_vocab.json` when available.

The LLM output is then validated:

- grounded artifact names must exist in the offline artifact vocabulary;
- artifact types must match known types, unless the known type is `unknown`;
- goal terms are tokenized before being used for local scoring;
- implicit `goal:text` and `query:text` are always added because the user query
  itself is a runtime artifact.

## 4. Forward Search

The planner builds entry nodes from Skills that either:

- consume the grounded user artifacts, or
- match grounded goal terms.

From each entry node it performs bounded BFS over `can_feed` edges:

- `min_edge_confidence` defaults to `0.7`.
- `max_depth` limits Skill steps per plan.
- `max_branch` limits expansion fanout at each state.
- `max_plans` limits returned candidate plans.

Each state carries selected Skill IDs, available artifact `(name, type)` pairs,
and `can_feed` edges used so far. When a Skill is added, its outputs are added to
the available artifact set.

Linear paths that share an upstream producer are composed into DAG plans. For
example, if the graph contains both `wisedev-team -> api-design-review-team` and
`wisedev-team -> devops-pipeline-design-team`, orchestration emits one candidate
with stages:

```text
stage 1: wisedev-team
stage 2: api-design-review-team, devops-pipeline-design-team
```

## 5. Plan Output

Each candidate plan includes:

- `status`: `ready` or `needs_input`.
- `stages`: DAG execution stages; Skills in the same stage can run in parallel.
- `steps`: ordered Skill calls.
- `missing_inputs`: required inputs not satisfied by the user or prior steps.
- `produced_artifacts`: outputs accumulated by the plan.
- `can_feed_edges`: graph edges used to connect steps.
- `goal_score`: deterministic goal coverage score.
- `edge_confidence`: average confidence of used `can_feed` edges.
- `consumed_user_artifacts`: how many explicit user-provided artifacts the first
  step consumes.

Candidate plans are ranked by readiness, missing input count, explicit user
artifact consumption, path length, goal score, and edge confidence.

## 6. Reranking

`PlanReranker` receives the bounded DAG candidate plans and asks the LLM to
choose the best existing candidates. It does not merge plans and does not change
Skill order or stages. The LLM returns candidate `plan_index` values, and invalid
indexes are dropped during validation.

`top_k` controls the number of recommended plans returned by the reranker. The
demo prints recommended plans by default and shows raw candidate paths only when
`--show_candidates` is passed.

## 7. Example

```powershell
.\.venv\Scripts\python.exe examples\graph_online_demo.py `
  --build_dir OUTPUT\build `
  --query "I have an OpenAPI spec and want a security review" `
  --max_plans 20 `
  --top_k 3
```

The demo always uses the LLM configuration from `.env` or the environment.

## 8. Next Steps

1. Add confidence and provenance fields to each grounded artifact and goal term.
2. Distinguish hard required inputs from conversational inputs that can be
   satisfied by the user query itself.
3. Add pruning rules for redundant producers, especially when the user already
   supplied the artifact that an upstream Skill would generate.
