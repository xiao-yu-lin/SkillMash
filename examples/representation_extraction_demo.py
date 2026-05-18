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
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skillmash.representation import (  # noqa: E402
    LLMConfig,
    OpenAICompatibleSchemaExtractor,
    RepresentationExtractionResult,
    RepresentationExtractor,
)


class ConsoleProgress:
    """Small stderr progress bar without external dependencies."""

    def __init__(self, logger: logging.Logger, width: int = 28) -> None:
        self.logger = logger
        self.width = width
        self.last_line_length = 0

    def __call__(self, stage: str, current: int, total: int, item: str) -> None:
        self.logger.info("stage=%s current=%s total=%s item=%s", stage, current, total, item)
        if stage != "done":
            return

        ratio = current / total if total else 1
        filled = int(self.width * ratio)
        bar = "#" * filled + "." * (self.width - filled)
        line = f"[{bar}] {current}/{total} {item}"
        padding = " " * max(0, self.last_line_length - len(line))
        print(f"\r{line}{padding}", end="", file=sys.stderr, flush=True)
        self.last_line_length = len(line)
        if current == total:
            print(file=sys.stderr, flush=True)


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
        help="Directory where representations.json and diagnostics.json are written.",
    )
    parser.add_argument(
        "--skills_root",
        dest="skills_root_opt",
        help="Directory containing multiple Skill folders with SKILL.md files.",
    )
    parser.add_argument(
        "--out_dir",
        dest="out_dir_opt",
        help="Directory where representations.json and diagnostics.json are written.",
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

    extractor = RepresentationExtractor(
        OpenAICompatibleSchemaExtractor(LLMConfig.from_env()),
        progress=ConsoleProgress(logger),
    )
    result = extractor.extract_all(skills_root)
    write_result(result, out_dir)
    logger.info(
        "finished representation extraction representation_count=%s diagnostics_count=%s",
        len(result.representations),
        len(result.diagnostics),
    )

    print(
        json.dumps(
            {
                "skills_root": str(skills_root),
                "out_dir": str(out_dir),
                "representation_count": len(result.representations),
                "diagnostics_count": len(result.diagnostics),
                "representations": "representations.json",
                "diagnostics": "diagnostics.json",
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


def write_result(result: RepresentationExtractionResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "representations.json").write_text(
        json.dumps(
            {
                "representations": [
                    representation.to_dict()
                    for representation in result.representations
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "diagnostics.json").write_text(
        json.dumps(
            {
                "diagnostics": [
                    diagnostic.to_dict()
                    for diagnostic in result.diagnostics
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
