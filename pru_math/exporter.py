"""Phase 6 export / import.

The accumulated database (problems, attempts, tool_outcomes, hypotheses,
plus the relational graph) is the value of this engine — see
``PHILOSOPHY.md``. ``export_bundle`` produces a single JSON object the
user can save / share / version-control; ``import_bundle`` accepts the
same shape and replaces the live state atomically.

The graph is serialised as a base64-encoded ``pickle`` payload (the
same on-disk format as the gpickle), nested inside the JSON.
"""
from __future__ import annotations

import base64
import json
import pickle
import sqlite3
from typing import Any

from .graph import RelationalGraph
from .store import Store


SCHEMA_VERSION = 1

# The full, canonical column list for each exported table. Hand-coded
# (rather than reflected from PRAGMA table_info) so a malicious or
# malformed import can't smuggle extra columns; we only insert these.
_TABLE_COLUMNS: dict[str, list[str]] = {
    "problems": [
        "id", "raw_input", "source_format", "problem_type", "parsed_expr",
        "parsed_pretty", "fingerprint_json", "signature", "created_at",
    ],
    "attempts": [
        "id", "problem_id", "tool", "approach", "success",
        "result_repr", "result_pretty",
        "verification_status", "verification_detail",
        "time_ms", "error", "steps_json", "created_at",
        "cross_verify_tool", "cross_verify_status",
        "cross_verify_detail", "cross_verify_time_ms",
    ],
    "tool_outcomes": [
        "signature", "tool", "approach", "n_attempts", "n_success",
        "n_verified", "total_time_ms", "failure_modes_json", "updated_at",
    ],
    "hypotheses": [
        "id", "kind", "claim", "claim_repr", "fingerprint",
        "evidence_json", "status", "method", "verification_detail",
        "rule_node", "created_at", "updated_at",
    ],
}


def export_bundle(store: Store, graph: RelationalGraph) -> dict[str, Any]:
    """Return a JSON-serialisable dict containing every persisted row.

    The graph is included as a base64-encoded pickle so the bundle is a
    single self-contained object — no companion files needed.
    """
    bundle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tables": {},
        "graph_pickle_b64": _encode_graph(graph),
    }
    with store._cursor() as cur:    # noqa: SLF001 — intentional simplicity
        for table, cols in _TABLE_COLUMNS.items():
            try:
                rows = cur.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            except sqlite3.OperationalError:
                # Older DB without one of the columns — skip the table
                # rather than fail the whole export.
                bundle["tables"][table] = []
                continue
            bundle["tables"][table] = [dict(r) for r in rows]
    return bundle


def import_bundle(store: Store, graph: RelationalGraph,
                  bundle: dict[str, Any]) -> dict[str, int]:
    """Replace ``store`` and ``graph`` contents with the rows in ``bundle``.

    Atomic: the SQLite work runs in a single transaction so a malformed
    bundle leaves the store untouched. The graph is replaced *after*
    the SQL transaction commits so the two sides stay in sync.
    Returns per-table row counts of what was loaded.
    """
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a dict")
    if int(bundle.get("schema_version", 0)) > SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version: {bundle.get('schema_version')!r}"
        )
    tables = bundle.get("tables") or {}
    counts: dict[str, int] = {}

    # Phase 1 of the swap: SQLite. Single transaction.
    with store._lock:                                    # noqa: SLF001
        conn = store._conn                               # noqa: SLF001
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Wipe in dependency order. attempts -> problems is FK-cascading
            # but tool_outcomes / hypotheses have no FK, so we just truncate.
            for t in ("attempts", "problems", "tool_outcomes", "hypotheses"):
                conn.execute(f"DELETE FROM {t}")
            # Re-insert.
            for table, cols in _TABLE_COLUMNS.items():
                rows = tables.get(table) or []
                if not rows:
                    counts[table] = 0
                    continue
                placeholders = ", ".join("?" for _ in cols)
                col_list = ", ".join(cols)
                sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
                payload = []
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError(f"{table}: rows must be objects")
                    payload.append(tuple(row.get(c) for c in cols))
                conn.executemany(sql, payload)
                counts[table] = len(payload)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # Phase 2 of the swap: graph (best-effort; if it fails the SQL is
    # already committed, so we leave a stale graph rather than corrupt
    # state).
    encoded = bundle.get("graph_pickle_b64")
    if encoded:
        _decode_graph_into(graph, encoded)
    counts["graph_nodes"] = graph.node_count()
    counts["graph_edges"] = graph.edge_count()
    return counts


def _encode_graph(graph: RelationalGraph) -> str:
    raw = pickle.dumps(graph.graph, protocol=pickle.HIGHEST_PROTOCOL)
    return base64.b64encode(raw).decode("ascii")


def _decode_graph_into(graph: RelationalGraph, encoded: str) -> None:
    raw = base64.b64decode(encoded.encode("ascii"))
    obj = pickle.loads(raw)
    # Replace the in-memory graph and persist immediately so a crash
    # right after import doesn't lose the imported state.
    graph._g = obj                                        # noqa: SLF001
    graph.save()
