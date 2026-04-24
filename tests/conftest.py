"""Shared test fixtures.

Every test that touches the store gets a fresh temporary SQLite file so
tests don't contaminate each other and don't touch the real ``data/``
directory. We also force OLLAMA_ENABLED=false so parser tests don't hit
the network by accident.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the repo importable without installing.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("OLLAMA_ENABLED", "false")

import pytest  # noqa: E402

from pru_math.store import Store  # noqa: E402


@pytest.fixture()
def tmp_store(tmp_path: Path) -> Store:
    db = tmp_path / "pru.sqlite"
    return Store(db_path=db)
