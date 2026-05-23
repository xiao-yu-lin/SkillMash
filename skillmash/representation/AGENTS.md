# Representation Module Guide

`skillmash.representation` owns the offline Skill representation extraction
stage. It scans Skill folders, parses `SKILL.md`, extracts a candidate schema
with an LLM, normalizes the result, and writes compact representation artifacts
for graph construction.

## Responsibilities

- Discover folders that contain `SKILL.md` through `SkillFolderScanner`.
- Parse frontmatter, body text, and content hashes through
  `SkillManifestParser`.
- Extract candidate schemas through `SkillSchemaExtractor` implementations.
- Normalize free-form LLM output through `SkillRepresentationNormalizer`,
  `IONameVocabulary`, task vocabulary handling, and data type aliases.
- Write extraction artifacts with `write_extraction_result`.

## Core Contracts

The main graph-facing contract is `SkillRepresentation`. Its input and output
fields use:

- `ParameterSpec` for runtime inputs.
- `ArtifactSpec` for produced outputs.
- `Condition` for preconditions and postconditions.
- `NormalizationDecision` for traceability outside the compact graph-facing
  representation.

When adding or changing fields, update the model dataclasses, normalization
logic, writers, tests, and documentation together. Do not leave artifact shape
changes implicit.

## Constraints

- Keep normalized representation output stable and deterministic where possible.
- Do not bypass vocabulary or normalizer logic when producing graph-facing
  `name`, `type`, or `tasks` values.
- Preserve the distinction between I/O semantic `name` and data carrier `type`.
- Keep diagnostics structured and useful; prefer explicit diagnostic codes over
  free-form strings.
- Tests in `tests/test_representation.py` are the first place to update when
  changing scanning, parsing, normalization, vocabulary, or artifact behavior.
