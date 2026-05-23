# SkillMash Agent Guide

This repository is a Python prototype for extracting Skill representations,
building Skill graphs, planning orchestration paths, and reranking candidate
plans. Treat the normalized representation and graph artifacts as the stable
interfaces between stages.

## Project Map

- `skillmash/representation/`: scans folder-based Skills, parses `SKILL.md`,
  calls an LLM extractor, normalizes schemas, and writes representation
  artifacts.
- `skillmash/graph/`: builds a registry, deterministic relation candidates,
  LLM-backed relation matches, Skill graph artifacts, and lookup indexes from
  normalized representations.
- `skillmash/orchestration/`: loads graph build artifacts and creates candidate
  execution plans from a user query, grounded artifacts, and `can_feed` edges.
- `skillmash/reranking/`: reranks existing orchestration candidates with an
  LLM. It should not invent new execution paths.
- `examples/`: CLI demos for representation extraction, graph construction,
  and online orchestration.
- `docs/`: design notes and module documentation. Update these when changing
  architecture or artifact contracts.
- `tests/`: pytest coverage for representation, graph construction,
  orchestration, and reranking behavior.
- `ui/`: a small local graph/orchestration UI served by `ui/serve.py`.
- `tools/`: utility scripts.
- `skills/`: sample or indexed Skill data.

## Development Environment

This project is developed on both Windows and macOS. Keep commands and docs
clear for both Windows `\` paths and POSIX `/` paths. In Python code, prefer
`pathlib.Path` over string path manipulation.

Use the existing virtual environment for tests until the Python version
constraint is fixed. At the moment, `uv run ...` dependency resolution can fail
because `pyproject.toml` declares `requires-python >=3.9`, while the current
FastAPI dependency requires Python `>=3.10`.

Run the test suite with:

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp
```

```bash
# macOS
.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp
```

The explicit `--basetemp .pytest-tmp` keeps pytest temporary files inside the
workspace and avoids Windows temp-directory permission issues.

## LLM Configuration

LLM settings follow the implementation in `skillmash/representation/llm.py` and
`.env.example`, not older README examples. The expected variables are:

- `LLM_MODEL` is required.
- `LLM_API_KEY` is required for API mode.
- `LLM_BASE_URL` is optional and defaults to OpenAI-compatible API behavior.
- `LLM_TEMPERATURE`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_TOKENS`, and
  `LLM_BATCH_SIZE` are optional generation controls.

If `LLM_MODEL` points to an existing local model path, SkillMash uses the local
vLLM backend and caches one client per model path.

Never commit secrets or local runtime configuration. Keep `.env` private.

## Generated Files

Do not commit generated runtime artifacts unless the user explicitly asks for
them. Common generated paths include:

- `.env`
- `OUTPUT/`
- `.skillmash/`
- `.pytest_cache/`
- `.pytest-tmp/`
- graph or extraction output directories created by examples

## Development Principles

- Keep dataclass contracts stable. Changes to `SkillRepresentation`, graph
  models, build manifests, or plan payloads need focused tests and artifact
  writer/loader updates.
- Downstream graph and orchestration code should consume normalized
  `SkillRepresentation` records. Do not make those stages reread `SKILL.md`.
- Prefer deterministic processing before LLM calls. LLM output must be parsed,
  normalized, and validated before it becomes graph or planning state.
- Add tests close to the behavior being changed. Broaden coverage when touching
  shared artifact contracts, graph shape, or plan ranking.
- Keep public examples and docs in sync with actual environment variable names,
  artifact filenames, and command-line options.
