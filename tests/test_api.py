"""FastAPI layer smoke tests using the ASGI TestClient."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pru_math.api import create_app
from pru_math.graph import RelationalGraph
from pru_math.store import Store


@pytest.fixture()
def client(tmp_path: Path):
    store = Store(db_path=tmp_path / "api.sqlite")
    graph = RelationalGraph(path=tmp_path / "api_graph.gpickle", autosave=False)
    app = create_app(store=store, graph=graph)
    return TestClient(app), store, graph


def test_solve_endpoint_returns_trace(client):
    c, *_ = client
    r = c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["verification_status"] == "verified"
    kinds = [s["kind"] for s in data["trace"]]
    for k in ("tool_call", "verify", "retrieval", "graph_update"):
        assert k in kinds


def test_solve_endpoint_returns_similar_field(client):
    c, *_ = client
    # First solve seeds the graph; second similar one should populate `similar`.
    c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"})
    r = c.post("/solve", json={"text": "Eq(x**2 - 7*x + 12, 0)"}).json()
    assert "similar" in r
    assert isinstance(r["similar"], list)
    assert r["similar"]


def test_list_and_get_problem(client):
    c, *_ = client
    c.post("/solve", json={"text": "sin(x)**2 + cos(x)**2"})
    lst = c.get("/problems").json()
    assert lst["items"]
    pid = lst["items"][0]["id"]
    detail = c.get(f"/problems/{pid}").json()
    assert detail["problem"]["id"] == pid
    # SIMPLIFY may try multiple approaches when early ones return
    # ``no_change`` (Phase 11 merge); we just need at least one attempt.
    assert detail["attempts"]


def test_similar_endpoint(client):
    c, *_ = client
    c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"})
    r2 = c.post("/solve", json={"text": "Eq(x**2 - 7*x + 12, 0)"}).json()
    pid = r2["problem_id"]
    s = c.get(f"/problems/{pid}/similar?k=5").json()
    assert s["problem_id"] == pid
    # The first quadratic should appear, and the problem itself excluded.
    ids = [item["problem"]["id"] for item in s["items"]]
    assert pid not in ids
    assert ids


def test_attempts_endpoint(client):
    c, *_ = client
    c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"})
    r = c.get("/attempts").json()
    assert r["items"]
    # Phase 4: any registered tool may be chosen; just confirm the row carries one.
    assert r["items"][0]["tool"]
    assert r["items"][0]["approach"]


def test_tool_outcomes_endpoint(client):
    c, *_ = client
    c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"})
    r = c.get("/tool_outcomes").json()
    assert r["items"]
    o = r["items"][0]
    assert "success_rate" in o and "verify_rate" in o and "avg_time_ms" in o


def test_graph_endpoint(client):
    c, *_ = client
    c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"})
    g = c.get("/graph").json()
    kinds = {n["data"]["kind"] for n in g["nodes"]}
    assert "problem" in kinds
    assert "tool" in kinds
    assert "problem_type" in kinds
    assert "signature" in kinds


def test_graph_around_endpoint(client):
    c, *_ = client
    r = c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"}).json()
    pid = r["problem_id"]
    sub = c.get(f"/graph/around/{pid}?radius=1").json()
    assert sub["nodes"]
    # Problem node must be in the subgraph.
    ids = {n["data"]["id"] for n in sub["nodes"]}
    assert f"p:{pid}" in ids


def test_graph_stats_endpoint(client):
    c, *_ = client
    c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"})
    s = c.get("/graph/stats").json()
    assert s["nodes"] >= 3
    assert "threshold" in s


def test_stats(client):
    c, *_ = client
    c.post("/solve", json={"text": "Integral(x**2, (x, 0, 1))"})
    s = c.get("/db/stats").json()
    assert s["problems"] >= 1
    assert s["attempts"] >= 1
    assert "sympy" in s["by_tool"]
    assert "graph" in s


def test_missing_problem_404(client):
    c, *_ = client
    r = c.get("/problems/99999")
    assert r.status_code == 404
