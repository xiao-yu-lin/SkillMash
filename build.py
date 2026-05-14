"""CLI entry for the offline SkillMash build stage."""

from __future__ import annotations

import argparse
import json
import sys

from skillmash.build.extraction import OpenAISkillSchemaExtractor
from skillmash.build.offline import OfflineBuilder


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a SkillMash offline index.")
    parser.add_argument("--skills-root", required=True, help="Root directory containing Skill folders.")
    parser.add_argument("--out", default=".skillmash/index", help="Output directory for build artifacts.")
    parser.add_argument(
        "--llm-model",
        default="gpt-4.1-mini",
        help="OpenAI-compatible model used for Skill schema extraction.",
    )
    parser.add_argument(
        "--llm-base-url",
        help="Optional OpenAI-compatible base URL for schema extraction.",
    )
    args = parser.parse_args()

    extractor = OpenAISkillSchemaExtractor(model=args.llm_model, base_url=args.llm_base_url)
    artifact = OfflineBuilder(
        args.skills_root,
        args.out,
        extractor=extractor,
        progress=lambda message: print(f"[build] {message}", file=sys.stderr, flush=True),
    ).build()
    print(json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

