"""Phase 11: notebook view UI.

- DELETE /problems/{id} cascades attempts and removes the graph node
- GET /sessions/{id}/export returns a session-scoped bundle that
  round-trips through POST /db/import
- The served HTML carries every ID the new JS expects
- The bundled package ships marked.js via the index.html CDN tag
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pru_math.api import create_app
from pru_math.exporter import export_session_bundle, import_bundle
from pru_math.graph import RelationalGraph, problem_node
from pru_math.reasoner import Reasoner
from pru_math.store import Store


# ── DELETE /problems/{id} ───────────────────────────────────────────


def test_delete_problem_removes_problem_and_attempts(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(x**2 - 4, 0)")
    pid = out.problem_id
    assert tmp_store.list_attempts(pid)
    assert tmp_store.delete_problem(pid)
    assert tmp_store.get_problem(pid) is None
    assert tmp_store.list_attempts(pid) == []


def test_delete_problem_returns_false_when_missing(tmp_store: Store):
    assert tmp_store.delete_problem(99999) is False


@pytest.fixture()
def client(tmp_path: Path):
    store = Store(db_path=tmp_path / "nb.sqlite")
    graph = RelationalGraph(path=tmp_path / "nb.gpickle", autosave=False)
    return TestClient(create_app(store=store, graph=graph)), store, graph


def test_delete_problem_endpoint_drops_graph_node(client):
    c, store, graph = client
    out = c.post("/solve", json={"text": "Eq(x**2 - 4, 0)"}).json()
    pid = out["problem_id"]
    assert problem_node(pid) in graph.graph
    r = c.delete(f"/problems/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert problem_node(pid) not in graph.graph
    # The endpoint also tears down the attempt rows.
    assert c.get(f"/problems/{pid}").status_code == 404


def test_delete_problem_endpoint_404_unknown(client):
    c, *_ = client
    r = c.delete("/problems/99999")
    assert r.status_code == 404


# ── /sessions/{id}/export ───────────────────────────────────────────


def test_session_export_contains_only_attached_problems(client):
    c, *_ = client
    sid = c.post("/sessions", json={"title": "calc warmups"}).json()["id"]
    a = c.post("/solve", json={"text": "Eq(x**2 - 4, 0)", "session_id": sid}).json()
    b = c.post("/solve", json={"text": "Integral(x**2, (x, 0, 1))",
                                "session_id": sid}).json()
    c.post("/solve", json={"text": "sin(x)**2 + cos(x)**2"})  # global

    bundle = c.get(f"/sessions/{sid}/export").json()
    assert bundle["session"]["id"] == sid
    pids = {p["id"] for p in bundle["tables"]["problems"]}
    assert pids == {a["problem_id"], b["problem_id"]}
    # Attempts table is restricted to the two problems above.
    a_pids = {row["problem_id"] for row in bundle["tables"]["attempts"]}
    assert a_pids <= pids
    assert "graph_pickle_b64" in bundle


def test_session_export_404_unknown(client):
    c, *_ = client
    r = c.get("/sessions/99999/export")
    assert r.status_code == 404


def test_session_export_round_trips_through_db_import(tmp_path):
    """The exported bundle is a valid input to POST /db/import; importing
    it into a fresh engine re-creates the same problems."""
    src = (tmp_path / "src.sqlite", tmp_path / "src.gpickle")
    src_store = Store(db_path=src[0])
    src_graph = RelationalGraph(path=src[1], autosave=False)
    sid = src_store.create_session(title="export me")
    r = Reasoner(store=src_store, graph=src_graph)
    r.solve("Eq(x**2 - 5*x + 6, 0)", session_id=sid)
    r.solve("Integral(x**2, (x, 0, 1))", session_id=sid)
    r.solve("sin(x)**2 + cos(x)**2")     # not in this session

    bundle = export_session_bundle(src_store, src_graph, sid)

    dst_store = Store(db_path=tmp_path / "dst.sqlite")
    dst_graph = RelationalGraph(path=tmp_path / "dst.gpickle", autosave=False)
    counts = import_bundle(dst_store, dst_graph, bundle)
    # Two problems came across, the global trig identity didn't.
    assert counts["problems"] == 2
    inputs = {p.raw_input for p in dst_store.list_problems()}
    assert "Eq(x**2 - 5*x + 6, 0)" in inputs
    assert "Integral(x**2, (x, 0, 1))" in inputs
    assert "sin(x)**2 + cos(x)**2" not in inputs


# ── Frontend smoke ──────────────────────────────────────────────────


def test_html_carries_notebook_ids(client):
    """If the JS expects an element by id, it must be in the served HTML."""
    c, *_ = client
    html = c.get("/").text
    needed = [
        "tab-notebook", "notebook-empty", "notebook-body",
        "notebook-title", "notebook-meta",
        "notebook-attach-current", "notebook-export",
        "notebook-new-problem",
        "notebook-notes-rendered", "notebook-notes-editor",
        "notebook-notes-edit", "notebook-notes-save", "notebook-notes-cancel",
        "notebook-problems",
    ]
    missing = [n for n in needed if f'id="{n}"' not in html]
    assert missing == []


def test_html_loads_marked_via_cdn(client):
    """marked.js is the only new runtime dependency Phase 11 introduced;
    confirm the CDN script tag actually ships in the HTML."""
    c, *_ = client
    html = c.get("/").text
    assert "marked" in html and "marked.min.js" in html
