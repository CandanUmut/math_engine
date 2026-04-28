"""Numeric tool — scipy / mpmath / numpy fallback for problems where
symbolic methods fail or are too slow.

Approaches
----------
- ``numeric.fsolve``   — scipy.optimize.fsolve over a small grid of starts
- ``numeric.brentq``   — bracketed 1-D root by sign change (univariate only)
- ``numeric.quad``     — scipy.integrate.quad for definite integrals
- ``numeric.evalf``    — mpmath / SymPy ``.evalf`` for closed-form numbers

Numeric solutions are stored as ``sympy.Float`` so the rest of the engine
(verifier, store) can treat them like any other answer. The solve path
returns a *list* of distinct roots found across the start grid, mirroring
the SymPy solve approaches.
"""
from __future__ import annotations

import math
import time
from typing import Any, Sequence

import numpy as np
import sympy as sp
from scipy import integrate as sp_integrate
from scipy import optimize as sp_optimize

from .. import problem_types as PT
from ..parser import ParsedProblem
from .base import Tool, ToolResult


TOOL_NAME = "numeric"


# --- Helpers ----------------------------------------------------------------

def _residual(expr: sp.Basic) -> sp.Basic:
    return expr.lhs - expr.rhs if isinstance(expr, sp.Equality) else expr


def _univariate(expr: sp.Basic, target: sp.Symbol | None) -> sp.Symbol:
    if target is not None:
        return target
    free = sorted(expr.free_symbols, key=lambda s: s.name)
    if len(free) != 1:
        raise ValueError("numeric: requires a single variable")
    return free[0]


def _lambdify(expr: sp.Basic, var: sp.Symbol):
    return sp.lambdify(var, expr, modules=["numpy", "scipy"])


def _dedupe(values: Sequence[float], *, tol: float = 1e-6) -> list[float]:
    """Cluster numerically-equal floats so a single root found from many
    starts isn't reported as multiple distinct ones."""
    out: list[float] = []
    for v in values:
        if not math.isfinite(v):
            continue
        if any(abs(v - u) < tol * max(1.0, abs(u)) for u in out):
            continue
        out.append(float(v))
    out.sort()
    return out


def _result_to_pretty(result: Any) -> tuple[str, str]:
    if isinstance(result, list):
        items = [sp.Float(v) for v in result]
        srep = "[" + ", ".join(sp.srepr(i) for i in items) + "]"
        pretty = "[" + ", ".join(sp.sstr(i) for i in items) + "]"
        return srep, pretty
    if isinstance(result, sp.Basic):
        return sp.srepr(result), sp.sstr(result)
    return repr(result), str(result)


# --- SOLVE ------------------------------------------------------------------

_DEFAULT_STARTS = (-10.0, -5.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 5.0, 10.0)


def _solve_fsolve(problem: ParsedProblem) -> tuple[list[float], list[str]]:
    expr = _residual(problem.expression)
    var = _univariate(expr, problem.target_symbol)
    f = _lambdify(expr, var)
    roots: list[float] = []
    np.seterr(all="ignore")
    for x0 in _DEFAULT_STARTS:
        try:
            sol, info, ier, _ = sp_optimize.fsolve(
                f, x0, full_output=True, xtol=1e-10,
            )
            if ier == 1 and abs(float(info["fvec"][0])) < 1e-7:
                roots.append(float(sol[0]))
        except (TypeError, ValueError, ArithmeticError):
            continue
    deduped = _dedupe(roots)
    if not deduped:
        raise ValueError("fsolve found no roots from the default start grid")
    return [sp.Float(r) for r in deduped], [
        f"Lambdify {sp.sstr(expr)} as a Python callable",
        f"scipy.optimize.fsolve from {len(_DEFAULT_STARTS)} starts; deduplicated",
    ]


def _solve_brentq(problem: ParsedProblem) -> tuple[list[float], list[str]]:
    expr = _residual(problem.expression)
    var = _univariate(expr, problem.target_symbol)
    f = _lambdify(expr, var)
    # The grid is intentionally stepped at 0.0997... so it does not land
    # on integer points — that lets sign-change detection fire on roots
    # that fall on round numbers like 1, 2, 3 without us having to
    # special-case "y == 0 at a grid point".
    grid = np.linspace(-20.0, 20.0, 401, endpoint=True)
    grid = grid + 0.013    # tiny offset; still covers [-19.987, 20.013]
    roots: list[float] = []
    last_y: float | None = None
    last_x: float | None = None
    np.seterr(all="ignore")
    for x in grid:
        try:
            y = float(f(x))
        except (TypeError, ValueError, ArithmeticError):
            last_y = None
            last_x = x
            continue
        if not math.isfinite(y):
            last_y = None
            last_x = x
            continue
        if abs(y) < 1e-12:
            roots.append(float(x))
        elif last_y is not None and last_y * y < 0:
            try:
                root = sp_optimize.brentq(f, last_x, x, xtol=1e-10)
                roots.append(float(root))
            except (ValueError, RuntimeError):
                pass
        last_y = y
        last_x = x
    deduped = _dedupe(roots)
    if not deduped:
        raise ValueError("brentq found no sign changes on [-20, 20]")
    return [sp.Float(r) for r in deduped], [
        "Sample sign on a 401-point grid spanning [-20, 20]",
        "scipy.optimize.brentq on each sign-change bracket",
    ]


# --- INTEGRATE --------------------------------------------------------------

def _integrate_quad(problem: ParsedProblem) -> tuple[Any, list[str]]:
    expr = problem.expression
    if isinstance(expr, sp.Integral) and expr.limits and len(expr.limits[0]) == 3:
        integrand = expr.function
        var, lo, hi = expr.limits[0]
    else:
        raise ValueError("numeric.quad: expects a definite Integral(f, (x, a, b))")
    f = _lambdify(integrand, var)
    lo_f = float(sp.N(lo))
    hi_f = float(sp.N(hi))
    val, abserr = sp_integrate.quad(f, lo_f, hi_f, epsabs=1e-10, epsrel=1e-10)
    return sp.Float(val), [
        f"Lambdify integrand {sp.sstr(integrand)}",
        f"scipy.integrate.quad on [{lo_f}, {hi_f}] (abs err ~ {abserr:.2e})",
    ]


# --- EVALUATE ---------------------------------------------------------------

def _evaluate_evalf(problem: ParsedProblem) -> tuple[Any, list[str]]:
    expr = problem.expression
    target = expr.doit() if hasattr(expr, "doit") else expr
    if getattr(target, "free_symbols", set()):
        raise ValueError("numeric.evalf: expression still has free symbols")
    return sp.N(target, 30), [f"sympy.N({sp.sstr(target)}, 30)"]


# --- Registry --------------------------------------------------------------

_APPROACHES = {
    PT.SOLVE: [
        ("numeric.fsolve", _solve_fsolve),
        ("numeric.brentq", _solve_brentq),
    ],
    PT.INTEGRATE: [
        ("numeric.quad", _integrate_quad),
    ],
    PT.EVALUATE: [
        ("numeric.evalf", _evaluate_evalf),
    ],
}


def candidate_approaches(problem_type: str) -> list[str]:
    return [name for name, _ in _APPROACHES.get(problem_type, [])]


class NumericTool(Tool):
    """scipy / mpmath / numpy backed Tool. Always available because
    everything it depends on is a hard requirement of the package."""

    name = TOOL_NAME
    # Numeric is a strong cross-verifier — sampling a closed-form answer
    # at many points catches most algebra mistakes — but it's empirical,
    # so Z3's actual proof beats it.
    cross_verify_priority = 50

    def is_available(self) -> bool:
        return True

    def candidate_approaches(self, problem_type: str):
        return candidate_approaches(problem_type)

    def can_handle(self, fingerprint):
        ptype = fingerprint.get("problem_type")
        if ptype not in _APPROACHES:
            return 0.0
        # Numeric is most useful when symbolic methods are likely to
        # struggle: large polynomials, non-polynomial transcendentals,
        # or multi-variable expressions reduce confidence to a tie-breaker
        # but never to zero (so the learner can still try us).
        if ptype == PT.SOLVE:
            deg = fingerprint.get("polynomial_degree")
            if deg and deg <= 4:
                return 0.4    # SymPy will likely beat us on low-degree
            return 0.7        # transcendental / high-degree: numeric shines
        if ptype == PT.INTEGRATE:
            return 0.6
        return 0.5

    def _solve_with(self, problem: ParsedProblem, approach: str) -> ToolResult:
        approaches = dict(
            (name, fn)
            for lst in _APPROACHES.values()
            for name, fn in lst
        )
        fn = approaches.get(approach)
        if fn is None:
            return ToolResult(
                tool=self.name, approach=approach, success=False,
                error=f"unknown numeric approach: {approach!r}",
            )
        t0 = time.perf_counter()
        try:
            result, steps = fn(problem)
        except Exception as exc:
            dt = (time.perf_counter() - t0) * 1000.0
            return ToolResult(
                tool=self.name, approach=approach, success=False,
                time_ms=dt, error=f"{type(exc).__name__}: {exc}",
            )
        dt = (time.perf_counter() - t0) * 1000.0
        srep, pretty = _result_to_pretty(result)
        return ToolResult(
            tool=self.name, approach=approach, success=True,
            result=result, result_repr=srep, result_pretty=pretty,
            time_ms=dt, steps=steps,
            meta={"problem_type": problem.problem_type},
        )
