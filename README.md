# SkillMash

Extract Agent Skill representations. Build Skill networks. Power intelligent orchestration.

SkillMash is being rebuilt around a staged architecture. The current working stage is **Skill representation extraction**: scan folder-based Skills, parse `SKILL.md`, call an LLM to extract a candidate schema, normalize input/output names and data representation types, then write structured representation artifacts.

## Current Focus

The implemented path is:

```text
skills_root/
  some-skill/
    SKILL.md
  another-skill/
    SKILL.md

-> skillmash.representation
-> representations.json
-> diagnostics.json
-> normalization_decisions.json
-> io_name_vocab.json
-> task_vocab.json
-> extraction.log
```

The first version prioritizes:

- stable `SkillRepresentation` data contracts
- folder scanning and `SKILL.md` frontmatter parsing
- OpenAI-compatible LLM extraction
- dynamic input/output `name` normalization through `io_name_vocab`
- dynamic Skill capability normalization through `task_vocab`
- shared input/output data representation `type` normalization
- structured diagnostics, progress, and logs

## Environment

Create a `.env` file from [.env.example](.env.example):

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
```

`OPENAI_API_KEY` and `OPENAI_MODEL` are required. `OPENAI_BASE_URL` can point to any OpenAI-compatible provider.

Optional overrides:

```env
SKILLMASH_LLM_API_KEY=your_api_key_here
SKILLMASH_LLM_MODEL=gpt-4.1-mini
SKILLMASH_LLM_BASE_URL=https://api.openai.com/v1
SKILLMASH_LLM_TEMPERATURE=0
SKILLMASH_LLM_TIMEOUT_SECONDS=60
```

## Run Representation Extraction

PowerShell:

```powershell
.\.venv\Scripts\python.exe examples\representation_extraction_demo.py --skills_root C:\Users\admin\Documents\data\skills --out_dir OUTPUT
```

Use concurrent LLM calls to speed up larger Skill folders:

```powershell
.\.venv\Scripts\python.exe examples\representation_extraction_demo.py --skills_root C:\Users\admin\Documents\data\skills --out_dir OUTPUT --workers 8
```

By default, unseen `io_name_vocab` terms are resolved with the LLM. To use the local heuristic resolver instead, add:

```powershell
.\.venv\Scripts\python.exe examples\representation_extraction_demo.py --skills_root C:\Users\admin\Documents\data\skills --out_dir OUTPUT --heuristic_vocab_resolver
```

Positional arguments also work:

```powershell
.\.venv\Scripts\python.exe examples\representation_extraction_demo.py C:\Users\admin\Documents\data\skills OUTPUT
```

Linux:

```bash
python examples/representation_extraction_demo.py --skills_root /data/xiaoyu/data/skills/20260325 --out_dir /data/xiaoyu/code/SkillMash/OUTPUT --workers 8
```

The command writes:

```text
OUTPUT/
  representations.json
  diagnostics.json
  normalization_decisions.json
  io_name_vocab.json
  task_vocab.json
  extraction.log
```

Progress is printed to `stderr` as a small progress bar. A JSON summary is printed to `stdout` when extraction finishes.

## Output Shape

`representations.json` contains normalized Skill records:

```json
{
  "representations": [
    {
      "id": "aris-arxiv",
      "name": "Aris Arxiv",
      "description": "Search, download, and summarize academic papers from arXiv.",
      "version": "1.0.0",
      "tasks": ["search", "summarize"],
      "inputs": [
        {
          "name": "query",
          "type": "text",
          "required": true,
          "description": "Search query or arXiv identifier.",
          "default": null,
          "schema_ref": null
        }
      ],
      "outputs": [
        {
          "name": "paper",
          "type": "pdf",
          "description": "Downloaded paper PDF.",
          "schema_ref": null
        }
      ],
      "preconditions": [],
      "postconditions": []
    }
  ]
}
```

Examples of normalization:

```text
Query or Arxiv ID        -> query
Downloaded PDF          -> paper
natural language query  -> text
pdf                     -> pdf
```

Normalization decisions are written separately to `normalization_decisions.json` so graph-facing representations stay compact and stable. The final dynamic input/output name vocabulary is written to `io_name_vocab.json`, and the final dynamic task vocabulary is written to `task_vocab.json`.

When a new I/O name is not already in `io_name_vocab`, the resolver chooses one of:

```text
alias_existing        add the new spelling as an alias of an existing term
create_new            add a new vocab term while capacity remains
merge_existing        force-merge into an existing term when the vocab is full
exclude_non_runtime   drop logging/statistics/telemetry/original-copy fields
```

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .pytest-tmp
```

The explicit `--basetemp` avoids Windows temp-directory permission issues seen in this workspace.

## Structure

```text
skillmash/
  representation/
    base_vocab.py    shared vocabulary infrastructure (base classes, constants, utilities)
    models.py        representation data contracts
    scanner.py       finds folders containing SKILL.md
    manifest.py      parses SKILL.md frontmatter and body
    extractor.py     OpenAI-compatible LLM schema extractor
    io_name_vocab.py dynamic input/output name vocabulary (extends base_vocab)
    semantic_vocab.py dynamic task/capability vocabulary (extends base_vocab)
    llm.py           shared OpenAI-compatible client helpers
    normalizer.py    deterministic schema normalization
    pipeline.py      scan -> parse -> extract -> normalize orchestration
    utils.py         shared text normalization helpers
    writer.py        writes extraction JSON artifacts

examples/
  representation_extraction_demo.py

docs/
  skill-orchestration-system-design.md
  modules/
    offline-representation-extraction.md
    offline-graph-construction.md
    online-orchestration-retrieval.md
    online-pruning-ranking.md
    online-execution.md

tests/
  test_representation.py
```

## Design Notes

The representation module is intentionally independent of graph construction and online orchestration. It produces stable `SkillRepresentation` records and compact dynamic vocabularies for graph-facing semantic fields; later stages should consume those records instead of rereading `SKILL.md`.
