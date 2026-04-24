"""Structural fingerprints for math problems.

A fingerprint is a deterministic, JSON-serializable dict summarising the
shape of a SymPy expression plus its problem type. Two expressions with
the same fingerprint are structurally similar and, in Phase 2+, cluster
together in the relational graph.

Design rules:
- Fingerprints are stable: the same input always yields the same dict.
- Fingerprints are small: keep keys bounded so SQLite indexing stays fast.
- Fingerprints are inspectable: every key has an obvious meaning.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import sympy as sp

from . import problem_types as PT


# Function-class tags we track. Presence flags make structural matching cheap
# without storing the full operator tree.
_FUNCTION_TAGS: dict[str, tuple[type, ...]] = {
    "trig": (sp.sin, sp.cos, sp.tan, sp.cot, sp.sec, sp.csc),
    "inv_trig": (sp.asin, sp.acos, sp.atan, sp.acot, sp.asec, sp.acsc),
    "hyp": (sp.sinh, sp.cosh, sp.tanh, sp.coth),
    "log": (sp.log,),
    "exp": (sp.exp,),
    "abs": (sp.Abs,),
    "piecewise": (sp.Piecewise,),
    "factorial": (sp.factorial,),
    "gamma": (sp.gamma,),
}


def _count_nodes(expr: sp.Expr) -> int:
    return sum(1 for _ in sp.preorder_traversal(expr))


def _max_depth(expr: Any) -> int:
    if not getattr(expr, "args", None):
        return 1
    return 1 + max((_max_depth(a) for a in expr.args), default=0)


def _operator_counts(expr: sp.Expr) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in sp.preorder_traversal(expr):
        if isinstance(node, sp.Add):
            counts["Add"] = counts.get("Add", 0) + 1
        elif isinstance(node, sp.Mul):
            counts["Mul"] = counts.get("Mul", 0) + 1
        elif isinstance(node, sp.Pow):
            counts["Pow"] = counts.get("Pow", 0) + 1
        elif isinstance(node, sp.Equality):
            counts["Eq"] = counts.get("Eq", 0) + 1
        elif isinstance(node, (sp.Rel,)):
            counts["Rel"] = counts.get("Rel", 0) + 1
    return counts


def _function_flags(expr: sp.Expr) -> dict[str, bool]:
    flags = {tag: False for tag in _FUNCTION_TAGS}
    for node in sp.preorder_traversal(expr):
        for tag, cls in _FUNCTION_TAGS.items():
            if isinstance(node, cls):
                flags[tag] = True
    return flags


def _polynomial_degree(expr: sp.Expr, symbols: list[sp.Symbol]) -> int | None:
    if not symbols:
        return None
    try:
        poly = sp.Poly(expr, *symbols)
    except (sp.PolynomialError, TypeError, ValueError):
        return None
    try:
        return int(poly.total_degree())
    except Exception:
        return None


def _canonical_signature(expr: sp.Expr, problem_type: str) -> str:
    """A short hash capturing the shape of the expression after a light
    canonicalization. Same shape -> same signature, regardless of variable
    names. Used as a coarse equivalence class."""
    # Rename free symbols to x0, x1, ... in a deterministic order so that
    # e.g. x^2 + x and y^2 + y share a signature.
    free = sorted(expr.free_symbols, key=lambda s: s.name)
    mapping = {s: sp.Symbol(f"x{i}") for i, s in enumerate(free)}
    renamed = expr.xreplace(mapping) if mapping else expr
    key = f"{problem_type}|{sp.srepr(renamed)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def compute_fingerprint(
    expr: sp.Expr,
    problem_type: str = PT.UNKNOWN,
    *,
    target_symbol: sp.Symbol | None = None,
) -> dict[str, Any]:
    """Compute a deterministic structural fingerprint.

    Args:
        expr: SymPy expression (for SOLVE, typically an Eq or an expression
            implicitly equal to zero).
        problem_type: one of ``problem_types`` constants.
        target_symbol: the variable of interest if meaningful (e.g. the
            integration variable). Included as a tag only.
    """
    if not isinstance(expr, sp.Basic):
        raise TypeError(f"expected a SymPy expression, got {type(expr).__name__}")

    free = sorted(expr.free_symbols, key=lambda s: s.name)
    ops = _operator_counts(expr)
    flags = _function_flags(expr)
    poly_deg = _polynomial_degree(expr, list(free))
    sig = _canonical_signature(expr, problem_type)

    fp = {
        "problem_type": problem_type,
        "variables": [s.name for s in free],
        "variable_count": len(free),
        "node_count": _count_nodes(expr),
        "max_depth": _max_depth(expr),
        "operator_counts": ops,
        "function_flags": flags,
        "polynomial_degree": poly_deg,
        "target": target_symbol.name if target_symbol is not None else None,
        "signature": sig,
    }
    return fp


def fingerprint_to_json(fp: dict[str, Any]) -> str:
    """Stable JSON serialization (sorted keys) for storage."""
    return json.dumps(fp, sort_keys=True)


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def similarity(fp_a: dict[str, Any], fp_b: dict[str, Any]) -> float:
    """A coarse similarity score in [0, 1]. Phase 2 consumes this to cluster
    problems; documented so it can be tuned transparently.

    Components, each in [0, 1], combined with fixed weights:
      - problem-type match (0 or 1)                          weight 0.30
      - Jaccard over operator names                          weight 0.25
      - Jaccard over active function-class flags             weight 0.20
      - relative variable-count closeness                    weight 0.10
      - relative node-count closeness                        weight 0.15
    """
    same_type = 1.0 if fp_a.get("problem_type") == fp_b.get("problem_type") else 0.0

    ops_a = set((fp_a.get("operator_counts") or {}).keys())
    ops_b = set((fp_b.get("operator_counts") or {}).keys())
    ops_sim = jaccard(ops_a, ops_b)

    flags_a = {k for k, v in (fp_a.get("function_flags") or {}).items() if v}
    flags_b = {k for k, v in (fp_b.get("function_flags") or {}).items() if v}
    flag_sim = jaccard(flags_a, flags_b)

    va = int(fp_a.get("variable_count") or 0)
    vb = int(fp_b.get("variable_count") or 0)
    var_sim = 1.0 - abs(va - vb) / max(va, vb, 1)

    na = int(fp_a.get("node_count") or 0)
    nb = int(fp_b.get("node_count") or 0)
    node_sim = 1.0 - abs(na - nb) / max(na, nb, 1)

    return (
        0.30 * same_type
        + 0.25 * ops_sim
        + 0.20 * flag_sim
        + 0.10 * var_sim
        + 0.15 * node_sim
    )
