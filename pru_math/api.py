"""FastAPI layer exposing the Phase 1+2+3+4 reasoner.

Endpoints
---------
- ``POST /solve``                          — solve a math problem
- ``GET  /problems``                       — list problems (recent first)
- ``GET  /problems/{id}``                  — one problem + its attempts
- ``GET  /problems/{id}/similar?k=5``      — K most similar past problems
- ``GET  /attempts``                       — list attempts (recent first)
- ``GET  /attempts/timeline``              — recent attempts joined to problems (for charts)
- ``GET  /tool_outcomes``                  — aggregated tool stats
- ``GET  /learner/rank``                   — preview the learner's rank for a (sig, type)
- ``GET  /tools``                          — registered tools and their availability (Phase 4)
- ``GET  /graph``                          — full graph as cytoscape JSON
- ``GET  /graph/around/{id}?radius=1``     — subgraph around a problem
- ``GET  /graph/stats``                    — node/edge counts by kind
- ``GET  /db/stats``                       — store stats
- ``GET  /config``                         — runtime configuration snapshot
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import CONFIG
from .graph import RelationalGraph
from .hypothesizer import Hypothesizer
from .learner import Learner
from .reasoner import Reasoner
from .retrieval import find_similar_problems
from .store import HypothesisRecord, Store
from .tools import sympy_tool
from .tools.registry import ToolRegistry, default_registry


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
        "cross_verify_tool": a.cross_verify_tool,
        "cross_verify_status": a.cross_verify_status,
        "cross_verify_detail": a.cross_verify_detail,
        "cross_verify_time_ms": a.cross_verify_time_ms,
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


def _hypothesis_to_dict(h: HypothesisRecord) -> dict[str, Any]:
    return {
        "id": h.id, "kind": h.kind, "claim": h.claim,
        "claim_repr": h.claim_repr, "fingerprint": h.fingerprint,
        "evidence": h.evidence, "status": h.status, "method": h.method,
        "verification_detail": h.verification_detail,
        "rule_node": h.rule_node,
        "created_at": h.created_at, "updated_at": h.updated_at,
    }


def create_app(store: Store | None = None,
               graph: RelationalGraph | None = None,
               learner: Learner | None = None,
               registry: ToolRegistry | None = None,
               hypothesizer: Hypothesizer | None = None) -> FastAPI:
    store = store or Store()
    graph = graph or RelationalGraph()
    learner = learner or Learner(store)
    registry = registry or default_registry()
    hypothesizer = hypothesizer or Hypothesizer(store=store, graph=graph)
    reasoner = Reasoner(store=store, graph=graph, learner=learner, registry=registry)

    app = FastAPI(title="PRU Math Engine", version="0.5.0")

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
        records = store.list_tool_outcomes(limit=limit)
        return {
            "items": [
                {
                    "signature": r.signature,
                    "tool": r.tool,
                    "approach": r.approach,
                    "n_attempts": r.n_attempts,
                    "n_success": r.n_success,
                    "n_verified": r.n_verified,
                    "success_rate": r.success_rate,
                    "verify_rate": r.verify_rate,
                    "avg_time_ms": r.avg_time_ms,
                    "failure_modes": r.failure_modes,
                    "updated_at": r.updated_at,
                }
                for r in records
            ],
            "limit": limit,
        }

    @app.get("/attempts/timeline")
    def attempts_timeline(limit: int = 500) -> dict[str, Any]:
        return {"items": store.attempt_timeline(limit=limit), "limit": limit}

    # --- Learner (Phase 3) --------------------------------------------------

    @app.get("/learner/rank")
    def learner_rank(
        problem_type: str = Query(..., description="Problem type to look up approaches for"),
        signature: str = Query("", description="Optional fingerprint signature; empty = type-level only"),
    ) -> dict[str, Any]:
        candidates = [
            (sympy_tool.TOOL_NAME, name)
            for name in sympy_tool.candidate_approaches(problem_type)
        ]
        ranked = learner.rank(
            signature=signature, problem_type=problem_type, candidates=candidates,
        )
        return {
            "problem_type": problem_type,
            "signature": signature,
            "exploration_c": learner.exploration_c,
            "candidates": [c.to_dict() for c in ranked],
        }

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
        s["hypotheses"] = store.hypothesis_counts()
        return s

    # --- Hypotheses (Phase 5) ----------------------------------------------

    @app.get("/hypotheses")
    def list_hypotheses(
        status: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        items = store.list_hypotheses(status=status, kind=kind, limit=limit)
        return {
            "items": [_hypothesis_to_dict(h) for h in items],
            "counts": store.hypothesis_counts(),
            "limit": limit,
        }

    @app.get("/hypotheses/{hypothesis_id}")
    def get_hypothesis(hypothesis_id: int) -> dict[str, Any]:
        rec = store.get_hypothesis(hypothesis_id)
        if not rec:
            raise HTTPException(404, f"no hypothesis with id={hypothesis_id}")
        return _hypothesis_to_dict(rec)

    @app.post("/hypotheses/scan")
    def scan_hypotheses(verify: bool = True) -> dict[str, Any]:
        results = hypothesizer.scan(verify=verify)
        return {
            "scanned": len(results),
            "items": [r.to_dict() for r in results],
            "counts": store.hypothesis_counts(),
        }

    @app.post("/hypotheses/{hypothesis_id}/verify")
    def reverify_hypothesis(hypothesis_id: int) -> dict[str, Any]:
        rec = store.get_hypothesis(hypothesis_id)
        if not rec:
            raise HTTPException(404, f"no hypothesis with id={hypothesis_id}")
        from .hypothesizer import record_to_hypothesis
        h = record_to_hypothesis(rec)
        hypothesizer.verify(h)
        out = store.get_hypothesis(hypothesis_id)
        return _hypothesis_to_dict(out)  # type: ignore[arg-type]

    @app.get("/config")
    def config() -> dict[str, Any]:
        return {
            "ollama_enabled": CONFIG.ollama_enabled,
            "ollama_model": CONFIG.ollama_model,
            "similarity_threshold": CONFIG.similarity_threshold,
            "similar_top_k": CONFIG.similar_top_k,
            "tool_timeout_s": CONFIG.tool_timeout_s,
            "max_attempts": CONFIG.max_attempts,
            "learner_exploration": CONFIG.learner_exploration,
            "cross_verify": CONFIG.cross_verify,
        }

    @app.get("/tools")
    def list_tools() -> dict[str, Any]:
        return {
            "items": registry.status(),
            "available": [t.name for t in registry.available_tools()],
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
