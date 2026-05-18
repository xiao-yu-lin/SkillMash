from __future__ import annotations

import time
from pathlib import Path

from skillmash.representation import (
    ArtifactSpec,
    ExtractedSkillSchema,
    ParameterSpec,
    RepresentationExtractor,
    SkillFolder,
    SkillFolderScanner,
    SkillManifestParser,
    SkillRepresentationNormalizer,
    schema_from_llm_payload,
)


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
        skill_tags=["Search", "Paper", "summarize"],
        data_tags=["PDF", "writing"],
        confidence=0.86,
    )

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)
    representation = result.representation

    assert representation.id == "aris-arxiv"
    assert representation.inputs[0].name == "query_or_arxiv_id"
    assert representation.inputs[0].type == "text"
    assert representation.inputs[0].format is None
    assert representation.inputs[0].raw == {
        "name": "Query or Arxiv ID",
        "type": "natural language query",
    }
    assert representation.inputs[0].normalization["type_method"] == "alias_map"
    assert representation.outputs[0].name == "downloaded_pdf"
    assert representation.outputs[0].type == "paper"
    assert representation.outputs[0].format == "pdf"
    assert representation.outputs[0].raw == {
        "name": "Downloaded PDF",
        "type": "pdf",
    }
    assert representation.outputs[0].normalization["raw_type"] == "pdf"
    assert representation.skill_tags == ["paper", "search", "summarize"]
    assert representation.data_tags == ["pdf", "writing"]
    assert representation.quality["extraction_confidence"] == 0.86
    assert representation.metadata["normalizer"]["version"] == "representation-normalizer-v1"


def test_normalizer_creates_defaults_and_diagnostics(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    extracted = ExtractedSkillSchema(description="Unknown result skill.")

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)

    assert result.representation.inputs[0].name == "input"
    assert result.representation.inputs[0].type == "text"
    assert result.representation.outputs[0].name == "result"
    assert result.representation.outputs[0].type == "unknown"
    assert result.representation.inputs[0].raw["name"] == "input"
    assert result.representation.outputs[0].normalization["type_method"] == "default_unknown"
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "default_input_created",
        "unknown_output_created",
    }


def test_schema_from_llm_payload_keeps_candidate_names_for_normalizer() -> None:
    schema = schema_from_llm_payload(
        {
            "description": "Search arXiv papers.",
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
            "skill_tags": ["Search"],
            "data_tags": ["PDF"],
            "constraints": [],
            "confidence": 0.9,
            "warnings": [],
        }
    )

    assert schema.inputs[0].name == "Query or Arxiv ID"
    assert schema.outputs[0].type == "pdf"
    assert schema.confidence == 0.9


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
                inputs=[{"name": "Research Topic", "type": "text"}],
                outputs=[{"name": "Short Summary", "type": "summary"}],
                skill_tags=["Summarize"],
                data_tags=["Text"],
            )

    result = RepresentationExtractor(FakeSchemaExtractor()).extract_all(tmp_path)

    assert len(result.representations) == 1
    representation = result.representations[0]
    assert representation.id == "demo-skill"
    assert representation.inputs[0].name == "research_topic"
    assert representation.outputs[0].name == "short_summary"
    assert result.diagnostics == []


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
                outputs=[{"name": "Short Summary", "type": "summary"}],
            )

    result = RepresentationExtractor(SlowSchemaExtractor(), max_workers=3).extract_all(tmp_path)

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
