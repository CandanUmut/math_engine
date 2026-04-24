"""SQLite storage layer.

Three tables for Phase 1:

- ``problems``       — one row per solve request
- ``attempts``       — one row per tool invocation on a problem
- ``tool_outcomes``  — aggregated (fingerprint_signature, tool, approach) stats

The store is intentionally plain ``sqlite3`` — no ORM — so the schema stays
trivially inspectable with any SQLite browser. Phase 2 will add graph
tables; Phase 5 will add hypotheses.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from .config import CONFIG


SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_input         TEXT    NOT NULL,
    source_format     TEXT    NOT NULL,
    problem_type      TEXT    NOT NULL,
    parsed_expr       TEXT    NOT NULL,
    parsed_pretty     TEXT    NOT NULL,
    fingerprint_json  TEXT    NOT NULL,
    signature         TEXT    NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_problems_signature ON problems(signature);
CREATE INDEX IF NOT EXISTS idx_problems_type ON problems(problem_type);

CREATE TABLE IF NOT EXISTS attempts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id           INTEGER NOT NULL,
    tool                 TEXT    NOT NULL,
    approach             TEXT    NOT NULL,
    success              INTEGER NOT NULL,   -- 0/1
    result_repr          TEXT,
    result_pretty        TEXT,
    verification_status  TEXT,               -- verified | refuted | inconclusive
    verification_detail  TEXT,
    time_ms              REAL    NOT NULL DEFAULT 0,
    error                TEXT,
    steps_json           TEXT    NOT NULL DEFAULT '[]',
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (problem_id) REFERENCES problems(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_attempts_problem ON attempts(problem_id);
CREATE INDEX IF NOT EXISTS idx_attempts_tool ON attempts(tool, approach);

CREATE TABLE IF NOT EXISTS tool_outcomes (
    signature      TEXT    NOT NULL,
    tool           TEXT    NOT NULL,
    approach       TEXT    NOT NULL,
    n_attempts     INTEGER NOT NULL DEFAULT 0,
    n_success      INTEGER NOT NULL DEFAULT 0,
    n_verified     INTEGER NOT NULL DEFAULT 0,
    total_time_ms  REAL    NOT NULL DEFAULT 0,
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (signature, tool, approach)
);
"""


@dataclass
class ProblemRecord:
    id: int
    raw_input: str
    source_format: str
    problem_type: str
    parsed_expr: str
    parsed_pretty: str
    fingerprint: dict[str, Any]
    signature: str
    created_at: str


@dataclass
class AttemptRecord:
    id: int
    problem_id: int
    tool: str
    approach: str
    success: bool
    result_repr: str | None
    result_pretty: str | None
    verification_status: str | None
    verification_detail: str | None
    time_ms: float
    error: str | None
    steps: list[str]
    created_at: str


class Store:
    """Thin, thread-safe wrapper around a SQLite connection."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else CONFIG.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- writes ---------------------------------------------------------

    def insert_problem(
        self,
        *,
        raw_input: str,
        source_format: str,
        problem_type: str,
        parsed_expr: str,
        parsed_pretty: str,
        fingerprint: dict[str, Any],
    ) -> int:
        signature = fingerprint.get("signature") or ""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO problems
                    (raw_input, source_format, problem_type, parsed_expr,
                     parsed_pretty, fingerprint_json, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_input,
                    source_format,
                    problem_type,
                    parsed_expr,
                    parsed_pretty,
                    json.dumps(fingerprint, sort_keys=True),
                    signature,
                ),
            )
            return int(cur.lastrowid)

    def insert_attempt(
        self,
        *,
        problem_id: int,
        tool: str,
        approach: str,
        success: bool,
        result_repr: str | None,
        result_pretty: str | None,
        verification_status: str | None,
        verification_detail: str | None,
        time_ms: float,
        error: str | None,
        steps: Sequence[str],
    ) -> int:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO attempts
                    (problem_id, tool, approach, success, result_repr, result_pretty,
                     verification_status, verification_detail, time_ms, error, steps_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    problem_id, tool, approach, 1 if success else 0,
                    result_repr, result_pretty, verification_status, verification_detail,
                    float(time_ms), error, json.dumps(list(steps)),
                ),
            )
            return int(cur.lastrowid)

    def upsert_tool_outcome(
        self,
        *,
        signature: str,
        tool: str,
        approach: str,
        success: bool,
        verified: bool,
        time_ms: float,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO tool_outcomes
                    (signature, tool, approach, n_attempts, n_success, n_verified, total_time_ms, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, datetime('now'))
                ON CONFLICT(signature, tool, approach) DO UPDATE SET
                    n_attempts    = n_attempts    + 1,
                    n_success     = n_success     + excluded.n_success,
                    n_verified    = n_verified    + excluded.n_verified,
                    total_time_ms = total_time_ms + excluded.total_time_ms,
                    updated_at    = datetime('now')
                """,
                (
                    signature, tool, approach,
                    1 if success else 0,
                    1 if verified else 0,
                    float(time_ms),
                ),
            )

    # --- reads ----------------------------------------------------------

    def _row_to_problem(self, row: sqlite3.Row) -> ProblemRecord:
        return ProblemRecord(
            id=row["id"],
            raw_input=row["raw_input"],
            source_format=row["source_format"],
            problem_type=row["problem_type"],
            parsed_expr=row["parsed_expr"],
            parsed_pretty=row["parsed_pretty"],
            fingerprint=json.loads(row["fingerprint_json"]),
            signature=row["signature"],
            created_at=row["created_at"],
        )

    def _row_to_attempt(self, row: sqlite3.Row) -> AttemptRecord:
        return AttemptRecord(
            id=row["id"],
            problem_id=row["problem_id"],
            tool=row["tool"],
            approach=row["approach"],
            success=bool(row["success"]),
            result_repr=row["result_repr"],
            result_pretty=row["result_pretty"],
            verification_status=row["verification_status"],
            verification_detail=row["verification_detail"],
            time_ms=row["time_ms"],
            error=row["error"],
            steps=json.loads(row["steps_json"] or "[]"),
            created_at=row["created_at"],
        )

    def get_problem(self, problem_id: int) -> ProblemRecord | None:
        with self._cursor() as cur:
            row = cur.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
            return self._row_to_problem(row) if row else None

    def list_problems(self, limit: int = 100, offset: int = 0) -> list[ProblemRecord]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM problems ORDER BY id DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            ).fetchall()
            return [self._row_to_problem(r) for r in rows]

    def list_attempts(self, problem_id: int) -> list[AttemptRecord]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM attempts WHERE problem_id = ? ORDER BY id ASC",
                (int(problem_id),),
            ).fetchall()
            return [self._row_to_attempt(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._cursor() as cur:
            n_problems = cur.execute("SELECT COUNT(*) AS c FROM problems").fetchone()["c"]
            n_attempts = cur.execute("SELECT COUNT(*) AS c FROM attempts").fetchone()["c"]
            n_verified = cur.execute(
                "SELECT COUNT(*) AS c FROM attempts WHERE verification_status = 'verified'"
            ).fetchone()["c"]
            per_type = cur.execute(
                "SELECT problem_type, COUNT(*) AS c FROM problems GROUP BY problem_type"
            ).fetchall()
            per_tool = cur.execute(
                "SELECT tool, COUNT(*) AS c FROM attempts GROUP BY tool"
            ).fetchall()
        return {
            "problems": int(n_problems),
            "attempts": int(n_attempts),
            "verified_attempts": int(n_verified),
            "by_problem_type": {r["problem_type"]: int(r["c"]) for r in per_type},
            "by_tool": {r["tool"]: int(r["c"]) for r in per_tool},
            "db_path": str(self.db_path),
        }
