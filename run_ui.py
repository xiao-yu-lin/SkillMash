from __future__ import annotations

import argparse

from skillmash.interfaces.ui_server import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SkillMash demo UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--index", help="Offline build artifact directory. Uses sample skills if omitted.")
    args = parser.parse_args()
    run(args.host, args.port, index_dir=args.index)


if __name__ == "__main__":
    main()

