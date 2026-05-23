"""Apply relation feedback to offline graph artifacts.

Usage:
  .venv/bin/python tools/apply_relation_feedback.py --build_dir OUTPUT/build
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from skillmash.graph.feedback import (
    apply_feedback_to_graph_payload,
    read_feedback_aggregates,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply relation feedback to graph edge confidence values."
    )
    parser.add_argument(
        "--build_dir",
        required=True,
        help="Directory containing build artifacts.",
    )
    parser.add_argument(
        "--feedback_path",
        default=".skillmash/runtime/relation_feedback.jsonl",
        help="Feedback JSONL path.",
    )
    parser.add_argument(
        "--window_days",
        type=int,
        default=30,
        help="Rolling feedback window size in days.",
    )
    parser.add_argument(
        "--min_count",
        type=int,
        default=20,
        help="Minimum failed count to trigger degradation.",
    )
    parser.add_argument(
        "--min_fail_rate",
        type=float,
        default=0.6,
        help="Minimum fail rate to trigger degradation.",
    )
    parser.add_argument(
        "--degrade_step",
        type=float,
        default=0.1,
        help="Confidence decrease applied per trigger.",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Print adjustments without writing files.",
    )
    args = parser.parse_args()

    build_dir = Path(args.build_dir).resolve()
    graph_path = build_dir / "skill_graph.json"
    if not graph_path.exists():
        raise FileNotFoundError(f"Missing graph artifact: {graph_path}")
    graph_payload = json.loads(graph_path.read_text(encoding="utf-8"))

    aggregates = read_feedback_aggregates(
        args.feedback_path,
        window_days=args.window_days,
    )
    updated_graph, adjustments = apply_feedback_to_graph_payload(
        graph_payload,
        aggregates,
        min_count=args.min_count,
        min_fail_rate=args.min_fail_rate,
        degrade_step=args.degrade_step,
    )

    if args.dry_run:
        print(json.dumps({"adjustments": adjustments}, ensure_ascii=False, indent=2))
        return

    graph_path.write_text(
        json.dumps(updated_graph, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"updated_edges": len(adjustments), "graph_path": str(graph_path)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
