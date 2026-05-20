from __future__ import annotations

import time
from pathlib import Path

from skillmash.representation import (
    ArtifactSpec,
    ExtractedSkillSchema,
    IONameResolution,
    NormalizationConfig,
    ParameterSpec,
    RepresentationExtractor,
    SkillFolder,
    SkillFolderScanner,
    SkillManifestParser,
    SkillRepresentationNormalizer,
    schema_from_llm_payload,
)


class RecordingIONameResolver:
    def __init__(self, action: str = "create_new") -> None:
        self.action = action
        self.resolve_calls = 0
        self.resolve_many_calls = 0
        self.batches = []

    def resolve(self, candidate, vocabulary):
        self.resolve_calls += 1
        return IONameResolution(
            action=self.action,
            normalized_value=None if self.action == "exclude_non_runtime" else candidate.token,
            confidence=0.7,
            reason="single fallback",
        )

    def resolve_many(self, candidates, vocabulary):
        self.resolve_many_calls += 1
        self.batches.append([candidate.token for candidate in candidates])
        return {
            candidate.token: IONameResolution(
                action=self.action,
                normalized_value=None if self.action == "exclude_non_runtime" else candidate.token,
                confidence=0.9,
                reason="batch",
            )
            for candidate in candidates
        }


def test_scanner_finds_skill_folders_in_stable_order(tmp_path: Path) -> None:
    (tmp_path / "b-skill").mkdir()
    (tmp_path / "b-skill" / "SKILL.md").write_text("# B", encoding="utf-8")
    (tmp_path / "a-skill").mkdir()
    (tmp_path / "a-skill" / "SKILL.md").write_text("# A", encoding="utf-8")

    folders = SkillFolderScanner().scan(tmp_path)

    assert [folder.relative_path for folder in folders] == ["a-skill", "b-skill"]


def test_scanner_can_limit_depth_when_called_directly(tmp_path: Path) -> None:
    (tmp_path / "top").mkdir()
    (tmp_path / "top" / "SKILL.md").write_text("# Top", encoding="utf-8")
    (tmp_path / "category" / "nested").mkdir(parents=True)
    (tmp_path / "category" / "nested" / "SKILL.md").write_text("# Nested", encoding="utf-8")

    scanner = SkillFolderScanner()

    assert [folder.relative_path for folder in scanner.scan(tmp_path)] == [
        "category/nested",
        "top",
    ]
    assert [folder.relative_path for folder in scanner.scan(tmp_path, max_depth=1)] == [
        "top",
    ]


def test_manifest_parser_splits_frontmatter_and_body(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    entry = skill_dir / "SKILL.md"
    entry.write_text(
        "---\n"
        "name: Demo Skill\n"
        "description: Demo description\n"
        "allowed-tools: Bash(*), Read\n"
        "---\n"
        "# Demo\n"
        "Body text\n",
        encoding="utf-8",
    )
    folder = SkillFolder("demo", skill_dir, entry, "demo")

    manifest = SkillManifestParser().parse(folder)

    assert manifest.frontmatter["name"] == "Demo Skill"
    assert manifest.frontmatter["allowed-tools"] == "Bash(*), Read"
    assert manifest.body.startswith("# Demo")
    assert len(manifest.body_sha256) == 64


def test_normalizer_normalizes_input_and_output_names_and_types(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(
        description="Search arXiv papers.",
        inputs=[
            ParameterSpec(
                name="Query or Arxiv ID",
                type="natural language query",
            )
        ],
        outputs=[
            ArtifactSpec(
                name="Downloaded PDF",
                type="pdf",
            )
        ],
        confidence=0.86,
    )

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)
    representation = result.representation

    assert representation.id == "aris-arxiv"
    assert representation.inputs[0].name == "query"
    assert representation.inputs[0].type == "text"
    assert representation.outputs[0].name == "paper"
    assert representation.outputs[0].type == "pdf"
    assert representation.inputs[0].to_dict() == {
        "name": "query",
        "type": "text",
        "required": True,
        "description": "",
        "default": None,
        "schema_ref": None,
    }

    name_decisions = [
        decision for decision in result.decisions
        if decision.field == "name"
    ]
    assert name_decisions[0].method == "vocab_alias"
    assert name_decisions[0].token == "query_or_arxiv_id"
    assert name_decisions[1].method == "vocab_alias"
    assert name_decisions[1].token == "downloaded_pdf"


def test_normalizer_uses_shared_io_name_vocab_aliases(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(
        description="Connect output and input ports by vocab term.",
        inputs=[{"name": "Research Topic", "type": "text"}],
        outputs=[{"name": "Short Summary", "type": "summary"}],
    )

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)

    assert result.representation.inputs[0].name == "topic"
    assert result.representation.outputs[0].name == "summary"
    assert result.decisions[0].raw_value == "Research Topic"
    assert result.decisions[2].raw_value == "Short Summary"


def test_normalizer_normalizes_tasks_with_dynamic_vocab(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(
        description="Find papers and create a short summary.",
        tasks=["Research", "Summarisation", "Custom Ranking"],
        inputs=[{"name": "Research Topic", "type": "text"}],
        outputs=[{"name": "Short Summary", "type": "markdown"}],
    )

    normalizer = SkillRepresentationNormalizer()
    result = normalizer.normalize(manifest, extracted)

    assert result.representation.tasks == ["search", "summarize", "custom_ranking"]
    assert normalizer.task_vocabulary.lookup("custom_ranking") == "custom_ranking"
    task_decisions = [
        decision for decision in result.decisions
        if decision.vocab == "task_vocab"
    ]
    assert [decision.method for decision in task_decisions] == [
        "vocab_alias",
        "vocab_alias",
        "create_new",
    ]


def test_normalization_config_exposes_io_name_vocab_size_limit() -> None:
    config = NormalizationConfig()

    assert config.max_vocab_size == 8
    assert not hasattr(config, "max_canonical_names")


def test_normalizer_creates_defaults_and_diagnostics(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(description="Unknown result skill.")

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)

    assert result.representation.inputs[0].name == "input"
    assert result.representation.inputs[0].type == "text"
    assert result.representation.outputs[0].name == "result"
    assert result.representation.outputs[0].type == "unknown"
    assert result.decisions[0].method == "default"
    assert result.decisions[3].method == "default_unknown"
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "default_input_created",
        "unknown_output_created",
    }


def test_normalizer_adds_new_io_name_to_vocab_when_capacity_remains(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(
        description="Handle a customer intent.",
        inputs=[{"name": "Customer Intent", "type": "text"}],
        outputs=[{"name": "Downloaded PDF", "type": "pdf"}],
    )

    normalizer = SkillRepresentationNormalizer()
    result = normalizer.normalize(manifest, extracted)

    assert result.representation.inputs[0].name == "customer_intent"
    assert normalizer.io_name_vocabulary.lookup("customer_intent") == "customer_intent"
    assert result.decisions[0].method == "create_new"


def test_normalizer_batches_unseen_io_names_per_skill(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    resolver = RecordingIONameResolver()
    extracted = ExtractedSkillSchema(
        description="Plan a custom route.",
        inputs=[
            {"name": "Customer Intent", "type": "text"},
            {"name": "Operator Secret", "type": "text"},
        ],
        outputs=[
            {"name": "Routing Manifest", "type": "json"},
            {"name": "Customer Intent", "type": "text"},
        ],
    )

    result = SkillRepresentationNormalizer(
        config=NormalizationConfig(max_vocab_size=32),
        io_name_resolver=resolver,
    ).normalize(
        manifest,
        extracted,
    )

    assert resolver.resolve_many_calls == 1
    assert resolver.resolve_calls == 0
    assert resolver.batches == [
        ["customer_intent", "operator_secret", "routing_manifest"]
    ]
    assert [item.name for item in result.representation.inputs] == [
        "customer_intent",
        "operator_secret",
    ]
    assert [item.name for item in result.representation.outputs] == [
        "routing_manifest",
        "customer_intent",
    ]


def test_normalizer_caches_unseen_io_name_resolutions(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    resolver = RecordingIONameResolver(action="exclude_non_runtime")
    normalizer = SkillRepresentationNormalizer(io_name_resolver=resolver)
    extracted = ExtractedSkillSchema(
        description="Emit debug details.",
        inputs=[{"name": "Debug Payload", "type": "json"}],
        outputs=[],
    )

    first = normalizer.normalize(_manifest(first_root), extracted)
    second = normalizer.normalize(_manifest(second_root), extracted)

    assert resolver.resolve_many_calls == 1
    assert resolver.resolve_calls == 0
    assert first.representation.inputs == []
    assert second.representation.inputs == []
    assert any(
        decision.method == "exclude_non_runtime"
        and decision.token == "debug_payload"
        for decision in second.decisions
    )


def test_normalizer_merges_new_io_name_when_vocab_is_full(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    config = NormalizationConfig(
        max_vocab_size=1,
        io_name_aliases={"search_query": "query"},
    )
    extracted = ExtractedSkillSchema(
        description="Force merge a new name.",
        inputs=[{"name": "Travel Request Text", "type": "text"}],
        outputs=[{"name": "Search Query", "type": "text"}],
    )

    result = SkillRepresentationNormalizer(config).normalize(manifest, extracted)

    assert result.representation.inputs[0].name == "query"
    assert result.decisions[0].method == "merge_existing"
    assert result.decisions[0].details["forced_merge"] is True


def test_normalizer_excludes_non_runtime_io_names(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(
        description="Search flights.",
        inputs=[
            {"name": "Query", "type": "text"},
            {
                "name": "Origin Query",
                "type": "text",
                "description": "用户原始查询内容，用于统计",
            },
        ],
        outputs=[{"name": "Search Query", "type": "text"}],
    )

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)

    assert [item.name for item in result.representation.inputs] == ["query"]
    assert any(
        decision.method == "exclude_non_runtime"
        and decision.token == "origin_query"
        for decision in result.decisions
    )


def test_normalizer_merges_duplicate_inputs_after_name_normalization(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(
        description="Get weather.",
        inputs=[
            {
                "name": "Query",
                "type": "text",
                "required": True,
                "description": "City or region name.",
            },
            {
                "name": "Search Query",
                "type": "natural language query",
                "required": False,
                "description": "Forecast days, optional 1-7 days.",
                "default": 1,
            },
        ],
        outputs=[{"name": "Short Summary", "type": "text"}],
    )

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)
    inputs = result.representation.inputs

    assert len(inputs) == 1
    assert inputs[0].name == "query"
    assert inputs[0].type == "text"
    assert inputs[0].required is True
    assert inputs[0].default == 1
    assert inputs[0].description == (
        "City or region name. Forecast days, optional 1-7 days."
    )
    assert any(
        diagnostic.code == "duplicate_input_merged"
        and diagnostic.details["name"] == "query"
        for diagnostic in result.diagnostics
    )


def test_normalizer_merges_duplicate_outputs_after_name_normalization(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(
        description="Summarize.",
        inputs=[{"name": "Research Topic", "type": "text"}],
        outputs=[
            {
                "name": "Short Summary",
                "type": "markdown",
                "description": "Markdown summary.",
            },
            {
                "name": "Final Answer",
                "type": "text",
                "description": "Plain text answer.",
            },
        ],
    )

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)
    outputs = result.representation.outputs

    assert len(outputs) == 1
    assert outputs[0].name == "summary"
    assert outputs[0].type == "markdown"
    assert outputs[0].description == "Markdown summary. Plain text answer."
    assert any(
        diagnostic.code == "duplicate_output_merged"
        and diagnostic.details["type_conflict"] is True
        for diagnostic in result.diagnostics
    )


def test_schema_from_llm_payload_keeps_candidate_names_for_normalizer() -> None:
    schema = schema_from_llm_payload(
        {
            "description": "Search arXiv papers.",
            "tasks": ["research"],
            "inputs": [
                {
                    "name": "Query or Arxiv ID",
                    "type": "natural language query",
                    "required": True,
                }
            ],
            "outputs": [
                {
                    "name": "Downloaded PDF",
                    "type": "pdf",
                }
            ],
            "constraints": [],
            "confidence": 0.9,
            "warnings": [],
        }
    )

    assert schema.inputs[0].name == "Query or Arxiv ID"
    assert schema.tasks == ["research"]
    assert schema.outputs[0].type == "pdf"
    assert schema.confidence == 0.9


def test_schema_from_llm_payload_combines_legacy_format_into_type() -> None:
    schema = schema_from_llm_payload(
        {
            "description": "Download a paper.",
            "inputs": [],
            "outputs": [
                {
                    "name": "Downloaded PDF",
                    "type": "paper",
                    "format": "pdf",
                }
            ],
        }
    )

    assert schema.outputs[0].type == "pdf"


def test_representation_extractor_accepts_pluggable_schema_extractor(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo Skill\n---\n# Demo\n",
        encoding="utf-8",
    )

    class FakeSchemaExtractor:
        def extract(self, manifest):
            return ExtractedSkillSchema(
                description="Create a short summary.",
                tasks=["Summarization"],
                inputs=[{"name": "Research Topic", "type": "text"}],
                outputs=[{"name": "Short Summary", "type": "markdown"}],
            )

    result = RepresentationExtractor(FakeSchemaExtractor()).extract_all(tmp_path)

    assert len(result.representations) == 1
    representation = result.representations[0]
    assert representation.id == "demo-skill"
    assert representation.tasks == ["summarize"]
    assert representation.inputs[0].name == "topic"
    assert representation.outputs[0].name == "summary"
    assert result.diagnostics == []
    assert result.io_name_vocab["version"] == "io-name-vocab-v1"
    assert result.task_vocab["version"] == "task-vocab-v1"
    assert any(term["name"] == "topic" for term in result.io_name_vocab["terms"])
    assert any(term["name"] == "summarize" for term in result.task_vocab["terms"])


def test_representation_extractor_keeps_scan_order_with_workers(tmp_path: Path) -> None:
    for name in ["a-skill", "b-skill", "c-skill"]:
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\n---\n# {name}\n",
            encoding="utf-8",
        )

    class SlowSchemaExtractor:
        def extract(self, manifest):
            if manifest.folder.relative_path == "a-skill":
                time.sleep(0.03)
            return ExtractedSkillSchema(
                description="Create a short summary.",
                inputs=[{"name": "Research Topic", "type": "text"}],
                outputs=[{"name": "Short Summary", "type": "markdown"}],
            )

    result = RepresentationExtractor(SlowSchemaExtractor(), max_workers=3).extract_all(tmp_path)

    assert [representation.id for representation in result.representations] == [
        "a-skill",
        "b-skill",
        "c-skill",
    ]


def test_representation_extractor_uses_batched_schema_extractor(tmp_path: Path) -> None:
    for name in ["a-skill", "b-skill", "c-skill"]:
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\n---\n# {name}\n",
            encoding="utf-8",
        )

    class BatchConfig:
        batch_size = 2

    class BatchSchemaExtractor:
        use_batch = True
        config = BatchConfig()

        def __init__(self):
            self.extract_calls = 0
            self.batches = []

        def extract(self, manifest):
            self.extract_calls += 1
            raise AssertionError("single extract should not be used")

        def extract_many(self, manifests):
            self.batches.append(
                [manifest.folder.relative_path for manifest in manifests]
            )
            return [
                ExtractedSkillSchema(
                    description="Create a short summary.",
                    inputs=[{"name": "Research Topic", "type": "text"}],
                    outputs=[{"name": "Short Summary", "type": "markdown"}],
                )
                for _manifest in manifests
            ]

    schema_extractor = BatchSchemaExtractor()
    result = RepresentationExtractor(schema_extractor).extract_all(tmp_path)

    assert schema_extractor.extract_calls == 0
    assert schema_extractor.batches == [["a-skill", "b-skill"], ["c-skill"]]
    assert [representation.id for representation in result.representations] == [
        "a-skill",
        "b-skill",
        "c-skill",
    ]


def _manifest(tmp_path: Path):
    skill_dir = tmp_path / "aris-arxiv"
    skill_dir.mkdir()
    entry = skill_dir / "SKILL.md"
    entry.write_text("---\nname: Aris Arxiv\n---\n# Aris\n", encoding="utf-8")
    folder = SkillFolder("aris-arxiv", skill_dir, entry, "aris-arxiv")
    return SkillManifestParser().parse(folder)
