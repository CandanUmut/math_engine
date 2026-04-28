"""Database export / import round-trip (Phase 6)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pru_math.api import create_app
from pru_math.exporter import export_bundle, import_bundle
from pru_math.graph import RelationalGraph
from pru_math.reasoner import Reasoner
from pru_math.store import Store


def _seed(store: Store, graph: RelationalGraph) -> None:
    r = Reasoner(store=store, graph=graph)
    for p in ["Eq(x**2 - 4, 0)", "sin(x)**2 + cos(x)**2", "1"]:
        r.solve(p)


def test_round_trip_preserves_problems(tmp_path):
    src_store = Store(db_path=tmp_path / "src.sqlite")
    src_graph = RelationalGraph(path=tmp_path / "src.gpickle", autosave=False)
    _seed(src_store, src_graph)
    bundle = export_bundle(src_store, src_graph)

    dst_store = Store(db_path=tmp_path / "dst.sqlite")
    dst_graph = RelationalGraph(path=tmp_path / "dst.gpickle", autosave=False)
    counts = import_bundle(dst_store, dst_graph, bundle)
    assert counts["problems"] == len(src_store.list_problems())
    assert counts["graph_nodes"] == src_graph.node_count()


def test_import_replaces_existing_data(tmp_path):
    src = (tmp_path / "src.sqlite", tmp_path / "src.gpickle")
    dst = (tmp_path / "dst.sqlite", tmp_path / "dst.gpickle")
    src_store = Store(db_path=src[0])
    src_graph = RelationalGraph(path=src[1], autosave=False)
    _seed(src_store, src_graph)
    bundle = export_bundle(src_store, src_graph)

    dst_store = Store(db_path=dst[0])
    dst_graph = RelationalGraph(path=dst[1], autosave=False)
    # Seed dst with different data first to ensure it gets replaced.
    Reasoner(store=dst_store, graph=dst_graph).solve("Eq(x**3 - 1, 0)")
    assert len(dst_store.list_problems()) >= 1
    import_bundle(dst_store, dst_graph, bundle)
    pretty_inputs = {p.raw_input for p in dst_store.list_problems()}
    assert "Eq(x**3 - 1, 0)" not in pretty_inputs
    assert "Eq(x**2 - 4, 0)" in pretty_inputs


def test_import_rejects_unsupported_schema(tmp_path):
    store = Store(db_path=tmp_path / "x.sqlite")
    graph = RelationalGraph(path=tmp_path / "x.gpickle", autosave=False)
    with pytest.raises(ValueError):
        import_bundle(store, graph, {"schema_version": 999, "tables": {}})


def test_import_rolls_back_on_bad_row(tmp_path):
    store = Store(db_path=tmp_path / "x.sqlite")
    graph = RelationalGraph(path=tmp_path / "x.gpickle", autosave=False)
    Reasoner(store=store, graph=graph).solve("Eq(x**2 - 4, 0)")
    pre = len(store.list_problems())
    bad = {
        "schema_version": 1,
        "tables": {
            "problems": ["this isn't a dict"],
        },
    }
    with pytest.raises(Exception):
        import_bundle(store, graph, bad)
    # The transaction must have rolled back, leaving the original row in place.
    assert len(store.list_problems()) == pre


def test_export_endpoint_via_testclient(tmp_path):
    store = Store(db_path=tmp_path / "x.sqlite")
    graph = RelationalGraph(path=tmp_path / "x.gpickle", autosave=False)
    c = TestClient(create_app(store=store, graph=graph))
    c.post("/solve", json={"text": "Eq(x**2 - 4, 0)"})
    bundle = c.get("/db/export").json()
    assert "tables" in bundle and "graph_pickle_b64" in bundle
    r = c.post("/db/import", json=bundle)
    assert r.status_code == 200
