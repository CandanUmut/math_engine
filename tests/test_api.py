"""FastAPI layer smoke tests using the ASGI TestClient."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pru_math.api import create_app
from pru_math.store import Store


@pytest.fixture()
def client(tmp_path: Path):
    store = Store(db_path=tmp_path / "api.sqlite")
    app = create_app(store=store)
    return TestClient(app), store


def test_solve_endpoint_returns_trace(client):
    c, _ = client
    r = c.post("/solve", json={"text": "Eq(x**2 - 5*x + 6, 0)"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["verification_status"] == "verified"
    kinds = [s["kind"] for s in data["trace"]]
    assert "tool_call" in kinds and "verify" in kinds


def test_list_and_get_problem(client):
    c, _ = client
    c.post("/solve", json={"text": "sin(x)**2 + cos(x)**2"})
    lst = c.get("/problems").json()
    assert lst["items"]
    pid = lst["items"][0]["id"]
    detail = c.get(f"/problems/{pid}").json()
    assert detail["problem"]["id"] == pid
    assert len(detail["attempts"]) == 1


def test_stats(client):
    c, _ = client
    c.post("/solve", json={"text": "Integral(x**2, (x, 0, 1))"})
    s = c.get("/db/stats").json()
    assert s["problems"] >= 1
    assert s["attempts"] >= 1
    assert "sympy" in s["by_tool"]


def test_missing_problem_404(client):
    c, _ = client
    r = c.get("/problems/99999")
    assert r.status_code == 404
