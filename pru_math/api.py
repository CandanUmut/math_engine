"""FastAPI layer exposing the Phase 1+2 reasoner.

Endpoints
---------
- ``POST /solve``                          — solve a math problem
- ``GET  /problems``                       — list problems (recent first)
- ``GET  /problems/{id}``                  — one problem + its attempts
- ``GET  /problems/{id}/similar?k=5``      — K most similar past problems
- ``GET  /attempts``                       — list attempts (recent first)
- ``GET  /tool_outcomes``                  — aggregated tool stats
- ``GET  /graph``                          — full graph as cytoscape JSON
- ``GET  /graph/around/{id}?radius=1``     — subgraph around a problem
- ``GET  /graph/stats``                    — node/edge counts by kind
- ``GET  /db/stats``                       — store stats
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import CONFIG
from .graph import RelationalGraph
from .reasoner import Reasoner
from .retrieval import find_similar_problems
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


def create_app(store: Store | None = None,
               graph: RelationalGraph | None = None) -> FastAPI:
    store = store or Store()
    graph = graph or RelationalGraph()
    reasoner = Reasoner(store=store, graph=graph)

    app = FastAPI(title="PRU Math Engine", version="0.2.0")

    # --- Solve / problems ----------------------------------------------------

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

    @app.get("/problems/{problem_id}/similar")
    def similar(problem_id: int, k: int = 5) -> dict[str, Any]:
        problem = store.get_problem(problem_id)
        if not problem:
            raise HTTPException(404, f"no problem with id={problem_id}")
        sims = find_similar_problems(
            problem.fingerprint, graph=graph, store=store, k=k,
            exclude_problem_id=problem_id,
        )
        return {
            "problem_id": problem_id,
            "k": k,
            "items": [s.to_dict() for s in sims],
        }

    # --- DB inspector --------------------------------------------------------

    @app.get("/attempts")
    def list_attempts(limit: int = 50) -> dict[str, Any]:
        with store._cursor() as cur:  # noqa: SLF001 — intentionally simple
            rows = cur.execute(
                "SELECT * FROM attempts ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        items = [store._row_to_attempt(r) for r in rows]  # noqa: SLF001
        return {"items": [_attempt_to_dict(a) for a in items], "limit": limit}

    @app.get("/tool_outcomes")
    def list_tool_outcomes(limit: int = 200) -> dict[str, Any]:
        with store._cursor() as cur:  # noqa: SLF001
            rows = cur.execute(
                """
                SELECT signature, tool, approach, n_attempts, n_success,
                       n_verified, total_time_ms, updated_at
                FROM tool_outcomes
                ORDER BY n_attempts DESC, updated_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        out = []
        for r in rows:
            n = max(int(r["n_attempts"]), 1)
            out.append({
                "signature": r["signature"],
                "tool": r["tool"],
                "approach": r["approach"],
                "n_attempts": int(r["n_attempts"]),
                "n_success": int(r["n_success"]),
                "n_verified": int(r["n_verified"]),
                "success_rate": int(r["n_success"]) / n,
                "verify_rate": int(r["n_verified"]) / n,
                "avg_time_ms": float(r["total_time_ms"]) / n,
                "updated_at": r["updated_at"],
            })
        return {"items": out, "limit": limit}

    # --- Graph ---------------------------------------------------------------

    @app.get("/graph")
    def get_graph(max_problems: int = 200) -> dict[str, Any]:
        return graph.to_cytoscape(max_problems=max_problems)

    @app.get("/graph/around/{problem_id}")
    def graph_around(problem_id: int, radius: int = 1) -> dict[str, Any]:
        return graph.subgraph_around_problem(problem_id, radius=radius)

    @app.get("/graph/stats")
    def graph_stats() -> dict[str, Any]:
        s = graph.stats()
        s["threshold"] = graph.threshold
        s["graph_path"] = str(graph.path)
        return s

    # --- Stats ---------------------------------------------------------------

    @app.get("/db/stats")
    def stats() -> dict[str, Any]:
        s = store.stats()
        s["graph"] = graph.stats()
        return s

    @app.get("/config")
    def config() -> dict[str, Any]:
        return {
            "ollama_enabled": CONFIG.ollama_enabled,
            "ollama_model": CONFIG.ollama_model,
            "similarity_threshold": CONFIG.similarity_threshold,
            "similar_top_k": CONFIG.similar_top_k,
            "tool_timeout_s": CONFIG.tool_timeout_s,
        }

    # --- Frontend (static UI) -----------------------------------------------
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        static_dir = frontend_dir / "static"
        if static_dir.is_dir():
            app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

    return app


app = create_app()
