"""SQLite storage layer.

Tables:

- ``problems``       — one row per solve request
- ``attempts``       — one row per tool invocation on a problem
- ``tool_outcomes``  — aggregated (signature, tool, approach) stats including
                       a small bag of recent failure modes (Phase 3)

The store is intentionally plain ``sqlite3`` — no ORM — so the schema stays
trivially inspectable with any SQLite browser. Phase 5 will add hypotheses.
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
    signature           TEXT    NOT NULL,
    tool                TEXT    NOT NULL,
    approach            TEXT    NOT NULL,
    n_attempts          INTEGER NOT NULL DEFAULT 0,
    n_success           INTEGER NOT NULL DEFAULT 0,
    n_verified          INTEGER NOT NULL DEFAULT 0,
    total_time_ms       REAL    NOT NULL DEFAULT 0,
    failure_modes_json  TEXT    NOT NULL DEFAULT '[]',
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (signature, tool, approach)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_tool_approach ON tool_outcomes(tool, approach);

-- Phase 5: hypotheses are proposed by the hypothesizer, verified (or
-- refuted) by the verification pipeline, and the surviving ones are
-- materialised as rule nodes in the graph.
CREATE TABLE IF NOT EXISTS hypotheses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT    NOT NULL,        -- "identity" | "specialization" | "recurring_approach"
    claim               TEXT    NOT NULL,        -- short human-readable
    claim_repr          TEXT,                    -- machine-checkable form (e.g. "lhs == rhs" srepr)
    fingerprint         TEXT    NOT NULL UNIQUE, -- dedupe key (sha1 of kind+claim_repr)
    evidence_json       TEXT    NOT NULL DEFAULT '{}',
    status              TEXT    NOT NULL,        -- "proposed" | "verified" | "refuted" | "inconclusive"
    method              TEXT,                    -- "sympy" | "numeric" | "z3" | "stat" | NULL
    verification_detail TEXT,
    rule_node           TEXT,                    -- graph node id for verified hypotheses
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON hypotheses(status);
CREATE INDEX IF NOT EXISTS idx_hypotheses_kind ON hypotheses(kind);
"""

# Columns added after the initial schema. Each entry is a (table, column,
# DDL fragment) tuple; ``_init_schema`` issues ALTER TABLE for any column
# that is missing on existing databases.
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("tool_outcomes", "failure_modes_json",      "TEXT NOT NULL DEFAULT '[]'"),
    ("attempts",      "cross_verify_tool",       "TEXT"),
    ("attempts",      "cross_verify_status",     "TEXT"),
    ("attempts",      "cross_verify_detail",     "TEXT"),
    ("attempts",      "cross_verify_time_ms",    "REAL"),
]


_MAX_FAILURE_MODES = 8     # cap the per-(sig, approach) failure mode list


def _merge_evidence(prior: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Merge two evidence dicts: lists deduplicate (preserving order),
    integers add, scalars from ``new`` win."""
    out = dict(prior)
    for k, v in new.items():
        if isinstance(v, list) and isinstance(out.get(k), list):
            seen = set()
            merged: list[Any] = []
            for item in list(out[k]) + v:
                key = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else item
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
            out[k] = merged
        elif isinstance(v, int) and isinstance(out.get(k), int):
            out[k] = out[k] + v
        else:
            out[k] = v
    return out


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
class ToolOutcomeRecord:
    signature: str
    tool: str
    approach: str
    n_attempts: int
    n_success: int
    n_verified: int
    total_time_ms: float
    failure_modes: list[str]
    updated_at: str

    @property
    def avg_time_ms(self) -> float:
        return self.total_time_ms / self.n_attempts if self.n_attempts else 0.0

    @property
    def verify_rate(self) -> float:
        return self.n_verified / self.n_attempts if self.n_attempts else 0.0

    @property
    def success_rate(self) -> float:
        return self.n_success / self.n_attempts if self.n_attempts else 0.0


@dataclass
class HypothesisRecord:
    id: int
    kind: str
    claim: str
    claim_repr: str | None
    fingerprint: str
    evidence: dict[str, Any]
    status: str
    method: str | None
    verification_detail: str | None
    rule_node: str | None
    created_at: str
    updated_at: str


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
    cross_verify_tool: str | None = None
    cross_verify_status: str | None = None
    cross_verify_detail: str | None = None
    cross_verify_time_ms: float | None = None


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
            for table, col, ddl in _MIGRATIONS:
                cur = self._conn.execute(f"PRAGMA table_info({table})")
                existing = {row[1] for row in cur.fetchall()}
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

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

    def update_cross_verify(
        self,
        *,
        attempt_id: int,
        tool: str,
        status: str,
        detail: str | None,
        time_ms: float,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE attempts SET
                    cross_verify_tool    = ?,
                    cross_verify_status  = ?,
                    cross_verify_detail  = ?,
                    cross_verify_time_ms = ?
                WHERE id = ?
                """,
                (tool, status, detail, float(time_ms), int(attempt_id)),
            )

    def upsert_tool_outcome(
        self,
        *,
        signature: str,
        tool: str,
        approach: str,
        success: bool,
        verified: bool,
        time_ms: float,
        error: str | None = None,
    ) -> None:
        """Insert or update the (signature, tool, approach) aggregate.

        When ``error`` is provided (i.e. the attempt failed or was refuted),
        a short tag — ``ExceptionClass`` for tool errors, or
        ``verify:refuted``/``verify:inconclusive`` — is appended to the
        ``failure_modes_json`` list, capped at ``_MAX_FAILURE_MODES`` most-
        recent entries. The list is never re-ordered; the oldest entry is
        evicted when full.
        """
        # We need a read-modify-write for failure_modes_json, and we want the
        # whole upsert to be atomic, so do it in one cursor.
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT failure_modes_json FROM tool_outcomes "
                "WHERE signature = ? AND tool = ? AND approach = ?",
                (signature, tool, approach),
            ).fetchone()
            existing: list[str] = []
            if row and row["failure_modes_json"]:
                try:
                    existing = list(json.loads(row["failure_modes_json"]))
                except (TypeError, ValueError):
                    existing = []
            if error:
                existing.append(error)
                if len(existing) > _MAX_FAILURE_MODES:
                    existing = existing[-_MAX_FAILURE_MODES:]
            cur.execute(
                """
                INSERT INTO tool_outcomes
                    (signature, tool, approach, n_attempts, n_success, n_verified,
                     total_time_ms, failure_modes_json, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(signature, tool, approach) DO UPDATE SET
                    n_attempts         = n_attempts    + 1,
                    n_success          = n_success     + excluded.n_success,
                    n_verified         = n_verified    + excluded.n_verified,
                    total_time_ms      = total_time_ms + excluded.total_time_ms,
                    failure_modes_json = excluded.failure_modes_json,
                    updated_at         = datetime('now')
                """,
                (
                    signature, tool, approach,
                    1 if success else 0,
                    1 if verified else 0,
                    float(time_ms),
                    json.dumps(existing),
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
        keys = row.keys() if hasattr(row, "keys") else []
        def opt(name: str) -> Any:
            return row[name] if name in keys else None
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
            cross_verify_tool=opt("cross_verify_tool"),
            cross_verify_status=opt("cross_verify_status"),
            cross_verify_detail=opt("cross_verify_detail"),
            cross_verify_time_ms=opt("cross_verify_time_ms"),
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

    def _row_to_outcome(self, row: sqlite3.Row) -> ToolOutcomeRecord:
        try:
            modes = list(json.loads(row["failure_modes_json"] or "[]"))
        except (TypeError, ValueError):
            modes = []
        return ToolOutcomeRecord(
            signature=row["signature"],
            tool=row["tool"],
            approach=row["approach"],
            n_attempts=int(row["n_attempts"]),
            n_success=int(row["n_success"]),
            n_verified=int(row["n_verified"]),
            total_time_ms=float(row["total_time_ms"]),
            failure_modes=modes,
            updated_at=row["updated_at"],
        )

    def get_tool_outcomes_by_signature(self, signature: str) -> list[ToolOutcomeRecord]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM tool_outcomes WHERE signature = ?", (signature,),
            ).fetchall()
        return [self._row_to_outcome(r) for r in rows]

    def get_tool_outcomes_by_problem_type(self, problem_type: str
                                          ) -> list[ToolOutcomeRecord]:
        """Aggregate outcomes across all signatures of a given problem type
        by joining on ``problems.signature``. The returned records have
        ``signature`` set to the empty string and represent type-level
        totals — used as a fallback when the current signature is unseen."""
        with self._cursor() as cur:
            rows = cur.execute(
                """
                SELECT
                    '' AS signature,
                    o.tool, o.approach,
                    SUM(o.n_attempts)     AS n_attempts,
                    SUM(o.n_success)      AS n_success,
                    SUM(o.n_verified)     AS n_verified,
                    SUM(o.total_time_ms)  AS total_time_ms,
                    '[]'                  AS failure_modes_json,
                    MAX(o.updated_at)     AS updated_at
                FROM tool_outcomes o
                JOIN problems p ON p.signature = o.signature
                WHERE p.problem_type = ?
                GROUP BY o.tool, o.approach
                """,
                (problem_type,),
            ).fetchall()
        return [self._row_to_outcome(r) for r in rows]

    def list_tool_outcomes(self, limit: int = 200) -> list[ToolOutcomeRecord]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM tool_outcomes ORDER BY n_attempts DESC, updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_outcome(r) for r in rows]

    # --- Hypotheses (Phase 5) -----------------------------------------

    def _row_to_hypothesis(self, row: sqlite3.Row) -> HypothesisRecord:
        try:
            ev = dict(json.loads(row["evidence_json"] or "{}"))
        except (TypeError, ValueError):
            ev = {}
        return HypothesisRecord(
            id=row["id"],
            kind=row["kind"],
            claim=row["claim"],
            claim_repr=row["claim_repr"],
            fingerprint=row["fingerprint"],
            evidence=ev,
            status=row["status"],
            method=row["method"],
            verification_detail=row["verification_detail"],
            rule_node=row["rule_node"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert_hypothesis(
        self,
        *,
        kind: str,
        claim: str,
        claim_repr: str | None,
        fingerprint: str,
        evidence: dict[str, Any],
        status: str = "proposed",
        method: str | None = None,
        verification_detail: str | None = None,
    ) -> tuple[int, bool]:
        """Insert a new hypothesis or merge evidence into an existing one
        (matched on the deterministic ``fingerprint``). Returns
        ``(hypothesis_id, was_new)``."""
        with self._cursor() as cur:
            existing = cur.execute(
                "SELECT id, evidence_json FROM hypotheses WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing:
                # Merge evidence dicts (lists are concatenated and de-duplicated).
                try:
                    prior = dict(json.loads(existing["evidence_json"] or "{}"))
                except (TypeError, ValueError):
                    prior = {}
                merged = _merge_evidence(prior, evidence)
                cur.execute(
                    """
                    UPDATE hypotheses SET
                        evidence_json = ?,
                        updated_at    = datetime('now')
                    WHERE id = ?
                    """,
                    (json.dumps(merged), int(existing["id"])),
                )
                return int(existing["id"]), False
            cur.execute(
                """
                INSERT INTO hypotheses
                    (kind, claim, claim_repr, fingerprint, evidence_json,
                     status, method, verification_detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind, claim, claim_repr, fingerprint,
                    json.dumps(evidence), status, method, verification_detail,
                ),
            )
            return int(cur.lastrowid), True

    def update_hypothesis_status(
        self,
        *,
        hypothesis_id: int,
        status: str,
        method: str | None,
        verification_detail: str | None,
        rule_node: str | None = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE hypotheses SET
                    status              = ?,
                    method              = ?,
                    verification_detail = ?,
                    rule_node           = COALESCE(?, rule_node),
                    updated_at          = datetime('now')
                WHERE id = ?
                """,
                (status, method, verification_detail, rule_node, int(hypothesis_id)),
            )

    def get_hypothesis(self, hypothesis_id: int) -> HypothesisRecord | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM hypotheses WHERE id = ?", (int(hypothesis_id),),
            ).fetchone()
        return self._row_to_hypothesis(row) if row else None

    def list_hypotheses(
        self, *, status: str | None = None, kind: str | None = None,
        limit: int = 200,
    ) -> list[HypothesisRecord]:
        sql = "SELECT * FROM hypotheses"
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status = ?"); args.append(status)
        if kind:
            clauses.append("kind = ?"); args.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._cursor() as cur:
            rows = cur.execute(sql, args).fetchall()
        return [self._row_to_hypothesis(r) for r in rows]

    def hypothesis_counts(self) -> dict[str, int]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT status, COUNT(*) AS c FROM hypotheses GROUP BY status",
            ).fetchall()
        return {r["status"]: int(r["c"]) for r in rows}

    def attempt_timeline(self, limit: int = 500) -> list[dict[str, Any]]:
        """Recent attempts as plain dicts, suitable for charts / dashboards."""
        with self._cursor() as cur:
            rows = cur.execute(
                """
                SELECT a.id, a.problem_id, a.tool, a.approach, a.success,
                       a.verification_status, a.time_ms, a.created_at,
                       p.problem_type
                FROM attempts a
                JOIN problems p ON p.id = a.problem_id
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

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
