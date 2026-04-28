"""SymPy tool — registry of named approaches per problem type.

Phase 1 / 2 had one approach per problem type. Phase 3 introduces an
**approach registry**: a list of named callables per problem type, ordered
by historical default preference. The :class:`Learner` reorders them per
fingerprint based on past success.

Phase 4 wraps this module in a :class:`SymPyTool` that participates in the
multi-tool registry. Module-level helpers (``solve``,
``solve_with_approach``, ``candidate_approaches``) are kept for callers
that want SymPy directly without going through the registry.

Each approach is identified by a stable, dotted string name
(``sympy.solve``, ``sympy.solveset``, ``sympy.integrate.meijerg``, …).
These strings are written to the ``attempts`` and ``tool_outcomes`` tables
and are how the learner keys its statistics.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import sympy as sp

from .. import problem_types as PT
from ..parser import ParsedProblem
from .base import Tool, ToolResult


TOOL_NAME = "sympy"


# --- Helpers ----------------------------------------------------------------

def _fmt(result: Any) -> tuple[str, str]:
    try:
        if isinstance(result, (list, tuple, set)):
            items = list(result)
            srep = "[" + ", ".join(sp.srepr(sp.sympify(i)) for i in items) + "]"
            pretty = "[" + ", ".join(sp.sstr(i) for i in items) + "]"
            return srep, pretty
        if isinstance(result, (sp.Set, sp.FiniteSet)):
            try:
                items = list(result)
                srep = "[" + ", ".join(sp.srepr(sp.sympify(i)) for i in items) + "]"
                pretty = "[" + ", ".join(sp.sstr(i) for i in items) + "]"
                return srep, pretty
            except Exception:
                pass
        return sp.srepr(result), sp.sstr(result)
    except Exception:
        return repr(result), str(result)


def _target(problem: ParsedProblem, expr: sp.Basic | None = None) -> sp.Symbol | None:
    expr = expr if expr is not None else problem.expression
    if problem.target_symbol is not None:
        return problem.target_symbol
    free = sorted(expr.free_symbols, key=lambda s: s.name)
    return free[0] if len(free) == 1 else None


# --- SOLVE approaches -------------------------------------------------------

def _to_residual(expr: sp.Basic) -> sp.Basic:
    return sp.simplify(expr.lhs - expr.rhs) if isinstance(expr, sp.Equality) else expr


def _solve_solve(problem: ParsedProblem):
    residual = _to_residual(problem.expression)
    var = _target(problem, residual)
    if var is None:
        raise ValueError("solve: no target variable")
    return (
        sp.solve(residual, var, dict=False),
        [f"Treat as {sp.sstr(residual)} = 0", f"sympy.solve(_, {var})"],
    )


def _solve_solveset(problem: ParsedProblem):
    residual = _to_residual(problem.expression)
    var = _target(problem, residual)
    if var is None:
        raise ValueError("solveset: no target variable")
    s = sp.solveset(residual, var, domain=sp.S.Complexes)
    if isinstance(s, sp.FiniteSet):
        return list(s), [f"sympy.solveset on complex domain", f"FiniteSet → list"]
    raise ValueError(f"solveset returned non-finite set: {s}")


def _solve_polynomial_roots(problem: ParsedProblem):
    """Use sympy.roots — only succeeds for polynomial residuals."""
    residual = _to_residual(problem.expression)
    var = _target(problem, residual)
    if var is None:
        raise ValueError("roots: no target variable")
    poly = sp.Poly(residual, var)  # raises if not polynomial in var
    rs = sp.roots(poly)
    if not rs:
        raise ValueError("roots: empty result (likely high-degree irreducible)")
    out: list[Any] = []
    for r, mult in rs.items():
        out.extend([r] * int(mult))
    return out, [f"Form Poly({var})", "sympy.roots → expand multiplicities"]


# --- INTEGRATE approaches ---------------------------------------------------

def _integrate_default(problem: ParsedProblem):
    expr = problem.expression
    if isinstance(expr, sp.Integral):
        return expr.doit(), [f"Evaluate {sp.sstr(expr)} via .doit()"]
    var = _target(problem)
    if var is None:
        raise ValueError("integrate: no integration variable")
    return sp.integrate(expr, var), [f"sympy.integrate({sp.sstr(expr)}, {var})"]


def _integrate_meijerg(problem: ParsedProblem):
    expr = problem.expression
    if isinstance(expr, sp.Integral):
        integrand = expr.function
        var = expr.variables[0]
        limits = expr.limits[0]
        if len(limits) == 3:
            return (
                sp.integrate(integrand, (var, limits[1], limits[2]), meijerg=True),
                [f"sympy.integrate(_, ({var}, {limits[1]}, {limits[2]}), meijerg=True)"],
            )
        return sp.integrate(integrand, var, meijerg=True), [f"sympy.integrate(_, {var}, meijerg=True)"]
    var = _target(problem)
    if var is None:
        raise ValueError("integrate.meijerg: no variable")
    return sp.integrate(expr, var, meijerg=True), [f"sympy.integrate(_, {var}, meijerg=True)"]


def _integrate_risch(problem: ParsedProblem):
    expr = problem.expression
    if isinstance(expr, sp.Integral):
        integrand = expr.function
        var = expr.variables[0]
    else:
        integrand = expr
        var = _target(problem)
    if var is None:
        raise ValueError("integrate.risch: no variable")
    # The Risch algorithm is exact for elementary integrands but can
    # raise NotImplementedError for transcendental cases. The reasoner's
    # multi-attempt loop catches that and moves on.
    return sp.integrate(integrand, var, risch=True), [f"sympy.integrate(_, {var}, risch=True)"]


# --- DIFFERENTIATE ----------------------------------------------------------

def _differentiate_default(problem: ParsedProblem):
    expr = problem.expression
    if isinstance(expr, sp.Derivative):
        return expr.doit(), [f"Evaluate {sp.sstr(expr)} via .doit()"]
    var = _target(problem)
    if var is None:
        raise ValueError("differentiate: no variable")
    return sp.diff(expr, var), [f"sympy.diff({sp.sstr(expr)}, {var})"]


# --- SIMPLIFY approaches ----------------------------------------------------

def _simplify_default(problem: ParsedProblem):
    return sp.simplify(problem.expression), [f"sympy.simplify({sp.sstr(problem.expression)})"]


def _simplify_trigsimp(problem: ParsedProblem):
    return sp.trigsimp(problem.expression), [f"sympy.trigsimp({sp.sstr(problem.expression)})"]


def _simplify_cancel(problem: ParsedProblem):
    return sp.cancel(problem.expression), [f"sympy.cancel({sp.sstr(problem.expression)})"]


def _simplify_radsimp(problem: ParsedProblem):
    return sp.radsimp(problem.expression), [f"sympy.radsimp({sp.sstr(problem.expression)})"]


# --- FACTOR / EXPAND --------------------------------------------------------

def _factor_default(problem: ParsedProblem):
    return sp.factor(problem.expression), [f"sympy.factor({sp.sstr(problem.expression)})"]


def _factor_extension(problem: ParsedProblem):
    """Try factor over Q(i, sqrt(2)) — sometimes finds extra factors."""
    return (
        sp.factor(problem.expression, extension=[sp.I, sp.sqrt(2)]),
        [f"sympy.factor(_, extension=[I, sqrt(2)])"],
    )


def _expand_default(problem: ParsedProblem):
    return sp.expand(problem.expression), [f"sympy.expand({sp.sstr(problem.expression)})"]


def _expand_trig(problem: ParsedProblem):
    return sp.expand_trig(problem.expression), [f"sympy.expand_trig({sp.sstr(problem.expression)})"]


def _expand_log(problem: ParsedProblem):
    return sp.expand_log(problem.expression, force=True), [f"sympy.expand_log(_, force=True)"]


# --- EVALUATE / LIMIT / SERIES ----------------------------------------------

def _evaluate_default(problem: ParsedProblem):
    expr = problem.expression
    result = expr.doit() if hasattr(expr, "doit") else expr
    if not getattr(result, "free_symbols", set()):
        return result, [f"Evaluate {sp.sstr(expr)} (closed form)"]
    return sp.simplify(result), [f"Evaluate {sp.sstr(expr)} then simplify"]


def _evaluate_numeric(problem: ParsedProblem):
    expr = problem.expression
    result = expr.doit() if hasattr(expr, "doit") else expr
    return sp.N(result), [f"Numerically evaluate {sp.sstr(expr)}"]


def _limit_default(problem: ParsedProblem):
    expr = problem.expression
    if isinstance(expr, sp.Limit):
        return expr.doit(), [f"Evaluate {sp.sstr(expr)}"]
    raise ValueError("limit: expected a Limit(...) expression")


def _series_default(problem: ParsedProblem):
    expr = problem.expression
    var = _target(problem)
    if var is None:
        raise ValueError("series: no variable")
    return sp.series(expr, var).removeO(), [f"Taylor series of {sp.sstr(expr)} at 0"]


# --- Registry ---------------------------------------------------------------

@dataclass(frozen=True)
class Approach:
    """A named SymPy strategy for a problem type."""
    name: str
    func: Callable[[ParsedProblem], tuple[Any, list[str]]]


APPROACHES: dict[str, list[Approach]] = {
    PT.SOLVE: [
        Approach("sympy.solve", _solve_solve),
        Approach("sympy.solveset", _solve_solveset),
        Approach("sympy.roots", _solve_polynomial_roots),
    ],
    PT.INTEGRATE: [
        Approach("sympy.integrate", _integrate_default),
        Approach("sympy.integrate.meijerg", _integrate_meijerg),
        Approach("sympy.integrate.risch", _integrate_risch),
    ],
    PT.DIFFERENTIATE: [
        Approach("sympy.diff", _differentiate_default),
    ],
    PT.SIMPLIFY: [
        Approach("sympy.simplify", _simplify_default),
        Approach("sympy.trigsimp", _simplify_trigsimp),
        Approach("sympy.cancel", _simplify_cancel),
        Approach("sympy.radsimp", _simplify_radsimp),
    ],
    PT.FACTOR: [
        Approach("sympy.factor", _factor_default),
        Approach("sympy.factor.extension", _factor_extension),
    ],
    PT.EXPAND: [
        Approach("sympy.expand", _expand_default),
        Approach("sympy.expand_trig", _expand_trig),
        Approach("sympy.expand_log", _expand_log),
    ],
    PT.EVALUATE: [
        Approach("sympy.evaluate", _evaluate_default),
        Approach("sympy.evaluate.numeric", _evaluate_numeric),
    ],
    PT.LIMIT: [
        Approach("sympy.limit", _limit_default),
    ],
    PT.SERIES: [
        Approach("sympy.series", _series_default),
    ],
}


def candidate_approaches(problem_type: str) -> list[str]:
    """Names of every approach available for a given problem type, in default
    order. Falls back to the simplify list when the type is unknown."""
    approaches = APPROACHES.get(problem_type, APPROACHES[PT.SIMPLIFY])
    return [a.name for a in approaches]


def _find_approach(problem_type: str, approach_name: str) -> Approach:
    candidates = APPROACHES.get(problem_type, APPROACHES[PT.SIMPLIFY])
    for a in candidates:
        if a.name == approach_name:
            return a
    # Allow fall-through: if the requested approach lives under a different
    # type, pick it up there. This makes the registry future-proof.
    for lst in APPROACHES.values():
        for a in lst:
            if a.name == approach_name:
                return a
    raise KeyError(f"no SymPy approach named {approach_name!r}")


def solve_with_approach(problem: ParsedProblem, approach_name: str) -> ToolResult:
    """Run a specific named approach. Used by the Phase 3 multi-attempt loop."""
    ptype_effective = problem.problem_type if problem.problem_type in APPROACHES else PT.SIMPLIFY
    approach = _find_approach(ptype_effective, approach_name)

    t0 = time.perf_counter()
    try:
        result, steps = approach.func(problem)
    except Exception as exc:
        dt = (time.perf_counter() - t0) * 1000.0
        return ToolResult(
            tool=TOOL_NAME,
            approach=approach.name,
            success=False,
            time_ms=dt,
            error=f"{type(exc).__name__}: {exc}",
            meta={"problem_type": ptype_effective},
        )
    dt = (time.perf_counter() - t0) * 1000.0
    srep, pretty = _fmt(result)
    return ToolResult(
        tool=TOOL_NAME,
        approach=approach.name,
        success=True,
        result=result,
        result_repr=srep,
        result_pretty=pretty,
        time_ms=dt,
        steps=steps,
        meta={"problem_type": ptype_effective},
    )


def solve(problem: ParsedProblem) -> ToolResult:
    """Default single-approach entry. Kept for backward compatibility with
    Phase 1/2 callers and tests; identical to running the *first* approach
    for the problem type."""
    ptype_effective = problem.problem_type if problem.problem_type in APPROACHES else PT.SIMPLIFY
    approaches = APPROACHES[ptype_effective]
    return solve_with_approach(problem, approaches[0].name)


# --- Tool registry adapter (Phase 4) ---------------------------------------


class SymPyTool(Tool):
    """Adapter that exposes :mod:`sympy_tool` through the multi-tool registry.

    SymPy is *the* generalist: it knows every problem type, so
    :meth:`candidate_approaches` is just the module-level table and
    :meth:`can_handle` always returns 1.0. The reasoner relies on the
    learner to rank against tools that are stronger on specific shapes
    (Z3 for SMT-style equations, numeric for transcendental roots, ...).
    """

    name = TOOL_NAME
    # As a cross-verifier, SymPy is decent (it can re-derive symbolically)
    # but Z3 gives a proof and numeric gives empirical agreement faster,
    # so SymPy ranks lowest of the three for cross-verification.
    cross_verify_priority = 10

    def is_available(self) -> bool:
        return True

    def candidate_approaches(self, problem_type: str):
        return list(candidate_approaches(problem_type))

    def can_handle(self, fingerprint):
        return 1.0

    def _solve_with(self, problem: ParsedProblem, approach: str) -> ToolResult:
        return solve_with_approach(problem, approach)
