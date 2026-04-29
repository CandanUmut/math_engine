"""Phase 10: distribution-readiness checks.

- Read-only mode actually blocks writes
- The server console-script entry point imports cleanly
- pyproject.toml exposes the right metadata and entry points
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pru_math.api import create_app
from pru_math.graph import RelationalGraph
from pru_math.store import Store


# ── Read-only mode ──────────────────────────────────────────────────


@pytest.fixture()
def ro_client(tmp_path: Path, monkeypatch):
    """Client with read-only mode flipped on. We monkeypatch the
    frozen CONFIG by replacing the api module's CONFIG reference with
    a SimpleNamespace."""
    store = Store(db_path=tmp_path / "ro.sqlite")
    graph = RelationalGraph(path=tmp_path / "ro.gpickle", autosave=False)
    fake = SimpleNamespace(read_only=True)
    monkeypatch.setattr("pru_math.api.CONFIG", fake)
    return TestClient(create_app(store=store, graph=graph))


def test_get_endpoints_work_in_read_only(ro_client):
    # GET requests must not be blocked.
    r = ro_client.get("/db/stats")
    assert r.status_code == 200


def test_post_solve_blocked_in_read_only(ro_client):
    r = ro_client.post("/solve", json={"text": "Eq(x**2 - 4, 0)"})
    assert r.status_code == 403
    body = r.json()
    assert "read-only" in body["detail"].lower()


def test_put_config_blocked_in_read_only(ro_client):
    r = ro_client.put("/config", json={"max_attempts": 5})
    assert r.status_code == 403


def test_delete_session_blocked_in_read_only(ro_client):
    r = ro_client.delete("/sessions/1")
    assert r.status_code == 403


def test_post_hypotheses_scan_blocked_in_read_only(ro_client):
    r = ro_client.post("/hypotheses/scan")
    assert r.status_code == 403


def test_config_endpoint_reports_read_only_flag(ro_client):
    data = ro_client.get("/config").json()
    assert data["read_only"] is True


# ── Read-write mode (regression) ────────────────────────────────────


@pytest.fixture()
def rw_client(tmp_path: Path):
    store = Store(db_path=tmp_path / "rw.sqlite")
    graph = RelationalGraph(path=tmp_path / "rw.gpickle", autosave=False)
    return TestClient(create_app(store=store, graph=graph))


def test_writes_pass_through_when_read_only_off(rw_client):
    # Default CONFIG.read_only is False (env not set).
    r = rw_client.post("/solve", json={"text": "Eq(x**2 - 4, 0)"})
    assert r.status_code == 200


# ── Server console script ───────────────────────────────────────────


def test_server_main_with_help_does_not_start_server(capsys):
    """The argparse --help branch must exit 0 and not bring up uvicorn."""
    from pru_math.server import main
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "pru-math-server" in captured.out


def test_server_main_calls_uvicorn_run():
    """Without --help, main() should hand off to uvicorn.run with the
    expected app target."""
    from pru_math import server as srv
    with patch("uvicorn.run") as fake_run:
        srv.main(["--host", "127.0.0.1", "--port", "12345"])
    assert fake_run.called
    args, kwargs = fake_run.call_args
    assert args[0] == "pru_math.api:app"
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 12345


# ── Package metadata ────────────────────────────────────────────────


def test_pyproject_declares_entry_points():
    """pyproject.toml must declare the two console scripts users will
    look for after `pip install`."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib    # type: ignore[no-redef]

    repo_root = Path(__file__).resolve().parent.parent
    with (repo_root / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    scripts = (data.get("project") or {}).get("scripts", {})
    assert "pru-math" in scripts
    assert "pru-math-server" in scripts
    assert scripts["pru-math"].startswith("pru_math.")
    assert scripts["pru-math-server"].startswith("pru_math.")


def test_pyproject_has_required_metadata():
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib    # type: ignore[no-redef]
    repo_root = Path(__file__).resolve().parent.parent
    with (repo_root / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    project = data.get("project") or {}
    assert project["name"] == "pru-math-engine"
    assert "version" in project
    assert "description" in project
    assert project["requires-python"].startswith(">=3.")
    deps = set(project.get("dependencies", []))
    # A few hard requirements we lean on.
    assert any(d.startswith("sympy") for d in deps)
    assert any(d.startswith("fastapi") for d in deps)
    assert any(d.startswith("networkx") for d in deps)


def test_frontend_assets_are_inside_the_package():
    """The frontend must live under ``pru_math/frontend/`` so
    `pip install` bundles it via package_data."""
    from pru_math import api as api_mod
    package_dir = Path(api_mod.__file__).resolve().parent
    assert (package_dir / "frontend" / "index.html").is_file()
    assert (package_dir / "frontend" / "static" / "app.js").is_file()
    assert (package_dir / "frontend" / "static" / "style.css").is_file()
