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


def export_session_bundle(
    store: Store, graph: RelationalGraph, session_id: int,
) -> dict[str, Any]:
    """Phase 11: a bundle scoped to a single session.

    Includes:

    - the session row
    - every problem with this ``session_id``
    - every attempt for those problems
    - tool_outcomes restricted to the signatures of those problems
    - hypotheses whose ``support_problem_ids`` evidence references at
      least one of those problems (best-effort; the column is JSON)
    - a graph subgraph induced by the kept problem nodes (1-hop) so
      the relational view ships intact

    The same ``schema_version`` is used so a session bundle round-trips
    through ``import_bundle`` cleanly. The session itself is included
    in a top-level ``"session"`` field; the importer ignores it (it
    only reads ``tables``), but downstream tools / UIs can use it to
    display "imported from session #N: <title>" provenance.
    """
    import json
    bundle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tables": {k: [] for k in _TABLE_COLUMNS},
    }

    # Capture the session metadata up front so the bundle is self-describing.
    with store._cursor() as cur:    # noqa: SLF001
        srow = cur.execute(
            "SELECT id, title, notes_markdown, created_at, updated_at "
            "FROM sessions WHERE id = ?", (int(session_id),),
        ).fetchone()
        if srow is None:
            raise ValueError(f"no session with id={session_id}")
        bundle["session"] = dict(srow)

        # Problems in this session.
        prob_cols = _TABLE_COLUMNS["problems"]
        prob_rows = cur.execute(
            f"SELECT {', '.join(prob_cols)} FROM problems WHERE session_id = ?",
            (int(session_id),),
        ).fetchall()
        bundle["tables"]["problems"] = [dict(r) for r in prob_rows]
        problem_ids = {int(r["id"]) for r in prob_rows}
        signatures = {r["signature"] for r in prob_rows if r["signature"]}

        if problem_ids:
            placeholders = ",".join("?" * len(problem_ids))
            attempt_cols = _TABLE_COLUMNS["attempts"]
            try:
                arows = cur.execute(
                    f"SELECT {', '.join(attempt_cols)} FROM attempts "
                    f"WHERE problem_id IN ({placeholders})",
                    tuple(problem_ids),
                ).fetchall()
            except sqlite3.OperationalError:
                arows = []
            bundle["tables"]["attempts"] = [dict(r) for r in arows]

        if signatures:
            placeholders = ",".join("?" * len(signatures))
            outcome_cols = _TABLE_COLUMNS["tool_outcomes"]
            try:
                orows = cur.execute(
                    f"SELECT {', '.join(outcome_cols)} FROM tool_outcomes "
                    f"WHERE signature IN ({placeholders})",
                    tuple(signatures),
                ).fetchall()
            except sqlite3.OperationalError:
                orows = []
            bundle["tables"]["tool_outcomes"] = [dict(r) for r in orows]

        # Hypotheses with at least one supporting problem in this session.
        hyp_cols = _TABLE_COLUMNS["hypotheses"]
        try:
            all_hyps = cur.execute(
                f"SELECT {', '.join(hyp_cols)} FROM hypotheses",
            ).fetchall()
        except sqlite3.OperationalError:
            all_hyps = []

    relevant_hyps: list[dict[str, Any]] = []
    for h in all_hyps:
        d = dict(h)
        try:
            ev = json.loads(d.get("evidence_json") or "{}")
        except (TypeError, ValueError):
            continue
        sup = ev.get("support_problem_ids") or []
        if any(int(p) in problem_ids for p in sup if isinstance(p, (int, str))):
            relevant_hyps.append(d)
    bundle["tables"]["hypotheses"] = relevant_hyps

    # Build a subgraph induced by the kept problem nodes (1-hop neighbours).
    bundle["graph_pickle_b64"] = _encode_subgraph(graph, problem_ids)
    return bundle


def _encode_subgraph(graph: RelationalGraph, problem_ids: set[int]) -> str:
    """Pickle a subgraph induced by the given problem nodes plus their
    1-hop neighbours so attached tool / signature / rule nodes survive."""
    import networkx as nx
    g = graph.graph
    seeds = {f"p:{pid}" for pid in problem_ids if f"p:{pid}" in g}
    keep: set[str] = set(seeds)
    for n in seeds:
        keep.update(g.successors(n))
        keep.update(g.predecessors(n))
    sub = nx.MultiDiGraph(g.subgraph(keep)) if keep else nx.MultiDiGraph()
    return base64.b64encode(
        pickle.dumps(sub, protocol=pickle.HIGHEST_PROTOCOL)
    ).decode("ascii")


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


def reset_state(store: Store, graph: RelationalGraph) -> dict[str, int]:
    """Wipe all learning state — every row in every table, plus the
    relational graph. Atomic: SQLite work runs in a single transaction.

    Used by the "reset learning state" button in the UI's Demo flow so a
    professor can rerun the demo from a blank slate without restarting
    the server.

    The schema itself stays intact; sessions and config are untouched.
    """
    counts: dict[str, int] = {}
    with store._lock:                                        # noqa: SLF001
        conn = store._conn                                   # noqa: SLF001
        try:
            conn.execute("BEGIN IMMEDIATE")
            for t in ("attempts", "problems", "tool_outcomes", "hypotheses"):
                cur = conn.execute(f"DELETE FROM {t}")
                counts[f"{t}_deleted"] = cur.rowcount or 0
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # Drop the in-memory graph and persist immediately so subsequent
    # solves rebuild from scratch.
    import networkx as nx
    graph._g = nx.MultiDiGraph()                             # noqa: SLF001
    graph.save()
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
