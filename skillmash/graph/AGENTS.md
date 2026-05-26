# Graph Module Guide

`skillmash.graph` owns offline Skill graph construction. It consumes normalized
`SkillRepresentation` records and produces graph artifacts used by online
retrieval and orchestration.

## Responsibilities

- Build a validated Skill registry from normalized representations.
- Generate cheap deterministic relation candidates before any LLM call.
- Validate LLM relation judgments and keep traceable match artifacts.
- Resolve final relation edges through `RelationResolver` (LLM + deterministic
  exact-I/O rules + diagnostics aggregation).
- Build the Skill-only graph and lookup indexes.
- Reuse lexical rules from `skillmash.lexicon` for tokenization and generic
  I/O-name filtering.
- Write graph build artifacts through `write_graph_build_result`.

## Relation Types

Supported relation types are:

- `can_feed`: a source Skill can produce an artifact usable by a target Skill.

Add new relation types only with matching model, validation, writer, index, and
test updates.

## Constraints

- Keep candidate generation deterministic where possible so graph diffs are
  reviewable.
- Validate all LLM output before accepting it into `LLMMatch` or graph edges.
- Avoid indexing broad generic I/O names, high-fanout text terms, and stop
  terms as if they were precise retrieval keys.
- Preserve accepted match traceability through candidate IDs, reasons,
  supporting fields, confidence, and method.
- Tests in `tests/test_graph.py` are the first place to update when changing
  candidate generation, match validation, graph shape, index shape, or artifact
  writing.
