"""Apply runtime relation feedback to offline graph edges."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FeedbackAggregate:
    source_skill: str
    target_skill: str
    relation_type: str
    failed_count: int
    total_count: int

    @property
    def fail_rate(self) -> float:
        if self.total_count <= 0:
            return 1.0
        return self.failed_count / self.total_count


def read_feedback_aggregates(
    feedback_path: str | Path,
    *,
    window_days: int = 30,
    now: datetime | None = None,
) -> dict[tuple[str, str, str], FeedbackAggregate]:
    path = Path(feedback_path)
    if not path.exists():
        return {}
    current = now or datetime.now(timezone.utc)
    window_start = current - timedelta(days=max(1, int(window_days)))
    sums: dict[tuple[str, str, str], dict[str, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        timestamp = _parse_timestamp(record.get("timestamp"))
        if timestamp is None or timestamp < window_start:
            continue
        source_skill = str(record.get("source_skill") or "")
        target_skill = str(record.get("target_skill") or "")
        relation_type = str(record.get("relation_type") or "")
        if not source_skill or not target_skill or not relation_type:
            continue
        count = _as_count(record.get("count"))
        total_count = _as_count(record.get("total_count"), default=count)
        key = (source_skill, target_skill, relation_type)
        bucket = sums.setdefault(key, {"failed": 0, "total": 0})
        bucket["failed"] += count
        bucket["total"] += total_count
    output: dict[tuple[str, str, str], FeedbackAggregate] = {}
    for key, bucket in sums.items():
        output[key] = FeedbackAggregate(
            source_skill=key[0],
            target_skill=key[1],
            relation_type=key[2],
            failed_count=bucket["failed"],
            total_count=bucket["total"],
        )
    return output


def apply_feedback_to_graph_payload(
    graph_payload: dict[str, Any],
    aggregates: dict[tuple[str, str, str], FeedbackAggregate],
    *,
    min_count: int = 20,
    min_fail_rate: float = 0.6,
    degrade_step: float = 0.1,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    edges = list(graph_payload.get("edges", []))
    updated_edges = []
    adjustments: list[dict[str, Any]] = []
    for edge in edges:
        current = dict(edge)
        source_skill = _edge_skill_id(current.get("source"))
        target_skill = _edge_skill_id(current.get("target"))
        relation_type = str(current.get("type") or "")
        aggregate = aggregates.get((source_skill, target_skill, relation_type))
        if aggregate is None:
            updated_edges.append(current)
            continue
        if aggregate.failed_count < max(1, int(min_count)):
            updated_edges.append(current)
            continue
        if aggregate.fail_rate < float(min_fail_rate):
            updated_edges.append(current)
            continue
        old_confidence = float(current.get("confidence") or 0.0)
        new_confidence = max(0.0, old_confidence - float(degrade_step))
        current["confidence"] = round(new_confidence, 6)
        evidence = current.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        feedback_meta = evidence.get("feedback")
        if not isinstance(feedback_meta, dict):
            feedback_meta = {}
        previous_epoch = int(feedback_meta.get("degrade_epoch") or 0)
        feedback_meta["degrade_epoch"] = previous_epoch + 1
        feedback_meta["last_degrade_at"] = datetime.now(timezone.utc).isoformat()
        feedback_meta["failed_count"] = aggregate.failed_count
        feedback_meta["total_count"] = aggregate.total_count
        feedback_meta["fail_rate"] = round(aggregate.fail_rate, 6)
        evidence["feedback"] = feedback_meta
        current["evidence"] = evidence
        adjustments.append(
            {
                "source_skill": source_skill,
                "target_skill": target_skill,
                "relation_type": relation_type,
                "old_confidence": old_confidence,
                "new_confidence": new_confidence,
                "degrade_epoch": feedback_meta["degrade_epoch"],
            }
        )
        updated_edges.append(current)
    updated = dict(graph_payload)
    updated["edges"] = updated_edges
    return updated, adjustments


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_count(value: Any, *, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, parsed)


def _edge_skill_id(value: Any) -> str:
    text = str(value or "")
    return text.removeprefix("skill:")
