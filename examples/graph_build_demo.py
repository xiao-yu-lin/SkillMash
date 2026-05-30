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
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

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
    LLMConfig,
    ParameterSpec,
    SkillRepresentation,
)


class GraphProgress:
    """Render graph build progress with Rich.

    Follows the same style as representation_extraction_demo.RichProgress.
    """

    # Weight for each stage in overall progress (should sum to 100)
    STAGE_WEIGHTS = {
        "load": 10,
        "candidates": 20,
        "llm_matching": 50,
        "graph_build": 15,
        "write": 5,
    }

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.started_at = time.monotonic()
        self.console = Console(stderr=True)
        self.progress: Optional[Progress] = None
        self.main_task_id = None
        self.lock = threading.Lock()
        self.closed = False

        # Stage counters
        self.total = 0
        self.last_stage = "init"
        self.last_item = ""
        self.last_current = 0
        # LLM matching stats
        self.llm_total = 0
        self.llm_current = 0
        self.match_count = 0
        self.accepted_count = 0
        self.diagnostics_count = 0
        self.candidate_count = 0

    def __call__(
        self,
        stage: str,
        current: int,
        total: int,
        item: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Main progress callback for non-LLM stages."""
        self.logger.info(
            "stage=%s current=%s total=%s item=%s",
            stage,
            current,
            total,
            item,
        )
        with self.lock:
            self._ensure_started(max(1, total))
            self.total = max(self.total, total)
            self.last_stage = stage
            self.last_item = item
            self.last_current = current

            if stage == "load":
                pass  # Just tracking
            elif stage == "candidates":
                self.candidate_count = current
            elif stage == "graph_build":
                pass  # Just tracking
            elif stage == "write":
                pass  # Just tracking
            elif stage == "done":
                self._close_locked()
                return

            if self.progress is not None and self.main_task_id is not None:
                self.progress.update(
                    self.main_task_id,
                    completed=self._calculate_weighted_progress(),
                    total=100,
                    status=self._main_status_line(),
                )

    def llm(
        self,
        event: str,
        current: int,
        total: int,
        details: dict[str, Any],
    ) -> None:
        """LLM matching progress callback."""
        self.logger.info(
            "stage=llm_matching event=%s current=%s total=%s details=%s",
            event,
            current,
            total,
            details,
        )
        with self.lock:
            self._ensure_started(max(1, total))

            if event == "matching_start":
                self.llm_total = total
                self.llm_current = 0
                self.match_count = 0
                self.accepted_count = 0
                self.diagnostics_count = 0
                self.last_stage = "llm_matching"
                self.candidate_count = details.get("candidate_count", 0)
            elif event == "batch_start":
                self.llm_current = current
                self.last_item = f"batch {current}/{total}"
            elif event == "batch_done":
                self.llm_current = current
                self.match_count += int(details.get("match_count", 0))
                self.accepted_count += int(details.get("accepted_count", 0))
                self.diagnostics_count += int(details.get("diagnostics_count", 0))
            elif event == "matching_done":
                self.llm_current = total
                self.match_count = int(details.get("match_count", 0))
                self.accepted_count = int(details.get("accepted_count", 0))
                self.diagnostics_count = int(details.get("diagnostics_count", 0))

            if self.progress is not None and self.main_task_id is not None:
                self.progress.update(
                    self.main_task_id,
                    completed=self._calculate_weighted_progress(),
                    total=100,
                    status=self._main_status_line(),
                )

    def close(self) -> None:
        with self.lock:
            self._close_locked()

    def _ensure_started(self, total: int) -> None:
        if self.progress is not None:
            return
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
        self.main_task_id = self.progress.add_task(
            "Graph build",
            total=100,
            status=self._main_status_line(),
        )

    def _calculate_weighted_progress(self) -> int:
        """Calculate weighted progress percentage based on stage completion."""
        weights = self.STAGE_WEIGHTS

        # Each stage contributes its weight when complete
        load_progress = weights.get("load", 0) if self.last_stage not in ("init",) else 0
        candidates_progress = (
            (self.candidate_count / max(1, self.total) * weights.get("candidates", 0))
            if self.candidate_count > 0
            else 0
        )
        llm_progress = (
            (self.llm_current / max(1, self.llm_total) * weights.get("llm_matching", 0))
            if self.llm_total > 0
            else 0
        )

        return int(load_progress + candidates_progress + llm_progress)

    def _main_status_line(self) -> str:
        """Generate single-line status for main progress bar."""
        stage = self._stage_label(self.last_stage)
        item = self._short_item(self.last_item)
        current = self.last_current

        parts = [f"→ [{stage}]"]
        if item:
            parts.append(f"{item}")
        if self.llm_total > 0:
            parts.append(
                f"llm {self.llm_current}/{self.llm_total} "
                f"matches={self.match_count} accepted={self.accepted_count}"
            )
        elif self.candidate_count > 0:
            parts.append(f"candidates={self.candidate_count}")

        return " ".join(parts)

    def _close_locked(self) -> None:
        if self.closed:
            return
        if self.progress is not None:
            self.progress.stop()
            self.progress = None
            self.main_task_id = None
        elapsed = time.monotonic() - self.started_at
        self.console.log(
            "[graph-build] progress finished: "
            f"elapsed={elapsed:.1f}s "
            f"candidates={self.candidate_count} "
            f"llm_batches={self.llm_current}/{self.llm_total} "
            f"matches={self.match_count} "
            f"accepted={self.accepted_count} "
            f"diagnostics={self.diagnostics_count}"
        )
        self.closed = True

    def _stage_label(self, stage: str) -> str:
        labels = {
            "init": "init",
            "load": "load",
            "candidates": "candidates",
            "llm_matching": "llm_match",
            "graph_build": "build",
            "write": "write",
            "done": "done",
        }
        return labels.get(stage, stage)

    def _short_item(self, item: str) -> str:
        if not item:
            return ""
        # Truncate long items
        if len(item) > 40:
            return item[:37] + "..."
        return item


def configure_logging(out_dir: Path) -> logging.Logger:
    """Configure logging for graph build demo.

    Logs are written to both file and stderr console.
    """
    logger = logging.getLogger("skillmash.graph.example")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    # File handler for persistent log
    file_handler = logging.FileHandler(out_dir / "graph_build.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # Console handler for stderr
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    return logger


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
        "--debug_candidates",
        action="store_true",
        help="Emit DEBUG logs for relation candidate generation decisions.",
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
    out_dir.mkdir(parents=True, exist_ok=True)

    logger = configure_logging(out_dir)
    logger.info("starting graph build")
    logger.info("representations_json=%s", representations_json)
    logger.info("out_dir=%s", out_dir)
    logger.info("batch_size=%s workers=%s", args.batch_size, args.workers)
    logger.info(
        "consensus=%s can_feed_threshold=%s",
        not args.no_consensus,
        args.can_feed_threshold,
    )

    if args.debug_candidates:
        logging.getLogger("skillmash.graph.candidates").setLevel(logging.DEBUG)

    progress = GraphProgress(logger)

    progress("load", 0, 0, str(representations_json))
    progress("load", 1, 1, str(representations_json))
    logger.info("loading representations: %s", representations_json)
    representations = _load_representations(representations_json)
    logger.info("loaded representations: count=%s", len(representations))
    progress("load", 1, 1, f"loaded {len(representations)} representations")

    logger.info("loading LLM config from .env/environment")
    llm_config = LLMConfig.from_env(REPO_ROOT / ".env")
    logger.info(
        "LLM config loaded: model=%s base_url=%s temperature=%s",
        llm_config.model,
        llm_config.base_url,
        llm_config.temperature,
    )

    matcher = OpenAICompatibleOntologyMatcher(
        llm_config,
        batch_size=max(1, args.batch_size),
        max_workers=max(1, args.workers),
        require_consensus=not args.no_consensus,
        thresholds={
            "can_feed": _clamp_threshold(args.can_feed_threshold),
        },
        progress=progress.llm,
    )

    logger.info("building graph artifacts")
    result = GraphBuilder(matcher=matcher).build(representations)
    logger.info(
        "graph build complete: candidates=%s matches=%s accepted=%s diagnostics=%s",
        len(result.candidates),
        len(result.llm_matches),
        len([match for match in result.llm_matches if match.accepted]),
        len(result.diagnostics),
    )

    progress("write", 0, 1, str(out_dir))
    logger.info("writing artifacts: %s", out_dir)
    write_graph_build_result(result, out_dir)
    progress("write", 1, 1, str(out_dir))
    logger.info("artifacts written")

    progress("done", 1, 1, "complete")
    progress.close()

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
        SkillRepresentation.from_dict(item)
        for item in payload.get("representations", [])
    ]


def _clamp_threshold(value: float) -> float:
    return max(0.0, min(1.0, float(value)))

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


if __name__ == "__main__":
    main()
