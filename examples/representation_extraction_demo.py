"""Extract Skill representations from a folder of Skill folders.

Usage:
    python examples/representation_extraction_demo.py <skills_root> <out_dir>
    python examples/representation_extraction_demo.py --skills_root <skills_root> --out_dir <out_dir>

The LLM configuration is read from .env or the process environment:
    OPENAI_API_KEY=...
    OPENAI_BASE_URL=https://api.openai.com/v1
    OPENAI_MODEL=...

The command writes:
    <out_dir>/representations.json
    <out_dir>/diagnostics.json
    <out_dir>/normalization_decisions.json
    <out_dir>/io_name_vocab.json
    <out_dir>/task_vocab.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

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


class ConsoleProgress:
    """Small stderr progress display without external dependencies."""

    def __init__(self, logger: logging.Logger, width: int = 28) -> None:
        self.logger = logger
        self.width = width
        self.last_line_length = 0

    def __call__(self, stage: str, current: int, total: int, item: str) -> None:
        self.logger.info("stage=%s current=%s total=%s item=%s", stage, current, total, item)

        ratio = current / total if total else 1
        filled = int(self.width * ratio)
        bar = "#" * filled + "." * (self.width - filled)
        label = self._stage_label(stage)
        line = f"[{bar}] {current}/{total} {label:<13} {item}"
        self._write_line(line)
        if stage == "done" and current == total:
            print(file=sys.stderr, flush=True)

    def _write_line(self, line: str) -> None:
        padding = " " * max(0, self.last_line_length - len(line))
        print(f"\r{line}{padding}", end="", file=sys.stderr, flush=True)
        self.last_line_length = len(line)

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


class VocabularyProgress:
    """Report each LLM-backed io_name_vocab decision so normalize is not silent."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.last_line_length = 0

    def __call__(
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
            self._write_line(
                (
                    "[vocab] resolving   "
                    f"{candidate.skill_id} {candidate.direction}:{candidate.token}"
                )
            )
            return
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
        self._write_line(
            (
                "[vocab] resolved    "
                f"{candidate.skill_id} {candidate.direction}:{candidate.token} "
                f"-> {resolution.normalized_value or 'excluded'} "
                f"({resolution.action})"
            )
        )

    def _write_line(self, line: str) -> None:
        padding = " " * max(0, self.last_line_length - len(line))
        print(f"\r{line}{padding}", end="", file=sys.stderr, flush=True)
        self.last_line_length = len(line)


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
            "normalization_decisions.json, io_name_vocab.json, and "
            "task_vocab.json are written."
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
            "normalization_decisions.json, io_name_vocab.json, and "
            "task_vocab.json are written."
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

    llm_config = LLMConfig.from_env(REPO_ROOT / ".env")
    normalization_config = NormalizationConfig()
    io_name_resolver = (
        HeuristicIONameResolver()
        if args.heuristic_vocab_resolver
        else OpenAICompatibleIONameResolver(
            llm_config,
            progress=VocabularyProgress(logger),
        )
    )
    extractor = RepresentationExtractor(
        OpenAICompatibleSchemaExtractor(llm_config),
        normalizer=SkillRepresentationNormalizer(
            config=normalization_config,
            io_name_vocabulary=IONameVocabulary.from_config(normalization_config),
            io_name_resolver=io_name_resolver,
        ),
        progress=ConsoleProgress(logger),
        max_workers=args.workers,
    )
    result = extractor.extract_all(skills_root)
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
                "task_vocab": "task_vocab.json",
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
