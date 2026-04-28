"""Phase 8: notebook sessions + explain endpoint."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pru_math.api import create_app
from pru_math.graph import RelationalGraph
from pru_math.reasoner import Reasoner
from pru_math.store import Store


# ── Store helpers ───────────────────────────────────────────────────


def test_create_and_get_session(tmp_store: Store):
    sid = tmp_store.create_session(title="trigonometry", notes_markdown="warmups")
    rec = tmp_store.get_session(sid)
    assert rec is not None
    assert rec.title == "trigonometry"
    assert rec.notes_markdown == "warmups"


def test_list_sessions_orders_by_updated_at(tmp_store: Store):
    a = tmp_store.create_session(title="A")
    b = tmp_store.create_session(title="B")
    tmp_store.update_session(session_id=a, notes_markdown="touched")
    listed = tmp_store.list_sessions()
    assert listed[0].id == a    # 'A' was just touched, must come first


def test_update_session_partial(tmp_store: Store):
    sid = tmp_store.create_session(title="orig", notes_markdown="x")
    tmp_store.update_session(session_id=sid, title="renamed")
    rec = tmp_store.get_session(sid)
    assert rec.title == "renamed"
    assert rec.notes_markdown == "x"   # untouched


def test_delete_session_unlinks_problems(tmp_store: Store, tmp_graph: RelationalGraph):
    sid = tmp_store.create_session(title="s")
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(x**2 - 4, 0)", session_id=sid)
    pid = out.problem_id
    assert tmp_store.get_problem(pid).session_id == sid
    assert tmp_store.delete_session(sid)
    # Session is gone, but the problem survives with session_id=NULL.
    assert tmp_store.get_session(sid) is None
    assert tmp_store.get_problem(pid).session_id is None


def test_set_problem_session(tmp_store: Store, tmp_graph: RelationalGraph):
    sid = tmp_store.create_session(title="s")
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(x**2 - 4, 0)")     # solve with NO session
    assert tmp_store.get_problem(out.problem_id).session_id is None
    tmp_store.set_problem_session(problem_id=out.problem_id, session_id=sid)
    assert tmp_store.get_problem(out.problem_id).session_id == sid
    tmp_store.set_problem_session(problem_id=out.problem_id, session_id=None)
    assert tmp_store.get_problem(out.problem_id).session_id is None


def test_list_problems_by_session(tmp_store: Store, tmp_graph: RelationalGraph):
    sid = tmp_store.create_session(title="s")
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    r.solve("Eq(x**2 - 4, 0)", session_id=sid)
    r.solve("sin(x)**2 + cos(x)**2", session_id=sid)
    r.solve("Eq(x**2 - 9, 0)")     # not in this session
    problems = tmp_store.list_problems_by_session(sid)
    assert len(problems) == 2


# ── API ─────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path):
    store = Store(db_path=tmp_path / "s.sqlite")
    graph = RelationalGraph(path=tmp_path / "s.gpickle", autosave=False)
    return TestClient(create_app(store=store, graph=graph)), store, graph


def test_session_crud_endpoints(client):
    c, *_ = client
    # create
    r = c.post("/sessions", json={"title": "research"}).json()
    assert r["title"] == "research"
    sid = r["id"]
    # list
    listed = c.get("/sessions").json()
    assert any(item["id"] == sid for item in listed["items"])
    # get
    one = c.get(f"/sessions/{sid}").json()
    assert one["session"]["id"] == sid
    assert one["problems"] == []
    # update
    upd = c.put(f"/sessions/{sid}",
                json={"title": "research v2", "notes_markdown": "hi"}).json()
    assert upd["title"] == "research v2"
    # delete
    d = c.delete(f"/sessions/{sid}")
    assert d.status_code == 200
    assert c.get(f"/sessions/{sid}").status_code == 404


def test_solve_attaches_to_session(client):
    c, *_ = client
    sid = c.post("/sessions", json={"title": "s"}).json()["id"]
    out = c.post("/solve", json={
        "text": "Eq(x**2 - 4, 0)", "session_id": sid,
    }).json()
    assert out["problem_id"] is not None
    bundle = c.get(f"/sessions/{sid}").json()
    pids = [p["id"] for p in bundle["problems"]]
    assert out["problem_id"] in pids


def test_attach_problem_to_session_endpoint(client):
    c, *_ = client
    sid = c.post("/sessions", json={"title": "s"}).json()["id"]
    out = c.post("/solve", json={"text": "Eq(x**2 - 4, 0)"}).json()
    pid = out["problem_id"]
    r = c.post(f"/problems/{pid}/session", json={"session_id": sid})
    assert r.status_code == 200
    assert r.json()["session_id"] == sid
    # Detach
    r = c.post(f"/problems/{pid}/session", json={"session_id": None})
    assert r.status_code == 200
    assert r.json()["session_id"] is None


def test_create_session_400_on_empty_title(client):
    c, *_ = client
    r = c.post("/sessions", json={"title": "  "})
    assert r.status_code == 400


def test_db_stats_reports_session_count(client):
    c, *_ = client
    c.post("/sessions", json={"title": "s1"})
    c.post("/sessions", json={"title": "s2"})
    stats = c.get("/db/stats").json()
    assert stats["sessions"] == 2


# ── /explain ────────────────────────────────────────────────────────


def test_explain_falls_back_when_ollama_disabled(client, monkeypatch):
    # OLLAMA_ENABLED is already false in conftest.
    c, *_ = client
    out = c.post("/solve", json={"text": "Eq(x**2 - 4, 0)"}).json()
    pid = out["problem_id"]
    r = c.post(f"/explain/{pid}").json()
    assert r["source"] == "deterministic"
    assert "Eq(x**2 - 4, 0)" in r["text"] or "x**2" in r["text"]


def test_explain_uses_ollama_when_mocked(client, monkeypatch):
    from types import SimpleNamespace
    c, *_ = client
    out = c.post("/solve", json={"text": "Eq(x**2 - 4, 0)"}).json()
    pid = out["problem_id"]
    fake_config = SimpleNamespace(
        ollama_enabled=True, ollama_model="mock",
        ollama_host="http://localhost:11434", tool_timeout_s=5,
    )
    monkeypatch.setattr("pru_math.narrator.CONFIG", fake_config)
    fake_resp = type("R", (), {
        "raise_for_status": lambda self: None,
        "json": lambda self: {"message": {"content": "mocked narration"}},
    })()
    with patch("pru_math.narrator.httpx.post", return_value=fake_resp):
        r = c.post(f"/explain/{pid}").json()
    assert r["source"] == "ollama"
    assert r["text"] == "mocked narration"


def test_explain_404_unknown_problem(client):
    c, *_ = client
    r = c.post("/explain/99999")
    assert r.status_code == 404
