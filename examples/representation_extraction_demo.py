"""Extract Skill representations from a folder of Skill folders.

Usage:
    python examples/representation_extraction_demo.py <skills_root> <out_dir>
    python examples/representation_extraction_demo.py --skills_root <skills_root> --out_dir <out_dir>

The LLM configuration is read from .env or the process environment:
    LLM_API_KEY=...
    LLM_BASE_URL=https://api.openai.com/v1
    LLM_MODEL=...

The command writes:
    <out_dir>/representations.json
    <out_dir>/diagnostics.json
    <out_dir>/normalization_decisions.json
    <out_dir>/io_name_vocab.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

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
        "The rich package is required for representation_extraction_demo.py. "
        "Install dependencies with `uv sync` or `pip install rich`."
    ) from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skillmash.representation import (  # noqa: E402
    HeuristicIONameResolver,
    IONameCandidate,
    IONameResolution,
    IONameVocabulary,
    LLMConfig,
    NormalizationConfig,
    OpenAICompatibleSchemaExtractor,
    OpenAICompatibleIONameResolver,
    RepresentationExtractor,
    SkillRepresentationNormalizer,
    write_extraction_result,
)


class RichProgress:
    """Render extraction and vocab resolution progress with Rich."""

    def __init__(self, logger: logging.Logger, workers: int) -> None:
        self.logger = logger
        self.workers = max(1, workers)
        self.started_at = time.monotonic()
        self.console = Console(stderr=True)
        self.progress: Optional[Progress] = None
        self.main_task_id = None
        self.vocab_task_id = None
        self.lock = threading.Lock()
        self.closed = False

        self.total = 0
        self.parse_count = 0
        self.extract_count = 0
        self.normalize_count = 0
        self.done_count = 0
        self.last_stage = "scan"
        self.last_item = ""
        self.last_vocab = ""
        self.vocab_started = 0
        self.vocab_done = 0
        self.vocab_excluded = 0
        self.vocab_forced_merge = 0

    def __call__(self, stage: str, current: int, total: int, item: str) -> None:
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
            if stage == "parse":
                self.parse_count += 1
            elif stage == "extract":
                self.extract_count += 1
            elif stage == "extract_batch":
                self.extract_count = max(self.extract_count, current)
            elif stage == "normalize":
                self.normalize_count += 1
            elif stage == "done":
                self.done_count = max(self.done_count, current)

            if self.progress is not None and self.main_task_id is not None:
                self.progress.update(
                    self.main_task_id,
                    completed=self.done_count,
                    total=max(1, self.total),
                    status=self._main_status_line(),
                )
            if self.progress is not None and self.vocab_task_id is not None:
                self.progress.update(
                    self.vocab_task_id,
                    status=self._vocab_status_line(),
                )

            if stage == "scan" and total == 0:
                self._close_locked()
                self.console.log("[representation] no Skill folder found to process")
            elif stage == "done" and self.done_count >= total > 0:
                self._close_locked()

    def vocab(
        self,
        stage: str,
        candidate: IONameCandidate,
        resolution: Optional[IONameResolution],
    ) -> None:
        if resolution is None:
            self.logger.info(
                "stage=vocab_resolve status=start skill_id=%s direction=%s token=%s type=%s",
                candidate.skill_id,
                candidate.direction,
                candidate.token,
                candidate.data_type,
            )
        else:
            self.logger.info(
                (
                    "stage=vocab_resolve status=done skill_id=%s direction=%s "
                    "token=%s action=%s target=%s confidence=%s forced_merge=%s"
                ),
                candidate.skill_id,
                candidate.direction,
                candidate.token,
                resolution.action,
                resolution.normalized_value,
                resolution.confidence,
                resolution.forced_merge,
            )

        with self.lock:
            self._ensure_started(max(1, self.total))
            if resolution is None:
                self.vocab_started += 1
                self.last_vocab = (
                    f"resolving {candidate.skill_id} "
                    f"{candidate.direction}:{candidate.token}"
                )
            else:
                self.vocab_done += 1
                if resolution.action == "exclude":
                    self.vocab_excluded += 1
                if resolution.forced_merge:
                    self.vocab_forced_merge += 1
                confidence = (
                    f"{float(resolution.confidence):.2f}"
                    if resolution.confidence is not None
                    else "n/a"
                )
                target = resolution.normalized_value or "excluded"
                self.last_vocab = (
                    f"{candidate.skill_id} {candidate.direction}:{candidate.token} "
                    f"-> {target} ({resolution.action}, conf={confidence})"
                )

            if self.progress is not None and self.vocab_task_id is not None:
                self.progress.update(
                    self.vocab_task_id,
                    status=self._vocab_status_line(),
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
            "Representation extraction",
            total=total,
            status=self._main_status_line(),
        )
        self.vocab_task_id = self.progress.add_task(
            "IO name vocab",
            total=1,
            completed=0,
            status=self._vocab_status_line(),
        )

    def _main_status_line(self) -> str:
        total = max(1, self.total)
        stage = self._stage_label(self.last_stage)
        item = self._short_item(self.last_item)
        return (
            f"stg={stage} item={item} w={self.workers} "
            f"p={min(self.parse_count, total)}/{total} "
            f"e={min(self.extract_count, total)}/{total} "
            f"n={min(self.normalize_count, total)}/{total} "
            f"d={min(self.done_count, total)}/{total}"
        )

    def _vocab_status_line(self) -> str:
        started = max(self.vocab_started, self.vocab_done)
        detail = self._trim(self.last_vocab, 64) if self.last_vocab else "-"
        return (
            f"done={self.vocab_done}/{started} "
            f"exc={self.vocab_excluded} "
            f"merge={self.vocab_forced_merge} "
            f"last={detail}"
        )

    def _close_locked(self) -> None:
        if self.closed:
            return
        if self.progress is not None:
            self.progress.stop()
            self.progress = None
            self.main_task_id = None
            self.vocab_task_id = None
        elapsed = time.monotonic() - self.started_at
        self.console.log(
            "[representation] progress finished: "
            f"elapsed={elapsed:.1f}s "
            f"done={self.done_count}/{self.total} "
            f"vocab_done={self.vocab_done} "
            f"vocab_excluded={self.vocab_excluded} "
            f"vocab_forced_merge={self.vocab_forced_merge}"
        )
        self.closed = True

    def _stage_label(self, stage: str) -> str:
        labels = {
            "scan": "scan",
            "parse": "parse",
            "extract": "llm_extract",
            "extract_batch": "llm_batch",
            "normalize": "normalize",
            "done": "done",
        }
        return labels.get(stage, stage)

    def _short_item(self, item: str) -> str:
        if not item:
            return "-"
        if "/" in item:
            parts = [part for part in item.split("/") if part]
            if len(parts) >= 2:
                return f"{parts[-2]}/{parts[-1]}"
            return parts[-1]
        return item

    def _trim(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"...{text[-(limit - 3):]}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract normalized Skill representations from Skill folders."
    )
    parser.add_argument(
        "skills_root_pos",
        nargs="?",
        help="Directory containing multiple Skill folders with SKILL.md files.",
    )
    parser.add_argument(
        "out_dir_pos",
        nargs="?",
        help=(
            "Directory where representations.json, diagnostics.json, "
            "normalization_decisions.json, and io_name_vocab.json are written."
        ),
    )
    parser.add_argument(
        "--skills_root",
        dest="skills_root_opt",
        help="Directory containing multiple Skill folders with SKILL.md files.",
    )
    parser.add_argument(
        "--out_dir",
        dest="out_dir_opt",
        help=(
            "Directory where representations.json, diagnostics.json, "
            "normalization_decisions.json, and io_name_vocab.json are written."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of concurrent LLM extraction workers. Defaults to 1.",
    )
    parser.add_argument(
        "--heuristic_vocab_resolver",
        action="store_true",
        help=(
            "Resolve unseen io_name_vocab terms with a local heuristic. "
            "By default, unseen terms are resolved with the LLM."
        ),
    )
    args = parser.parse_args()

    skills_root_arg = args.skills_root_opt or args.skills_root_pos
    out_dir_arg = args.out_dir_opt or args.out_dir_pos
    if not skills_root_arg or not out_dir_arg:
        parser.error("skills_root and out_dir are required")

    skills_root = Path(skills_root_arg).resolve()
    out_dir = Path(out_dir_arg).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(out_dir)
    logger.info("starting representation extraction")
    logger.info("skills_root=%s", skills_root)
    logger.info("out_dir=%s", out_dir)
    logger.info("workers=%s", args.workers)
    logger.info(
        "io_name_vocab_resolver=%s",
        "heuristic" if args.heuristic_vocab_resolver else "llm",
    )

    progress = RichProgress(logger, workers=args.workers)
    llm_config = LLMConfig.from_env(REPO_ROOT / ".env")
    normalization_config = NormalizationConfig()
    io_name_resolver = (
        HeuristicIONameResolver()
        if args.heuristic_vocab_resolver
        else OpenAICompatibleIONameResolver(
            llm_config,
            progress=progress.vocab,
        )
    )
    extractor = RepresentationExtractor(
        OpenAICompatibleSchemaExtractor(llm_config),
        normalizer=SkillRepresentationNormalizer(
            config=normalization_config,
            io_name_vocabulary=IONameVocabulary.from_config(normalization_config),
            io_name_resolver=io_name_resolver,
        ),
        progress=progress,
        max_workers=args.workers,
    )
    try:
        result = extractor.extract_all(skills_root)
    finally:
        progress.close()
    write_extraction_result(result, out_dir)
    logger.info(
        (
            "finished representation extraction representation_count=%s "
            "diagnostics_count=%s normalization_decision_count=%s"
        ),
        len(result.representations),
        len(result.diagnostics),
        len(result.normalization_decisions),
    )

    print(
        json.dumps(
            {
                "skills_root": str(skills_root),
                "out_dir": str(out_dir),
                "representation_count": len(result.representations),
                "diagnostics_count": len(result.diagnostics),
                "normalization_decision_count": len(result.normalization_decisions),
                "workers": max(1, args.workers),
                "io_name_vocab_resolver": "heuristic" if args.heuristic_vocab_resolver else "llm",
                "representations": "representations.json",
                "diagnostics": "diagnostics.json",
                "normalization_decisions": "normalization_decisions.json",
                "io_name_vocab": "io_name_vocab.json",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def configure_logging(out_dir: Path) -> logging.Logger:
    logger = logging.getLogger("skillmash.representation.example")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    file_handler = logging.FileHandler(out_dir / "extraction.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


if __name__ == "__main__":
    main()
