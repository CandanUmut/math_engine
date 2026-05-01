"""Numerical and symbolic verification of candidate answers.

Verification is **separate** from solving. A tool produces a candidate; the
verifier says whether that candidate is trustworthy. This keeps the
reasoner honest: an answer is never presented as correct unless a verifier
actually checked it.

Statuses
--------
- ``verified``     — sampled / substituted checks all passed
- ``refuted``      — at least one check failed
- ``inconclusive`` — we could not construct a meaningful check (e.g. symbolic
                     identity with no free variables, or integral over an
                     unbounded region we didn't attempt to sample)
- ``no_change``    — for SIMPLIFY/FACTOR/EXPAND, the tool returned the input
                     unchanged. Mathematically valid but useless: the engine
                     did not actually transform anything.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

import sympy as sp

from . import problem_types as PT
from .parser import ParsedProblem


VERIFIED = "verified"
REFUTED = "refuted"
INCONCLUSIVE = "inconclusive"
NO_CHANGE = "no_change"

_DEFAULT_TOL = 1e-7
_SAMPLES = 6
_SAMPLE_RANGE = (0.37, 3.14)   # keep away from zero and common singularities


@dataclass
class VerificationResult:
    status: str
    detail: str = ""
    checks: int = 0


def _random_point(symbols: list[sp.Symbol], rng: random.Random) -> dict[sp.Symbol, float]:
    lo, hi = _SAMPLE_RANGE
    return {s: rng.uniform(lo, hi) for s in symbols}


def _numeric(expr: sp.Basic, subs: dict[sp.Symbol, float]) -> complex | None:
    try:
        v = complex(sp.N(expr.subs(subs)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not (math.isfinite(v.real) and math.isfinite(v.imag)):
        return None
    return v


def _close(a: complex, b: complex, tol: float = _DEFAULT_TOL) -> bool:
    if a is None or b is None:
        return False
    scale = max(1.0, abs(a), abs(b))
    return abs(a - b) <= tol * scale


def _verify_identity(lhs: sp.Basic, rhs: sp.Basic, seed: int = 0) -> VerificationResult:
    """Check lhs == rhs by sampling shared free variables."""
    syms = sorted((lhs.free_symbols | rhs.free_symbols), key=lambda s: s.name)
    # If no free symbols, evaluate both to numbers and compare directly.
    if not syms:
        la = _numeric(lhs, {})
        ra = _numeric(rhs, {})
        if la is None or ra is None:
            # Fall back to symbolic simplification
            if sp.simplify(lhs - rhs) == 0:
                return VerificationResult(VERIFIED, "symbolic simplify matches", 1)
            return VerificationResult(INCONCLUSIVE, "could not evaluate numerically", 0)
        if _close(la, ra):
            return VerificationResult(VERIFIED, "constants agree numerically", 1)
        return VerificationResult(REFUTED, f"{la} vs {ra}", 1)

    rng = random.Random(seed)
    passed = 0
    for _ in range(_SAMPLES):
        subs = _random_point(syms, rng)
        la = _numeric(lhs, subs)
        ra = _numeric(rhs, subs)
        if la is None or ra is None:
            continue
        if not _close(la, ra):
            return VerificationResult(
                REFUTED,
                f"mismatch at {subs}: {la} vs {ra}",
                passed + 1,
            )
        passed += 1
    if passed == 0:
        return VerificationResult(INCONCLUSIVE, "no usable sample points", 0)
    return VerificationResult(VERIFIED, f"{passed}/{_SAMPLES} sample points agreed", passed)


def _verify_solve(problem: ParsedProblem, solutions: Any) -> VerificationResult:
    """For a solve problem, substitute each solution into the equation."""
    expr = problem.expression
    if isinstance(expr, sp.Equality):
        residual = sp.simplify(expr.lhs - expr.rhs)
    else:
        residual = expr
    target = problem.target_symbol or (sorted(residual.free_symbols, key=lambda s: s.name)[:1] or [None])[0]
    if target is None:
        return VerificationResult(INCONCLUSIVE, "no target variable to substitute")

    sol_list: list[Any]
    if isinstance(solutions, (list, tuple, set)):
        sol_list = list(solutions)
    else:
        sol_list = [solutions]

    if not sol_list:
        # "no solutions" — verify that residual has no roots by sampling.
        # Inconclusive in the general case; keep honest.
        return VerificationResult(INCONCLUSIVE, "empty solution set; not re-derived")

    passed = 0
    for sol in sol_list:
        substituted = residual.subs(target, sol)
        simplified = sp.simplify(substituted)
        if simplified == 0:
            passed += 1
            continue
        # numeric fallback
        val = _numeric(simplified, {})
        if val is not None and _close(val, 0):
            passed += 1
            continue
        return VerificationResult(REFUTED, f"sol {sol} gives residual {simplified}", passed)
    return VerificationResult(VERIFIED, f"all {passed} solution(s) substitute to zero", passed)


def _verify_integrate(problem: ParsedProblem, result: Any) -> VerificationResult:
    """For indefinite integrals: differentiate the answer and compare to the
    integrand. For definite: compare numerical value against quadrature."""
    expr = problem.expression
    if isinstance(expr, sp.Integral):
        integrand = expr.function
        var = expr.variables[0] if expr.variables else None
        limits = expr.limits
    else:
        integrand = expr
        var = problem.target_symbol
        limits = None

    if var is None:
        return VerificationResult(INCONCLUSIVE, "no integration variable")

    # Definite: both sides to a number.
    if limits and len(limits[0]) == 3:
        try:
            exact = complex(sp.N(result))
            num = complex(sp.N(sp.Integral(integrand, *limits).evalf()))
        except Exception:
            return VerificationResult(INCONCLUSIVE, "could not evaluate definite integral")
        if _close(exact, num, tol=1e-5):
            return VerificationResult(VERIFIED, f"definite integral matches quadrature ({num:.6g})", 1)
        return VerificationResult(REFUTED, f"closed-form {exact} vs quadrature {num}")

    # Indefinite: derivative must equal integrand (up to constant).
    diffed = sp.simplify(sp.diff(result, var) - integrand)
    if diffed == 0:
        return VerificationResult(VERIFIED, "derivative matches integrand symbolically", 1)
    return _verify_identity(sp.diff(result, var), integrand, seed=1)


def _verify_differentiate(problem: ParsedProblem, result: Any) -> VerificationResult:
    expr = problem.expression
    if isinstance(expr, sp.Derivative):
        original = expr.expr
        var = expr.variables[0] if expr.variables else None
    else:
        original = expr
        var = problem.target_symbol
    if var is None:
        return VerificationResult(INCONCLUSIVE, "no differentiation variable")
    expected = sp.diff(original, var)
    return _verify_identity(sp.sympify(result), expected, seed=2)


def _is_unchanged(result: Any, original: sp.Basic) -> bool:
    """True iff `result` is the same expression as `original` (structurally,
    after canonicalization). Catches the case where a SIMPLIFY-family tool
    returned the input verbatim — mathematically equal, but the engine did
    not actually transform anything.

    The parser uses ``evaluate=False``, while tool results come back fully
    evaluated, so direct == on the SymPy tree can return False even when the
    structures are identical. We normalize both through ``sympify(str(...))``
    to put them on equal footing.
    """
    try:
        r = sp.sympify(result)
    except (sp.SympifyError, TypeError, ValueError):
        return False
    try:
        r_norm = sp.sympify(str(r))
        o_norm = sp.sympify(str(original))
    except Exception:
        return r == original
    return r_norm == o_norm


def _verify_simplify(problem: ParsedProblem, result: Any) -> VerificationResult:
    if _is_unchanged(result, problem.expression):
        return VerificationResult(
            NO_CHANGE,
            "tool returned the input unchanged — no simplification occurred",
            0,
        )
    return _verify_identity(sp.sympify(result), problem.expression, seed=3)


def _verify_factor_or_expand(problem: ParsedProblem, result: Any) -> VerificationResult:
    # factoring and expansion must be exact rewrites of the input.
    if _is_unchanged(result, problem.expression):
        return VerificationResult(
            NO_CHANGE,
            "tool returned the input unchanged — no factoring/expansion occurred",
            0,
        )
    return _verify_identity(sp.sympify(result), problem.expression, seed=4)


def _verify_limit(problem: ParsedProblem, result: Any) -> VerificationResult:
    expr = problem.expression
    if not isinstance(expr, sp.Limit):
        return VerificationResult(INCONCLUSIVE, "limit verification requires Limit expression")
    # Sanity: recomputing via doit() should agree.
    recomputed = expr.doit()
    if sp.simplify(recomputed - sp.sympify(result)) == 0:
        return VerificationResult(VERIFIED, "recomputed limit agrees", 1)
    return VerificationResult(REFUTED, f"recomputed {recomputed} vs {result}")


def _verify_evaluate(problem: ParsedProblem, result: Any) -> VerificationResult:
    val = sp.sympify(result)
    if not val.free_symbols and not problem.expression.free_symbols:
        lhs = _numeric(val, {})
        rhs = _numeric(problem.expression, {})
        if lhs is not None and rhs is not None and _close(lhs, rhs):
            return VerificationResult(VERIFIED, "numerical values agree", 1)
    return _verify_identity(val, problem.expression, seed=5)


_VERIFIERS = {
    PT.SOLVE: _verify_solve,
    PT.INTEGRATE: _verify_integrate,
    PT.DIFFERENTIATE: _verify_differentiate,
    PT.SIMPLIFY: _verify_simplify,
    PT.FACTOR: _verify_factor_or_expand,
    PT.EXPAND: _verify_factor_or_expand,
    PT.LIMIT: _verify_limit,
    PT.EVALUATE: _verify_evaluate,
}


def verify(problem: ParsedProblem, result: Any) -> VerificationResult:
    """Dispatch verification by problem type."""
    verifier = _VERIFIERS.get(problem.problem_type)
    if verifier is None:
        return VerificationResult(INCONCLUSIVE, f"no verifier for problem_type={problem.problem_type}")
    try:
        return verifier(problem, result)
    except Exception as exc:
        return VerificationResult(INCONCLUSIVE, f"{type(exc).__name__}: {exc}")
