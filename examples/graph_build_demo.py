"""Build Skill graph artifacts from normalized representations.

Usage:
    python examples/graph_build_demo.py <representations_json> <out_dir>
    python examples/graph_build_demo.py --representations_json OUTPUT/representations.json --out_dir .skillmash/index

The command writes:
    <out_dir>/build_manifest.json
    <out_dir>/skills.json
    <out_dir>/skill_graph.json
    <out_dir>/skill_index.json
    <out_dir>/llm_matches.json
    <out_dir>/diagnostics.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
except ImportError as exc:
    raise RuntimeError(
        "The rich package is required for graph_build_demo.py. "
        "Install dependencies with `uv sync` or `pip install rich`."
    ) from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skillmash.graph import (  # noqa: E402
    GraphBuilder,
    OpenAICompatibleOntologyMatcher,
    write_graph_build_result,
)
from skillmash.representation import (  # noqa: E402
    ArtifactSpec,
    Condition,
    LLMConfig,
    ParameterSpec,
    SkillRepresentation,
    SlotRef,
)


class ConsoleProgress:
    """Render graph build progress with Rich."""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.console = Console(stderr=True)
        self.progress = None
        self.task_id = None
        self.accepted_count = 0
        self.match_count = 0
        self.diagnostics_count = 0

    def log(self, message: str) -> None:
        elapsed = time.monotonic() - self.started_at
        self.console.log(f"[graph-build +{elapsed:6.1f}s] {message}")

    def llm(self, event: str, current: int, total: int, details: dict[str, Any]) -> None:
        if event == "matching_start":
            message = (
                f"candidates={details.get('candidate_count', 0)} "
                f"batch_size={details.get('batch_size', 0)} "
                f"workers={details.get('max_workers', 1)} "
                f"consensus_runs={details.get('consensus_runs', 1)}"
            )
            self.accepted_count = 0
            self.match_count = 0
            self.diagnostics_count = 0
            self.progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TextColumn("{task.fields[status]}"),
                console=self.console,
                transient=False,
            )
            self.progress.start()
            self.task_id = self.progress.add_task(
                "LLM batches",
                total=total,
                status=message,
            )
        elif event == "batch_start":
            if self.progress is not None and self.task_id is not None:
                self.progress.update(
                    self.task_id,
                    status=f"running {current}/{total}",
                )
        elif event == "batch_done":
            self.match_count += int(details.get("match_count", 0))
            self.accepted_count += int(details.get("accepted_count", 0))
            self.diagnostics_count += int(details.get("diagnostics_count", 0))
            if self.progress is not None and self.task_id is not None:
                self.progress.update(
                    self.task_id,
                    advance=1,
                    status=(
                        f"matches={self.match_count} "
                        f"accepted={self.accepted_count} "
                        f"diag={self.diagnostics_count}"
                    ),
                )
        elif event == "matching_done":
            if self.progress is not None:
                self.progress.stop()
                self.progress = None
                self.task_id = None
            self.log(
                "llm matching done: "
                f"matches={details.get('match_count', 0)} "
                f"accepted={details.get('accepted_count', 0)} "
                f"diagnostics={details.get('diagnostics_count', 0)}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Skill graph artifacts from representations.json."
    )
    parser.add_argument(
        "representations_json_pos",
        nargs="?",
        help="Path to representation extraction output representations.json.",
    )
    parser.add_argument(
        "out_dir_pos",
        nargs="?",
        help="Directory where graph artifacts are written.",
    )
    parser.add_argument(
        "--representations_json",
        dest="representations_json_opt",
        help="Path to representation extraction output representations.json.",
    )
    parser.add_argument(
        "--out_dir",
        dest="out_dir_opt",
        help="Directory where graph artifacts are written.",
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=12,
        help="Number of relation candidates per LLM matching request.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of concurrent LLM matching requests. Defaults to 1.",
    )
    parser.add_argument(
        "--no_consensus",
        action="store_true",
        help=(
            "Disable the default two-pass order-swapped LLM consensus check. "
            "Use this only when optimizing for speed over relation precision."
        ),
    )
    parser.add_argument(
        "--can_feed_threshold",
        type=float,
        default=0.7,
        help="Minimum confidence for accepting can_feed matches. Defaults to 0.7.",
    )
    parser.add_argument(
        "--similar_to_threshold",
        type=float,
        default=0.65,
        help="Minimum confidence for accepting similar_to matches. Defaults to 0.65.",
    )
    parser.add_argument(
        "--substitute_for_threshold",
        type=float,
        default=0.0,
        help="Minimum confidence for accepting substitute_for matches. Defaults to 0.",
    )
    args = parser.parse_args()

    representations_arg = (
        args.representations_json_opt or args.representations_json_pos
    )
    out_dir_arg = args.out_dir_opt or args.out_dir_pos
    if not representations_arg or not out_dir_arg:
        parser.error("representations_json and out_dir are required")

    representations_json = Path(representations_arg).resolve()
    out_dir = Path(out_dir_arg).resolve()
    progress = ConsoleProgress()

    progress.log(f"loading representations: {representations_json}")
    representations = _load_representations(representations_json)
    progress.log(f"loaded representations: count={len(representations)}")
    progress.log("loading LLM config from .env/environment")
    llm_config = LLMConfig.from_env(REPO_ROOT / ".env")
    progress.log(
        "LLM config loaded: "
        f"model={llm_config.model} base_url={llm_config.base_url} "
        f"temperature={llm_config.temperature}"
    )
    matcher = OpenAICompatibleOntologyMatcher(
        llm_config,
        batch_size=max(1, args.batch_size),
        max_workers=max(1, args.workers),
        require_consensus=not args.no_consensus,
        thresholds={
            "can_feed": _clamp_threshold(args.can_feed_threshold),
            "similar_to": _clamp_threshold(args.similar_to_threshold),
            "substitute_for": _clamp_threshold(args.substitute_for_threshold),
        },
        progress=progress.llm,
    )
    progress.log("building graph artifacts")
    result = GraphBuilder(matcher=matcher).build(representations)
    progress.log(
        "graph build complete: "
        f"candidates={len(result.candidates)} "
        f"matches={len(result.llm_matches)} "
        f"accepted={len([match for match in result.llm_matches if match.accepted])} "
        f"diagnostics={len(result.diagnostics)}"
    )
    progress.log(f"writing artifacts: {out_dir}")
    write_graph_build_result(result, out_dir)
    progress.log("artifacts written")

    print(
        json.dumps(
            {
                "representations_json": str(representations_json),
                "out_dir": str(out_dir),
                "skill_count": len(result.skills),
                "candidate_count": len(result.candidates),
                "llm_match_count": len(result.llm_matches),
                "accepted_match_count": len(
                    [match for match in result.llm_matches if match.accepted]
                ),
                "diagnostics_count": len(result.diagnostics),
                "artifacts": result.manifest.artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _load_representations(path: Path) -> list[SkillRepresentation]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        _representation_from_payload(item)
        for item in payload.get("representations", [])
    ]


def _clamp_threshold(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _representation_from_payload(payload: dict) -> SkillRepresentation:
    return SkillRepresentation(
        id=str(payload.get("id") or ""),
        name=str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
        version=str(payload.get("version") or "1.0.0"),
        tasks=[str(item) for item in payload.get("tasks", [])],
        inputs=[_parameter_from_payload(item) for item in payload.get("inputs", [])],
        outputs=[_artifact_from_payload(item) for item in payload.get("outputs", [])],
        emits_slots=[SlotRef.from_dict(item) for item in payload.get("emits_slots", [])],
        consumes_slots=[SlotRef.from_dict(item) for item in payload.get("consumes_slots", [])],
        preconditions=[
            _condition_from_payload(item)
            for item in payload.get("preconditions", [])
        ],
        postconditions=[
            _condition_from_payload(item)
            for item in payload.get("postconditions", [])
        ],
    )


def _parameter_from_payload(payload: dict) -> ParameterSpec:
    return ParameterSpec(
        name=str(payload.get("name") or "input"),
        type=str(payload.get("type") or "text"),
        required=bool(payload.get("required", True)),
        description=str(payload.get("description") or ""),
        default=payload.get("default"),
        schema_ref=payload.get("schema_ref"),
    )


def _artifact_from_payload(payload: dict) -> ArtifactSpec:
    return ArtifactSpec(
        name=str(payload.get("name") or "result"),
        type=str(payload.get("type") or "unknown"),
        description=str(payload.get("description") or ""),
        schema_ref=payload.get("schema_ref"),
    )


def _condition_from_payload(payload: dict) -> Condition:
    return Condition(
        type=str(payload.get("type") or ""),
        expression=str(payload.get("expression") or ""),
        description=str(payload.get("description") or ""),
    )


if __name__ == "__main__":
    main()
