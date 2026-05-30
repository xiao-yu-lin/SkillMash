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
LLM_API_KEY=your_api_key_here
LLM_MODEL=gpt-4.1-mini
LLM_BASE_URL=https://api.openai.com/v1
```

`LLM_API_KEY` and `LLM_MODEL` are required. `LLM_BASE_URL` can point to any OpenAI-compatible provider.

Optional overrides:

```env
LLM_API_KEY=your_api_key_here
LLM_MODEL=gpt-4.1-mini
LLM_BASE_URL=https://api.openai.com/v1
LLM_TEMPERATURE=0
LLM_TIMEOUT_SECONDS=60
```

### vLLM Offline Mode

When `LLM_MODEL` points to an existing local model path, SkillMash uses vLLM in-process for offline inference. To avoid duplicate model loading and redundant GPU memory usage, SkillMash implements a singleton caching mechanism: all components (schema extractor, I/O name resolver, graph matcher) sharing the same model path reuse a single vLLM engine instance.

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

## Run Graph Construction

After representation extraction, build the ontology-driven Skill graph from `representations.json`:

```powershell
.\.venv\Scripts\python.exe examples\graph_build_demo.py --representations_json OUTPUT\representations.json --out_dir .skillmash\index
```

Use concurrent LLM matching calls for larger candidate sets:

```powershell
.\.venv\Scripts\python.exe examples\graph_build_demo.py --representations_json OUTPUT\representations.json --out_dir .skillmash\index --batch_size 12 --workers 4
```

By default, each candidate batch is sent to the LLM twice with the Skill order swapped. A relation is accepted only when both runs agree. To disable this precision-oriented consensus check:

```powershell
.\.venv\Scripts\python.exe examples\graph_build_demo.py --representations_json OUTPUT\representations.json --out_dir .skillmash\index --no_consensus
```

Confidence thresholds default to `0` so all schema-valid consensus matches are kept. Override them when building if needed:

```powershell
.\.venv\Scripts\python.exe examples\graph_build_demo.py --representations_json OUTPUT\representations.json --out_dir .skillmash\index --can_feed_threshold 0.7
```

The graph builder first generates deterministic relation candidates from normalized `description`, `tasks`, `inputs`, and `outputs`, then asks the LLM to validate those candidates. It writes:

Progress is shown with Rich.

```text
.skillmash/index/
  build_manifest.json
  skills.json
  skill_graph.json
  skill_index.json
  llm_matches.json
  diagnostics.json
```

`skill_graph.json` is a Skill-only relation graph. Each Skill node keeps `description`, `inputs`, and `outputs` as properties, and edges are accepted `can_feed` relations. `llm_matches.json` keeps both candidates and LLM judgments for traceability.

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
Query or Arxiv ID        -> query_or_arxiv_id by local heuristic, or query if the LLM resolver chooses that term
Downloaded PDF          -> downloaded_pdf by local heuristic, or paper if the LLM resolver chooses that term
natural language query  -> text
pdf                     -> pdf
```

Normalization decisions are written separately to `normalization_decisions.json` so graph-facing representations stay compact and stable. The final dynamic input/output name vocabulary is written to `io_name_vocab.json`, and the final dynamic task vocabulary is written to `task_vocab.json`.

The built-in `io_name_vocab` starts without seed aliases by default. When a new I/O name is not already in `io_name_vocab`, the resolver chooses one of:

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

The `representation` module follows the staged pipeline architecture from the design specification:

```text
skillmash/
  common/
    llm.py           shared OpenAI-compatible client helpers
  
  representation/
    models.py        representation data contracts (SkillRepresentation, ParameterSpec, etc.)
    pipeline.py      scan -> parse -> extract -> normalize -> write orchestration
    utils.py         shared text normalization helpers
    
    scan/            Stage 1: Discover Skill folders containing SKILL.md
      scanner.py     finds folders containing SKILL.md entrypoint
    
    parse/           Stage 2: Parse SKILL.md into frontmatter and body
      parser.py      parses SKILL.md frontmatter and body
    
    extract/         Stage 3: LLM schema extraction from parsed content
      extractor.py   OpenAI-compatible LLM schema extractor
    
    normalize/       Stage 4: Normalize I/O names, types, and identities
      base_vocab.py      shared vocabulary infrastructure (base classes, constants, utilities)
      io_name_vocab.py   dynamic input/output name vocabulary (extends base_vocab)
      semantic_vocab.py  dynamic task/capability vocabulary (extends base_vocab)
      normalizer.py      deterministic schema normalization
    
    write/           Stage 5: Write extraction artifacts to disk
      writer.py      writes extraction JSON artifacts

examples/
  representation_extraction_demo.py
  graph_build_demo.py
  graph_online_demo.py

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
  test_graph.py
  test_orchestration_planner.py
```

## Design Notes

The representation module is intentionally independent of graph construction and online orchestration. It produces stable `SkillRepresentation` records and compact dynamic vocabularies for graph-facing semantic fields; later stages should consume those records instead of rereading `SKILL.md`.

### Type Compatibility Rules

The graph candidate generation in `skillmash/graph/candidates.py` uses type compatibility rules to determine whether one Skill's output can feed another Skill's input. The rules are:

1. **Exact type match**: `output.type == input.type` (e.g., `audio -> audio`, `text -> text`)
2. **Compatible type match**: defined in `COMPATIBLE_CAN_FEED_TYPES` constant

Current compatible type pairs:

```python
COMPATIBLE_CAN_FEED_TYPES = frozenset(
    {
        ("markdown", "text"),   # Markdown content is text
        ("audio", "file"),      # Audio files are file types
        ("video", "file"),      # Video files are file types
        ("image", "file"),      # Image files are file types
        ("pdf", "file"),        # PDF files are file types
        ("path", "file"),       # Paths can point to files
    }
)
```

These rules enable important workflow connections, such as:
- `tts.audio` output → `speech-to-text.file` input (TTS audio can be transcribed)
- `voice-synthesis.audio` output → `tts.ref_audio` input (synthesized audio can be used for voice cloning)

### Textual Coercion for Semantic Matching

When exact type match and compatible type match are insufficient, the candidate generator applies **textual coercion** rules to identify semantic connections between Skills with text-based inputs and outputs:

**Supported type combinations:**
- `(markdown, text)` - Markdown output can feed text input
- `(text, text)` - Text output can feed text input (for generic text inputs)

**Conditions for textual coercion:**

1. **Generic text input**: The target input name must be in `GENERIC_TEXT_INPUT_NAMES`:
   ```python
   GENERIC_TEXT_INPUT_NAMES = frozenset({
       "body", "content", "prompt", "query", "question",
       "request", "script", "text", "transcript",
   })
   ```

2. **Textual output**: The source output name must be in `TEXTUAL_OUTPUT_TERMS`:
   ```python
   TEXTUAL_OUTPUT_TERMS = frozenset({
       "article", "brief", "content", "draft", "notes",
       "report", "review", "script", "summary", "transcript",
   })
   ```

These rules enable semantic workflow connections such as:
- `speech-to-text.transcript` → `general-writing.query` (transcription can be used as writing input)
- `read-arxiv-paper.summary` → `ai-ppt-generator.topic` (paper summary can become PPT topic)
