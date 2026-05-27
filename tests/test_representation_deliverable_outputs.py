from __future__ import annotations

from pathlib import Path

import skillmash.representation.extractor as extractor_module
from skillmash.representation.extractor import schema_from_llm_payload
from skillmash.representation.manifest import SkillManifestParser
from skillmash.representation.models import ExtractedSkillSchema, SkillFolder
from skillmash.representation.normalizer import SkillRepresentationNormalizer


def test_schema_extraction_prompt_prioritizes_user_facing_deliverables() -> None:
    prompt = extractor_module._SYSTEM_PROMPT

    assert "Read the entire SKILL.md" in prompt
    assert "user-facing/downstream deliverables" in prompt
    assert "Do not emit raw API/control fields as outputs" in prompt
    assert "markdown delivery instructions must be represented as markdown outputs" in prompt


def test_schema_from_llm_payload_keeps_raw_output_notes_out_of_outputs() -> None:
    schema = schema_from_llm_payload(
        {
            "description": "Translate text in images.",
            "inputs": [],
            "outputs": [
                {
                    "name": "translated_image_markdown",
                    "type": "markdown",
                    "description": "Markdown rendering of translated image.",
                }
            ],
            "raw_output_notes": [
                "API response includes imageResult, textResult, errorCode, and errorMsg."
            ],
            "warnings": [],
        }
    )

    assert [output.name for output in schema.outputs] == ["translated_image_markdown"]
    assert "imageResult" not in {output.name for output in schema.outputs}
    assert any("imageResult" in warning for warning in schema.warnings)


def test_image_translation_deliverable_outputs_survive_normalization(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        tmp_path,
        body=(
            "# 图片翻译 Skill\n"
            "可同时获取翻译后的图片和文本。\n"
            "记住：图片翻译可同时获取翻译后的图片和文本，请把翻译后的图片以markdown形式发送给用户。"
        ),
    )
    extracted = ExtractedSkillSchema(
        description="Translate text in images and return translated results.",
        inputs=[
            {
                "name": "image_info",
                "type": "json",
                "required": True,
                "description": "Original image reference, supplied as imageId, imageUrl, or imageBase64.",
            },
            {
                "name": "target_language",
                "type": "text",
                "required": True,
                "description": "Target language code for translation.",
            },
        ],
        outputs=[
            {
                "name": "translated_image_markdown",
                "type": "markdown",
                "description": "Markdown rendering of the translated image for sending to the user.",
            },
            {
                "name": "translated_text",
                "type": "text",
                "description": "Translated text extracted from OCR translation results.",
            },
        ],
    )

    result = SkillRepresentationNormalizer().normalize(manifest, extracted)

    assert [(item.name, item.type) for item in result.representation.outputs] == [
        ("translated_image_markdown", "markdown"),
        ("translated_text", "text"),
    ]


def _manifest(tmp_path: Path, *, body: str):
    skill_dir = tmp_path / "xiaoyi-image-translation"
    skill_dir.mkdir()
    entry = skill_dir / "SKILL.md"
    entry.write_text(
        "---\nname: xiaoyi-image-translation\n---\n" + body,
        encoding="utf-8",
    )
    folder = SkillFolder(
        "xiaoyi-image-translation",
        skill_dir,
        entry,
        "xiaoyi-image-translation",
    )
    return SkillManifestParser().parse(folder)
