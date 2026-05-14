# SkillMash

Extract Agent Skill representations. Build Skill networks. Power intelligent orchestration.

SkillMash is a first-pass prototype for a Skill orchestration system:

- register atomic, composite, and wrapped skills
- build a typed Skill graph
- decompose coarse skills into atomic skills
- match composable skills through input/output compatibility
- plan execution paths from a user task
- inspect the system through a decoupled Python UI

## Quick Start

Run tests:

```powershell
uv run python -m pytest
```

Run the demo planner:

```powershell
uv run python skillmash_demo.py
```

Run the UI:

```powershell
uv run python run_ui.py --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

## Offline Build / Online Planning

Build an offline index from folder-based Skills:

```powershell
$env:OPENAI_API_KEY="..."
uv run python build.py --skills-root C:\Users\admin\Documents\data\skills --out .skillmash\index
```

The offline builder uses an LLM to extract Skill inputs, outputs, skill tags, and data tags. Progress is printed to `stderr`, and the final build artifact summary is printed as JSON to `stdout`.

```powershell
uv run python build.py --skills-root C:\Users\admin\Documents\data\skills --out .skillmash\index --llm-model gpt-4.1-mini
```

Run the online planning service from that build artifact:

```powershell
uv run python service.py --index .skillmash\index --host 127.0.0.1 --port 8765
```

Run the visualization UI for the build artifact:

```powershell
uv run python ui.py --index .skillmash\index --host 127.0.0.1 --port 8765
```

## Structure

```text
skillmash/
  core/
    models.py        core data models
    registry.py      skill registration and lookup
    graph.py         typed Skill graph
    decomposer.py    atomic skill decomposition
    matcher.py       composition compatibility checks
    planner.py       goal inference and execution planning
    scoring.py       candidate plan scoring
    serialization.py build artifact serialization
  build/
    extraction.py    LLM schema extractor for Skill IO/tags
    offline.py       folder scanning and offline build
  runtime/
    online.py        build artifact loading and retrieval
    app_service.py   UI/API-facing facade
  interfaces/
    api.py           FastAPI app
    ui_server.py     lightweight demo UI server
  samples/
    examples.py      sample registry for demos and tests
docs/
  skill-orchestration-system-design.md
  skill-orchestration-n-plus-one-views.md
tests/
  test_core.py
```
