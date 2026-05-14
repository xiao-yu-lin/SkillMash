"""CLI entry for the SkillMash artifact visualization UI."""

from __future__ import annotations

import argparse

from skillmash.interfaces.ui_server import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SkillMash build artifact visualization UI.")
    parser.add_argument("--index", help="Offline build artifact directory. Uses sample skills if omitted.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    run(host=args.host, port=args.port, index_dir=args.index)


if __name__ == "__main__":
    main()

