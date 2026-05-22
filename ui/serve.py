"""Serve the SkillMash graph UI without writing per-request console logs."""

from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit, urlunsplit


class QuietHandler(SimpleHTTPRequestHandler):
    build_dir: Optional[str] = None
    root_dir: Optional[str] = None

    def do_GET(self) -> None:
        if self.build_dir and self._is_ui_entry_without_query():
            self.send_response(302)
            self.send_header("Location", self._with_build_dir_query())
            self.end_headers()
            return
        super().do_GET()

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != "/api/orchestrate":
            self.send_error(404, "Not Found")
            return
        try:
            request = self._read_json_body()
            response = self._orchestrate(request)
            self._send_json(200, response)
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

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

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _orchestrate(self, request: dict) -> dict:
        root = Path(self.root_dir or ".").resolve()
        build_dir = _resolve_build_dir(root, str(request.get("build_dir") or self.build_dir or "OUTPUT/build"))
        query = str(request.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        top_k = max(1, min(10, int(request.get("top_k") or 3)))
        max_plans = max(1, min(80, int(request.get("max_plans") or 20)))
        max_depth = max(1, min(8, int(request.get("max_depth") or 4)))
        max_branch = max(1, min(20, int(request.get("max_branch") or 8)))

        import sys

        _ensure_project_import_paths(root, sys.path)

        from skillmash.orchestration import SkillOrchestrator, load_build_artifacts
        from skillmash.reranking import PlanReranker
        from skillmash.representation import LLMConfig

        llm_config = LLMConfig.from_env(root / ".env")
        artifacts = load_build_artifacts(build_dir)
        planner = SkillOrchestrator(
            artifacts,
            llm_config=llm_config,
            max_depth=max_depth,
            max_plans=max_plans,
            max_branch=max_branch,
        )
        result = planner.plan(query)
        return PlanReranker(llm_config=llm_config).rerank(result, top_k=top_k)


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
    QuietHandler.root_dir = str(Path(args.root).resolve())
    handler = lambda *handler_args, **handler_kwargs: QuietHandler(
        *handler_args,
        directory=args.root,
        **handler_kwargs,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.serve_forever()


def _normalize_url_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _resolve_build_dir(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / value.replace("\\", "/").strip("/")
    return path.resolve()


def _ensure_project_import_paths(root: Path, sys_path: list[str]) -> None:
    """Make the UI server robust when launched outside the project venv."""

    candidates = [root]
    venv_dir = root / ".venv"
    candidates.append(venv_dir / "Lib" / "site-packages")
    candidates.extend((venv_dir / "lib").glob("python*/site-packages"))

    for path in reversed([item.resolve() for item in candidates if item.exists()]):
        value = str(path)
        if value not in sys_path:
            sys_path.insert(0, value)


if __name__ == "__main__":
    main()
