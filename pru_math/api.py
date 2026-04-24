"""FastAPI layer exposing the Phase 1 reasoner."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .reasoner import Reasoner
from .store import Store


class SolveRequest(BaseModel):
    text: str = Field(..., description="Math problem in SymPy, LaTeX, or natural language.")


def _attempt_to_dict(a) -> dict[str, Any]:
    return {
        "id": a.id,
        "problem_id": a.problem_id,
        "tool": a.tool,
        "approach": a.approach,
        "success": a.success,
        "result_repr": a.result_repr,
        "result_pretty": a.result_pretty,
        "verification_status": a.verification_status,
        "verification_detail": a.verification_detail,
        "time_ms": a.time_ms,
        "error": a.error,
        "steps": a.steps,
        "created_at": a.created_at,
    }


def _problem_to_dict(p) -> dict[str, Any]:
    return {
        "id": p.id,
        "raw_input": p.raw_input,
        "source_format": p.source_format,
        "problem_type": p.problem_type,
        "parsed_expr": p.parsed_expr,
        "parsed_pretty": p.parsed_pretty,
        "fingerprint": p.fingerprint,
        "signature": p.signature,
        "created_at": p.created_at,
    }


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    reasoner = Reasoner(store=store)

    app = FastAPI(title="PRU Math Engine", version="0.1.0")

    @app.post("/solve")
    def solve(req: SolveRequest) -> dict[str, Any]:
        outcome = reasoner.solve(req.text)
        return outcome.to_dict()

    @app.get("/problems")
    def list_problems(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return {
            "items": [_problem_to_dict(p) for p in store.list_problems(limit=limit, offset=offset)],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/problems/{problem_id}")
    def get_problem(problem_id: int) -> dict[str, Any]:
        problem = store.get_problem(problem_id)
        if not problem:
            raise HTTPException(404, f"no problem with id={problem_id}")
        attempts = store.list_attempts(problem_id)
        return {
            "problem": _problem_to_dict(problem),
            "attempts": [_attempt_to_dict(a) for a in attempts],
        }

    @app.get("/db/stats")
    def stats() -> dict[str, Any]:
        return store.stats()

    # --- Frontend (minimal static UI) --------------------------------------
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        # Serve /static/* for assets, and / for the SPA entry.
        static_dir = frontend_dir / "static"
        if static_dir.is_dir():
            app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

    return app


app = create_app()
