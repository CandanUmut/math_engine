"""SymPy tool wrapper.

Dispatches a :class:`~pru_math.parser.ParsedProblem` to the appropriate
SymPy routine based on problem type, captures timing and errors, and
returns a :class:`ToolResult`.
"""
from __future__ import annotations

import time
from typing import Any

import sympy as sp

from .. import problem_types as PT
from ..parser import ParsedProblem
from .base import ToolResult


TOOL_NAME = "sympy"


def _fmt(result: Any) -> tuple[str, str]:
    """Return (srepr, pretty) for a result, handling list/tuple collections."""
    try:
        if isinstance(result, (list, tuple, set)):
            items = list(result)
            srep = "[" + ", ".join(sp.srepr(sp.sympify(i)) for i in items) + "]"
            pretty = "[" + ", ".join(sp.sstr(i) for i in items) + "]"
            return srep, pretty
        return sp.srepr(result), sp.sstr(result)
    except Exception:
        return repr(result), str(result)


def _solve(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    target = problem.target_symbol
    if isinstance(expr, sp.Equality):
        lhs_minus_rhs = sp.simplify(expr.lhs - expr.rhs)
        target_sym = target or (sorted(lhs_minus_rhs.free_symbols, key=lambda s: s.name)[:1] or [None])[0]
        if target_sym is None:
            raise ValueError("solve: no target variable")
        solutions = sp.solve(lhs_minus_rhs, target_sym, dict=False)
        return solutions, "sympy.solve", [
            f"Rearrange to {sp.sstr(lhs_minus_rhs)} = 0",
            f"Solve for {target_sym}",
        ]
    # expression assumed to equal zero
    target_sym = target or (sorted(expr.free_symbols, key=lambda s: s.name)[:1] or [None])[0]
    if target_sym is None:
        raise ValueError("solve: no target variable")
    solutions = sp.solve(expr, target_sym, dict=False)
    return solutions, "sympy.solve", [
        f"Interpret expression = 0",
        f"Solve for {target_sym}",
    ]


def _integrate(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    if isinstance(expr, sp.Integral):
        result = expr.doit()
        return result, "sympy.integrate.doit", [f"Evaluate {sp.sstr(expr)}"]
    target = problem.target_symbol or (sorted(expr.free_symbols, key=lambda s: s.name)[:1] or [None])[0]
    if target is None:
        raise ValueError("integrate: no integration variable")
    result = sp.integrate(expr, target)
    return result, "sympy.integrate", [f"Integrate {sp.sstr(expr)} d{target}"]


def _differentiate(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    if isinstance(expr, sp.Derivative):
        result = expr.doit()
        return result, "sympy.diff.doit", [f"Evaluate {sp.sstr(expr)}"]
    target = problem.target_symbol or (sorted(expr.free_symbols, key=lambda s: s.name)[:1] or [None])[0]
    if target is None:
        raise ValueError("differentiate: no variable")
    result = sp.diff(expr, target)
    return result, "sympy.diff", [f"Differentiate {sp.sstr(expr)} w.r.t. {target}"]


def _simplify(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    result = sp.simplify(expr)
    return result, "sympy.simplify", [f"Apply sympy.simplify to {sp.sstr(expr)}"]


def _factor(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    result = sp.factor(expr)
    return result, "sympy.factor", [f"Factor {sp.sstr(expr)}"]


def _expand(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    result = sp.expand(expr)
    return result, "sympy.expand", [f"Expand {sp.sstr(expr)}"]


def _evaluate(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    # If it is something with a .doit, prefer that (e.g. Sum, Integral).
    if hasattr(expr, "doit"):
        result = expr.doit()
    else:
        result = expr
    # Numerical evaluation if it has no free symbols.
    if not result.free_symbols:
        result = sp.nsimplify(result, rational=False) if isinstance(result, sp.Float) else result
        return result, "sympy.evaluate", [f"Evaluate {sp.sstr(expr)}"]
    return sp.simplify(result), "sympy.evaluate", [f"Evaluate {sp.sstr(expr)}"]


def _limit(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    if isinstance(expr, sp.Limit):
        return expr.doit(), "sympy.limit.doit", [f"Evaluate {sp.sstr(expr)}"]
    raise ValueError("limit: expected a Limit(...) expression; natural-language path should emit one")


def _series(problem: ParsedProblem) -> tuple[Any, str, list[str]]:
    expr = problem.expression
    target = problem.target_symbol or (sorted(expr.free_symbols, key=lambda s: s.name)[:1] or [None])[0]
    if target is None:
        raise ValueError("series: no variable")
    result = sp.series(expr, target).removeO()
    return result, "sympy.series", [f"Taylor series of {sp.sstr(expr)} at 0"]


_DISPATCH = {
    PT.SOLVE: _solve,
    PT.INTEGRATE: _integrate,
    PT.DIFFERENTIATE: _differentiate,
    PT.SIMPLIFY: _simplify,
    PT.FACTOR: _factor,
    PT.EXPAND: _expand,
    PT.EVALUATE: _evaluate,
    PT.LIMIT: _limit,
    PT.SERIES: _series,
    # PT.PROVE handled by a later phase
}


def solve(problem: ParsedProblem) -> ToolResult:
    """Run SymPy on the parsed problem and return a uniform :class:`ToolResult`."""
    ptype = problem.problem_type
    if ptype not in _DISPATCH:
        # Default to simplify — it is the safest universal fallback.
        ptype_effective = PT.SIMPLIFY
        dispatch = _simplify
    else:
        ptype_effective = ptype
        dispatch = _DISPATCH[ptype]

    t0 = time.perf_counter()
    try:
        result, approach, steps = dispatch(problem)
    except Exception as exc:
        dt = (time.perf_counter() - t0) * 1000.0
        return ToolResult(
            tool=TOOL_NAME,
            approach=f"sympy.{ptype_effective}",
            success=False,
            time_ms=dt,
            error=f"{type(exc).__name__}: {exc}",
        )
    dt = (time.perf_counter() - t0) * 1000.0
    srep, pretty = _fmt(result)
    return ToolResult(
        tool=TOOL_NAME,
        approach=approach,
        success=True,
        result=result,
        result_repr=srep,
        result_pretty=pretty,
        time_ms=dt,
        steps=steps,
        meta={"problem_type": ptype_effective},
    )
