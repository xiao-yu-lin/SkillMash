# SkillMash

Extract Agent Skill representations. Build Skill networks. Power intelligent orchestration.

SkillMash is being rebuilt around a staged architecture. The current working stage is **Skill representation extraction**: scan folder-based Skills, parse `SKILL.md`, call an LLM to extract a candidate schema, normalize input/output names and artifact types, then write structured representation artifacts.

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
-> extraction.log
```

The first version prioritizes:

- stable `SkillRepresentation` data contracts
- folder scanning and `SKILL.md` frontmatter parsing
- OpenAI-compatible LLM extraction
- deterministic input/output `name` normalization
- shared input/output artifact type normalization
- light `skill_tags` and `data_tags` cleanup
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
      "kind": "wrapped",
      "description": "Search, download, and summarize academic papers from arXiv.",
      "inputs": [
        {
          "name": "query_or_arxiv_id",
          "type": "text",
          "required": true,
          "description": "Search query or arXiv identifier.",
          "default": null,
          "format": null,
          "schema_ref": null,
          "raw": {
            "name": "Query or Arxiv ID",
            "type": "natural language query"
          },
          "normalization": {
            "name_method": "snake_case",
            "type_method": "alias_map",
            "raw_type": "natural language query",
            "normalized_token": "natural_language_query",
            "confidence": 0.95
          }
        }
      ],
      "outputs": [
        {
          "name": "downloaded_pdf",
          "type": "paper",
          "description": "Downloaded paper PDF.",
          "format": "pdf",
          "schema_ref": null,
          "raw": {
            "name": "Downloaded PDF",
            "type": "pdf"
          },
          "normalization": {
            "name_method": "snake_case",
            "type_method": "alias_map",
            "raw_type": "pdf",
            "normalized_token": "pdf",
            "confidence": 0.95
          }
        }
      ],
      "skill_tags": ["paper", "search", "summarize"],
      "data_tags": ["pdf", "writing"],
      "source": {},
      "metadata": {}
    }
  ]
}
```

Examples of normalization:

```text
Query or Arxiv ID        -> query_or_arxiv_id
Downloaded PDF          -> downloaded_pdf
natural language query  -> text
pdf                     -> paper
pdf                     -> format=pdf
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
    models.py       representation data contracts
    scanner.py      finds folders containing SKILL.md
    manifest.py     parses SKILL.md frontmatter and body
    extractor.py    OpenAI-compatible LLM schema extractor
    normalizer.py   deterministic schema normalization
    pipeline.py     scan -> parse -> extract -> normalize orchestration

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

The representation module is intentionally independent of graph construction and online orchestration. It produces stable `SkillRepresentation` records; later stages should consume those records instead of rereading `SKILL.md`.
