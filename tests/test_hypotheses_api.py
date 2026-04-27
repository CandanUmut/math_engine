"""FastAPI smoke for the Phase 5 hypothesis endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pru_math.api import create_app
from pru_math.graph import RelationalGraph
from pru_math.store import Store


@pytest.fixture()
def client(tmp_path: Path):
    store = Store(db_path=tmp_path / "hyp.sqlite")
    graph = RelationalGraph(path=tmp_path / "hyp_graph.gpickle", autosave=False)
    app = create_app(store=store, graph=graph)
    return TestClient(app), store, graph


def _seed(c):
    for p in ["sin(x)**2 + cos(x)**2", "1"]:
        c.post("/solve", json={"text": p})


def test_scan_returns_verified_identity(client):
    c, *_ = client
    _seed(c)
    r = c.post("/hypotheses/scan").json()
    assert r["scanned"] >= 1
    assert any(it["status"] == "verified" and it["kind"] == "identity"
               for it in r["items"])


def test_list_filters_by_status(client):
    c, *_ = client
    _seed(c)
    c.post("/hypotheses/scan")
    verified = c.get("/hypotheses?status=verified").json()
    refuted = c.get("/hypotheses?status=refuted").json()
    assert verified["items"]
    assert all(it["status"] == "verified" for it in verified["items"])
    assert all(it["status"] == "refuted" for it in refuted["items"])


def test_get_one_and_reverify(client):
    c, *_ = client
    _seed(c)
    r = c.post("/hypotheses/scan").json()
    hid = r["items"][0]["id"]
    one = c.get(f"/hypotheses/{hid}").json()
    assert one["id"] == hid
    re = c.post(f"/hypotheses/{hid}/verify").json()
    assert re["id"] == hid
    assert re["status"] in {"verified", "refuted", "inconclusive", "proposed"}


def test_db_stats_includes_hypotheses(client):
    c, *_ = client
    _seed(c)
    c.post("/hypotheses/scan")
    s = c.get("/db/stats").json()
    assert "hypotheses" in s
    assert isinstance(s["hypotheses"], dict)


def test_get_404_for_unknown_id(client):
    c, *_ = client
    r = c.get("/hypotheses/99999")
    assert r.status_code == 404
