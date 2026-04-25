"""Similarity-based retrieval over the relational graph.

The graph layer holds fingerprints; the SQLite store holds solutions and
verification status. ``find_similar_problems`` joins the two so callers
get one record per neighbour with everything they need for either the UI
or a future learner.

Two ranking paths are exposed:

- :func:`find_similar_problems` — the simple path. Calls
  ``RelationalGraph.find_similar_to_fingerprint``, which scores every
  problem in Python. Comfortable up to a few thousand problems.
- :func:`find_similar_problems_sparse` — same contract, but builds a
  scipy sparse-matrix view of the fingerprint feature space and computes
  cosine similarity in one BLAS call. Worth it past ~3k problems.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy import sparse as sp_sparse

from .config import CONFIG
from .graph import RelationalGraph
from .store import AttemptRecord, ProblemRecord, Store


@dataclass
class SimilarProblem:
    problem: ProblemRecord
    score: float
    best_attempt: AttemptRecord | None = None
    all_attempts: list[AttemptRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "problem": {
                "id": self.problem.id,
                "raw_input": self.problem.raw_input,
                "source_format": self.problem.source_format,
                "problem_type": self.problem.problem_type,
                "parsed_pretty": self.problem.parsed_pretty,
                "signature": self.problem.signature,
                "fingerprint": self.problem.fingerprint,
                "created_at": self.problem.created_at,
            },
            "best_attempt": _attempt_dict(self.best_attempt) if self.best_attempt else None,
            "attempts": [_attempt_dict(a) for a in self.all_attempts],
        }


def _attempt_dict(a: AttemptRecord) -> dict[str, Any]:
    return {
        "id": a.id,
        "tool": a.tool,
        "approach": a.approach,
        "success": a.success,
        "result_pretty": a.result_pretty,
        "verification_status": a.verification_status,
        "verification_detail": a.verification_detail,
        "time_ms": a.time_ms,
        "error": a.error,
        "steps": a.steps,
        "created_at": a.created_at,
    }


def _pick_best_attempt(attempts: Sequence[AttemptRecord]) -> AttemptRecord | None:
    """Prefer verified, then successful, then most recent."""
    if not attempts:
        return None
    def key(a: AttemptRecord) -> tuple[int, int, int]:
        return (
            1 if a.verification_status == "verified" else 0,
            1 if a.success else 0,
            a.id,
        )
    return max(attempts, key=key)


def find_similar_problems(
    fingerprint: dict[str, Any],
    *,
    graph: RelationalGraph,
    store: Store,
    k: int | None = None,
    exclude_problem_id: int | None = None,
) -> list[SimilarProblem]:
    """Return the K most structurally similar past problems with their
    attempts. Empty list if the graph has no problem nodes yet."""
    k = CONFIG.similar_top_k if k is None else int(k)
    candidates = graph.find_similar_to_fingerprint(fingerprint, top_k=k * 3 if exclude_problem_id else k)
    out: list[SimilarProblem] = []
    for n in candidates:
        if exclude_problem_id is not None and n.problem_id == exclude_problem_id:
            continue
        prob = store.get_problem(n.problem_id)
        if prob is None:
            continue
        attempts = store.list_attempts(n.problem_id)
        out.append(SimilarProblem(
            problem=prob,
            score=n.score,
            best_attempt=_pick_best_attempt(attempts),
            all_attempts=attempts,
        ))
        if len(out) >= k:
            break
    return out


# --- Sparse / batch path ----------------------------------------------------

# The feature space mixes:
#   - one-hot problem_type
#   - one-hot operator-class names (Add/Mul/Pow/Eq/Rel)
#   - one-hot function-class flags (trig/log/exp/...)
#   - normalized variable_count
#   - normalized node_count
#   - normalized polynomial_degree (capped)
# Cosine similarity over this vector is a fast batch proxy for the
# documented similarity score.
_PROBLEM_TYPES = (
    "solve", "simplify", "integrate", "differentiate", "factor",
    "evaluate", "expand", "limit", "series", "prove", "unknown",
)
_OPS = ("Add", "Mul", "Pow", "Eq", "Rel")
_FLAGS = ("trig", "inv_trig", "hyp", "log", "exp", "abs",
          "piecewise", "factorial", "gamma")


def fingerprint_to_vector(fp: dict[str, Any]) -> np.ndarray:
    """Project a fingerprint to a fixed-length numeric vector.

    Stable across runs; if you change this, bump a version constant and
    reseed the sparse cache."""
    pt = (fp.get("problem_type") or "unknown").lower()
    pt_vec = [1.0 if pt == p else 0.0 for p in _PROBLEM_TYPES]

    ops = fp.get("operator_counts") or {}
    op_vec = [1.0 if k in ops else 0.0 for k in _OPS]

    flags = fp.get("function_flags") or {}
    flag_vec = [1.0 if flags.get(k) else 0.0 for k in _FLAGS]

    vc = float(fp.get("variable_count") or 0)
    nc = float(fp.get("node_count") or 0)
    deg = float(fp.get("polynomial_degree") or 0)

    scalars = [
        min(vc / 6.0, 1.0),
        min(nc / 50.0, 1.0),
        min(deg / 10.0, 1.0),
    ]
    return np.asarray(pt_vec + op_vec + flag_vec + scalars, dtype=np.float32)


def _row_normalise(rows: sp_sparse.csr_matrix) -> sp_sparse.csr_matrix:
    norms = np.sqrt(rows.multiply(rows).sum(axis=1)).A.ravel()
    norms[norms == 0] = 1.0
    inv = sp_sparse.diags(1.0 / norms)
    return inv @ rows


def find_similar_problems_sparse(
    fingerprint: dict[str, Any],
    *,
    graph: RelationalGraph,
    store: Store,
    k: int | None = None,
    exclude_problem_id: int | None = None,
) -> list[SimilarProblem]:
    """Sparse-matrix variant of :func:`find_similar_problems`. Cheaper for
    large graphs but contractually identical for the caller.

    Falls back to the simple path if the graph is small or empty.
    """
    k = CONFIG.similar_top_k if k is None else int(k)

    nodes = list(graph.iter_problem_nodes())
    if len(nodes) < 200:
        return find_similar_problems(
            fingerprint, graph=graph, store=store, k=k,
            exclude_problem_id=exclude_problem_id,
        )

    ids = np.array([int(d["problem_id"]) for _, d in nodes], dtype=np.int64)
    matrix = sp_sparse.csr_matrix(np.vstack([
        fingerprint_to_vector(d.get("fingerprint") or {}) for _, d in nodes
    ]))
    matrix = _row_normalise(matrix)

    target = sp_sparse.csr_matrix(fingerprint_to_vector(fingerprint).reshape(1, -1))
    target = _row_normalise(target)

    sims = (matrix @ target.T).toarray().ravel()  # [n]
    # Boost exact signature matches
    target_sig = fingerprint.get("signature")
    if target_sig:
        for i, (_, d) in enumerate(nodes):
            if (d.get("fingerprint") or {}).get("signature") == target_sig:
                sims[i] = max(sims[i], 0.999)

    order = np.argsort(-sims)
    out: list[SimilarProblem] = []
    for i in order:
        pid = int(ids[i])
        if exclude_problem_id is not None and pid == exclude_problem_id:
            continue
        prob = store.get_problem(pid)
        if prob is None:
            continue
        attempts = store.list_attempts(pid)
        out.append(SimilarProblem(
            problem=prob,
            score=float(sims[i]),
            best_attempt=_pick_best_attempt(attempts),
            all_attempts=attempts,
        ))
        if len(out) >= k:
            break
    return out
