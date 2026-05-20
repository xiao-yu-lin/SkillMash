"""Serve the SkillMash graph UI without writing per-request console logs."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit, urlunsplit


class QuietHandler(SimpleHTTPRequestHandler):
    build_dir: Optional[str] = None

    def do_GET(self) -> None:
        if self.build_dir and self._is_ui_entry_without_query():
            self.send_response(302)
            self.send_header("Location", self._with_build_dir_query())
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _is_ui_entry_without_query(self) -> bool:
        parts = urlsplit(self.path)
        return parts.query == "" and parts.path in {"/", "/ui/", "/ui/index.html"}

    def _with_build_dir_query(self) -> str:
        parts = urlsplit(self.path)
        path = "/ui/index.html" if parts.path in {"/", "/ui/"} else parts.path
        query = f"build_dir={quote(self.build_dir or '')}"
        return urlunsplit(("", "", path, query, ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the SkillMash graph UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root to serve.",
    )
    parser.add_argument(
        "--build-dir",
        default="",
        help="Build output directory to load when opening the UI, relative to --root.",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    QuietHandler.build_dir = _normalize_url_path(args.build_dir or args.out_dir)
    handler = lambda *handler_args, **handler_kwargs: QuietHandler(
        *handler_args,
        directory=args.root,
        **handler_kwargs,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.serve_forever()


def _normalize_url_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


if __name__ == "__main__":
    main()
