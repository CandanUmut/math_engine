"""Runtime settings: layered config with persistent JSON overrides
(Phase 6)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pru_math import settings as runtime_settings
from pru_math.api import create_app
from pru_math.graph import RelationalGraph
from pru_math.store import Store


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch):
    """Each test gets a private settings file so we don't pollute the
    real data/ directory or leak state between cases."""
    monkeypatch.setenv("PRU_SETTINGS_PATH", str(tmp_path / "settings.json"))
    runtime_settings.reload_for_tests()
    yield
    runtime_settings.reload_for_tests()


def test_default_value_comes_from_config(tmp_path):
    # Without overrides, get() returns the env-loaded default.
    assert runtime_settings.get("max_attempts") >= 1


def test_set_many_persists_and_returns_snapshot():
    snap = runtime_settings.set_many({"max_attempts": 7, "cross_verify": True})
    assert snap["max_attempts"] == 7
    assert snap["cross_verify"] is True
    runtime_settings.reload_for_tests()
    assert runtime_settings.get("max_attempts") == 7
    assert runtime_settings.get("cross_verify") is True


def test_set_many_rejects_unknown_key():
    with pytest.raises(KeyError):
        runtime_settings.set_many({"nonexistent": 1})


def test_set_many_rejects_bad_type():
    with pytest.raises(ValueError):
        runtime_settings.set_many({"max_attempts": "not-a-number"})


def test_set_many_validates_int_range():
    with pytest.raises(ValueError):
        runtime_settings.set_many({"max_attempts": 0})


def test_set_many_validates_unit_interval():
    with pytest.raises(ValueError):
        runtime_settings.set_many({"similarity_threshold": 1.5})


def test_reset_drops_overrides():
    runtime_settings.set_many({"max_attempts": 9})
    runtime_settings.reset(["max_attempts"])
    runtime_settings.reload_for_tests()
    # back to default
    assert runtime_settings.get("max_attempts") != 9


def test_get_config_endpoint(tmp_path):
    store = Store(db_path=tmp_path / "s.sqlite")
    graph = RelationalGraph(path=tmp_path / "s.gpickle", autosave=False)
    c = TestClient(create_app(store=store, graph=graph))
    data = c.get("/config").json()
    assert "settable_keys" in data
    assert "max_attempts" in data
    assert "cross_verify" in data


def test_put_config_endpoint_persists(tmp_path):
    store = Store(db_path=tmp_path / "s.sqlite")
    graph = RelationalGraph(path=tmp_path / "s.gpickle", autosave=False)
    c = TestClient(create_app(store=store, graph=graph))
    r = c.put("/config", json={"max_attempts": 5})
    assert r.status_code == 200
    data = r.json()
    assert data["max_attempts"] == 5


def test_put_config_endpoint_400_on_bad_value(tmp_path):
    store = Store(db_path=tmp_path / "s.sqlite")
    graph = RelationalGraph(path=tmp_path / "s.gpickle", autosave=False)
    c = TestClient(create_app(store=store, graph=graph))
    r = c.put("/config", json={"max_attempts": -1})
    assert r.status_code == 400
