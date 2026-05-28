from __future__ import annotations

import json
from pathlib import Path

from skillmash.orchestration.artifacts import BuildArtifacts
from skillmash.orchestration.planning.grounding import ground_query


class FakeGroundingClient:
    def complete_json(self, **kwargs):
        return json.dumps(
            {
                "available_artifacts": [],
                "inferred_inputs": [],
                "goal_terms": ["content", "image", "result", "text", "translated"],
            }
        )


def test_ground_query_preserves_chinese_image_translation_email_intent() -> None:
    grounded = ground_query(
        query="翻译图中的英文，写成一篇文章发邮件给王总",
        artifacts=BuildArtifacts(
            build_dir=Path("."),
            manifest={},
            skills=[
                {
                    "id": "xiaoyi-image-translation",
                    "inputs": [
                        {
                            "name": "target_language",
                            "type": "text",
                            "required": True,
                        }
                    ],
                    "outputs": [
                        {"name": "translated_text", "type": "json"},
                    ],
                },
                {
                    "id": "imap-smtp-email",
                    "inputs": [
                        {"name": "command", "type": "text", "required": True},
                    ],
                    "outputs": [{"name": "result", "type": "text"}],
                },
            ],
            graph={},
            index={
                "by_output": {
                    "translated_text": ["xiaoyi-image-translation"],
                    "result": ["imap-smtp-email"],
                },
                "by_text_term": {
                    "翻译": ["xiaoyi-image-translation"],
                    "图中": ["xiaoyi-image-translation"],
                    "邮件": ["imap-smtp-email"],
                },
            },
        ),
        llm_client=FakeGroundingClient(),
    )

    assert {"翻译", "图中", "英文", "邮件"} <= grounded.query_terms
    assert {"翻译", "图中", "邮件", "translated"} <= grounded.goal_terms
    assert {
        (item.skill_id, item.name, item.type, item.value)
        for item in grounded.inferred_inputs
    } == {
        ("xiaoyi-image-translation", "target_language", "text", "zh-CHS"),
        ("imap-smtp-email", "command", "text", "send"),
    }
