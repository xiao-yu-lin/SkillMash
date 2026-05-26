"""Serve the SkillMash graph UI with lightweight progress logging."""

from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
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
        started_at = perf_counter()
        try:
            request = self._read_json_body()
            query = str(request.get("query") or "").strip()
            min_edge_confidence = float(request.get("min_edge_confidence") or 0.7)
            top_m = int(request.get("top_m") or 40)
            show_candidates = bool(request.get("show_candidates"))
            _log(
                "orchestrate request started "
                f"query_len={len(query)} top_k={request.get('top_k', 5)} "
                f"max_plans={request.get('max_plans', 60)} max_depth={request.get('max_depth', 10)} "
                f"max_branch={request.get('max_branch', 20)} "
                f"min_edge_confidence={min_edge_confidence} top_m={top_m} "
                f"show_candidates={show_candidates}"
            )
            response = self._orchestrate(request)
            self._send_json(200, response)
            _log(f"orchestrate request finished elapsed={perf_counter() - started_at:.2f}s")
        except Exception as exc:
            _log(f"orchestrate request failed elapsed={perf_counter() - started_at:.2f}s error={exc!r}")
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
        started_at = perf_counter()
        root = Path(self.root_dir or ".").resolve()
        build_dir = _resolve_build_dir(
            root,
            str(
                request.get("build_dir")
                or self.build_dir
                or "OUTPUT/v4/graph"
            ),
        )
        query = str(request.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        min_edge_confidence = max(0.0, min(1.0, float(request.get("min_edge_confidence") or 0.7)))
        top_m = max(1, min(80, int(request.get("top_m") or 40)))
        show_candidates = bool(request.get("show_candidates"))
        top_k = max(1, min(10, int(request.get("top_k") or 5)))
        max_plans = max(1, min(80, int(request.get("max_plans") or 60)))
        max_depth = max(1, min(20, int(request.get("max_depth") or 10)))
        max_branch = max(1, min(20, int(request.get("max_branch") or 20)))

        import sys

        _ensure_project_import_paths(root, sys.path)

        from skillmash.orchestration import SkillOrchestrator, load_build_artifacts
        from skillmash.representation import LLMConfig

        _log(f"loading llm config and build artifacts from {build_dir}")
        stage_start = perf_counter()
        llm_config = LLMConfig.from_env(root / ".env")
        artifacts = load_build_artifacts(build_dir)
        _log(f"artifacts loaded elapsed={perf_counter() - stage_start:.2f}s")

        _log(
            "planning candidates "
            f"min_edge_confidence={min_edge_confidence} max_depth={max_depth} "
            f"max_plans={max_plans} max_branch={max_branch} top_m={top_m} top_k={top_k} "
            f"show_candidates={show_candidates}"
        )
        stage_start = perf_counter()
        planner = SkillOrchestrator(
            artifacts,
            llm_config=llm_config,
            min_edge_confidence=min_edge_confidence,
            max_depth=max_depth,
            max_plans=max_plans,
            max_branch=max_branch,
            top_m=top_m,
            top_k=top_k,
            include_candidates=show_candidates,
        )
        result = planner.plan(query)
        _log(f"planning finished elapsed={perf_counter() - stage_start:.2f}s")
        _log(f"orchestration finished total_elapsed={perf_counter() - started_at:.2f}s")
        return result


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
        help="Build output directory to load, relative to --root.",
    )
    args = parser.parse_args()

    QuietHandler.build_dir = _normalize_url_path(args.build_dir)
    QuietHandler.root_dir = str(Path(args.root).resolve())
    handler = lambda *handler_args, **handler_kwargs: QuietHandler(
        *handler_args,
        directory=args.root,
        **handler_kwargs,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    build_dir = QuietHandler.build_dir or "(not set)"
    ui_url = _build_ui_url(args.host, args.port, QuietHandler.build_dir)
    _log(
        f"serving UI on http://{args.host}:{args.port} "
        f"root={Path(args.root).resolve()} build_dir={build_dir}"
    )
    _log(f"open ui: {ui_url}")
    _log(f"server startup successful host={args.host} port={args.port}")
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


def _log(message: str) -> None:
    print(f"[skillmash-ui] {message}", flush=True)


def _build_ui_url(host: str, port: int, build_dir: Optional[str]) -> str:
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    path = "/ui/index.html"
    if build_dir:
        path = f"{path}?build_dir={quote(build_dir)}"
    return f"http://{display_host}:{port}{path}"


if __name__ == "__main__":
    main()
