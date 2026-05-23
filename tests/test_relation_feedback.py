from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skillmash.graph.feedback import (
    apply_feedback_to_graph_payload,
    read_feedback_aggregates,
)


def test_read_feedback_aggregates_filters_by_window(tmp_path: Path) -> None:
    path = tmp_path / "feedback.jsonl"
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    old = (now - timedelta(days=40)).isoformat()
    fresh = (now - timedelta(days=1)).isoformat()
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": old,
                        "source_skill": "a",
                        "target_skill": "b",
                        "relation_type": "substitute_for",
                        "count": 5,
                        "total_count": 10,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": fresh,
                        "source_skill": "a",
                        "target_skill": "b",
                        "relation_type": "substitute_for",
                        "count": 7,
                        "total_count": 10,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    aggregates = read_feedback_aggregates(path, window_days=30, now=now)
    agg = aggregates[("a", "b", "substitute_for")]
    assert agg.failed_count == 7
    assert agg.total_count == 10


def test_apply_feedback_degrades_confidence_and_tracks_epoch() -> None:
    graph = {
        "nodes": [],
        "edges": [
            {
                "source": "skill:a",
                "target": "skill:b",
                "type": "substitute_for",
                "confidence": 0.8,
                "method": "llm",
                "evidence": {},
            }
        ],
    }
    aggregates = {
        ("a", "b", "substitute_for"): type(
            "_Agg",
            (),
            {"failed_count": 25, "total_count": 30, "fail_rate": 25 / 30},
        )()
    }
    updated, adjustments = apply_feedback_to_graph_payload(
        graph,
        aggregates,
        min_count=20,
        min_fail_rate=0.6,
        degrade_step=0.1,
    )

    edge = updated["edges"][0]
    assert edge["confidence"] == 0.7
    assert edge["evidence"]["feedback"]["degrade_epoch"] == 1
    assert adjustments[0]["new_confidence"] == pytest.approx(0.7)
