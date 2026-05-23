# Orchestration Module Guide

`skillmash.orchestration` owns online planning over graph build artifacts. It
loads the graph, index, and Skill metadata, grounds a user query, and searches
candidate plans over accepted `can_feed` edges.

## Responsibilities

- Load build artifacts with `load_build_artifacts`.
- Ground user queries into known artifacts and goal terms.
- Search candidate Skill plans using available artifacts and `can_feed` edges.
- Return explainable plan payloads with steps, stages, produced artifacts,
  missing inputs, status, scores, and reasons.

## Constraints

- Do not invent user-provided artifacts. Only select artifacts the user clearly
  says they have, provide, attach, upload, or want to use.
- Preserve the implicit query artifacts `goal:text` and `query:text` unless the
  surrounding contract changes deliberately.
- Keep `ready` versus `needs_input` status explainable from each plan's missing
  inputs.
- Keep plan ordering stable and testable: ready plans, fewer missing inputs,
  user artifact consumption, plan length, goal score, and edge confidence all
  affect ranking.
- Tests in `tests/test_orchestration_planner.py` are the first place to update
  when changing grounding, search behavior, stage construction, or plan ranking.

## Reranking Boundary

`skillmash.reranking` may reorder and annotate existing candidate plans. It
should not create new execution paths, new Skill steps, or new missing inputs
that were not present in the planner output.
