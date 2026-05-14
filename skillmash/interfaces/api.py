from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from skillmash.interfaces.ui_server import INDEX_HTML
from skillmash.runtime.app_service import SkillMashService


def create_app(index_dir: str | None = None) -> FastAPI:
    service = SkillMashService(index_dir=index_dir)
    app = FastAPI(
        title="SkillMash Skill Orchestration API",
        version="0.1.0",
        description="API for the SkillMash Skill graph and planning prototype.",
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/api/build")
    def build_summary():
        return service.build_summary()

    @app.get("/api/skills")
    def list_skills():
        return service.list_skills()

    @app.get("/api/graph")
    def graph_summary():
        return service.graph_summary()

    @app.get("/api/decompose")
    def decompose(skill_id: str = Query(..., min_length=1)):
        try:
            return service.decompose(skill_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/match")
    def match(
        source: str = Query(..., min_length=1),
        target: str = Query(..., min_length=1),
    ):
        try:
            return service.match(source, target)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/plan")
    def plan(task: str = Query(..., min_length=1)):
        try:
            return service.plan(task)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()


def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    uvicorn.run("skillmash.interfaces.api:app", host=host, port=port, reload=False)
