"""Auto-scan: every N solves the reasoner triggers hypothesizer.scan() in
process and surfaces it as a trace step (Phase 6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pru_math import settings as runtime_settings
from pru_math.graph import RelationalGraph
from pru_math.hypothesizer import Hypothesizer
from pru_math.reasoner import Reasoner
from pru_math.store import Store


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRU_SETTINGS_PATH", str(tmp_path / "settings.json"))
    runtime_settings.reload_for_tests()
    yield
    runtime_settings.reload_for_tests()


def _setup(tmp_path: Path):
    store = Store(db_path=tmp_path / "as.sqlite")
    graph = RelationalGraph(path=tmp_path / "as.gpickle", autosave=False)
    h = Hypothesizer(store=store, graph=graph)
    return Reasoner(store=store, graph=graph, hypothesizer=h), store, graph


def test_auto_scan_disabled_by_default(tmp_path):
    runtime_settings.set_many({"auto_scan_every_n": 0})
    r, store, _ = _setup(tmp_path)
    out = r.solve("Eq(x**2 - 4, 0)")
    kinds = [s.kind for s in out.trace]
    assert "auto_scan" not in kinds
    assert store.list_hypotheses() == []


def test_auto_scan_fires_after_threshold(tmp_path):
    runtime_settings.set_many({"auto_scan_every_n": 2})
    r, store, _ = _setup(tmp_path)
    out1 = r.solve("sin(x)**2 + cos(x)**2")
    assert all(s.kind != "auto_scan" for s in out1.trace), \
        "auto-scan should NOT fire on the first solve (threshold=2)"
    out2 = r.solve("1")
    kinds = [s.kind for s in out2.trace]
    assert "auto_scan" in kinds
    # And by the time the scan runs, the Pythagorean identity should be
    # discoverable.
    hyps = store.list_hypotheses()
    assert hyps, "expected at least one hypothesis to be persisted by auto-scan"


def test_auto_scan_resets_counter(tmp_path):
    runtime_settings.set_many({"auto_scan_every_n": 2})
    r, _store, _ = _setup(tmp_path)
    r.solve("sin(x)**2 + cos(x)**2")
    out2 = r.solve("1")
    assert any(s.kind == "auto_scan" for s in out2.trace)
    out3 = r.solve("Eq(x**2 - 4, 0)")
    # Counter should have reset after firing on solve #2; solve #3 shouldn't fire.
    assert all(s.kind != "auto_scan" for s in out3.trace)
