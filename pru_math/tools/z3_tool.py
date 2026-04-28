"""Z3 tool — SMT-style equation solving and identity proving.

Supported subset
----------------
The translator (:func:`sympy_to_z3`) handles arithmetic over reals (and
integers when SymPy says ``is_integer``): ``+``, ``-``, ``*``, ``/``,
non-negative integer powers, equality, and the standard inequalities.
Anything outside that subset (transcendentals, unevaluated functions,
piecewise, etc.) raises :class:`Z3UnsupportedError`, which the reasoner
catches and treats as a tool failure — moving on to the next candidate.

Approaches
----------
- ``z3.solve``     — find a model satisfying the constraint(s)
- ``z3.solve.int`` — same, restricted to integer variables
- ``z3.prove``     — prove an equality/inequality holds for all values
                     (used for PROVE problems and as a cross-verifier)

Z3 returns *one* model when it finds one. For polynomial root-finding we
loop, asserting ``var ≠ each_found_root``, until the solver answers
``unsat`` — that gives the full set.

If ``z3-solver`` is not installed, :class:`Z3Tool.is_available` returns
``False`` and the registry filters the tool out entirely.
"""
from __future__ import annotations

import time
from typing import Any

import sympy as sp

from .. import problem_types as PT
from ..parser import ParsedProblem
from .base import CrossVerification, Tool, ToolResult


TOOL_NAME = "z3"

try:
    import z3   # type: ignore
    _Z3_AVAILABLE = True
except Exception:  # pragma: no cover — exercised when z3 is not installed
    z3 = None     # type: ignore
    _Z3_AVAILABLE = False


class Z3UnsupportedError(ValueError):
    """Raised when an expression contains a SymPy node Z3 can't represent."""


# --- Translator -------------------------------------------------------------

def sympy_to_z3(expr: sp.Basic, *, force_int: bool = False) -> "z3.ExprRef":
    """Translate a SymPy expression to Z3.

    All free symbols in ``expr`` become Z3 ``Real`` (or ``Int`` if
    ``force_int`` or the SymPy symbol carries ``is_integer=True``)
    constants of the same name.
    """
    if not _Z3_AVAILABLE:
        raise RuntimeError("z3 is not available")

    cache: dict[str, "z3.ExprRef"] = {}

    def var(sym: sp.Symbol) -> "z3.ExprRef":
        if sym.name in cache:
            return cache[sym.name]
        is_int = force_int or bool(getattr(sym, "is_integer", False))
        v = z3.Int(sym.name) if is_int else z3.Real(sym.name)
        cache[sym.name] = v
        return v

    def to(node: sp.Basic) -> "z3.ExprRef":
        # Numeric literals
        if node.is_Integer:
            return z3.IntVal(int(node))
        if node.is_Rational:
            return z3.RealVal(sp.Rational(node))
        if node.is_Float:
            return z3.RealVal(float(node))
        if isinstance(node, sp.Symbol):
            return var(node)
        if isinstance(node, sp.Add):
            args = [to(a) for a in node.args]
            return z3.Sum(args) if len(args) > 1 else args[0]
        if isinstance(node, sp.Mul):
            args = [to(a) for a in node.args]
            out = args[0]
            for a in args[1:]:
                out = out * a
            return out
        if isinstance(node, sp.Pow):
            base, exp = node.args
            if exp.is_Integer and int(exp) >= 0:
                e = int(exp)
                if e == 0:
                    return z3.IntVal(1)
                b = to(base)
                out = b
                for _ in range(e - 1):
                    out = out * b
                return out
            if exp.is_Integer and int(exp) < 0:
                e = -int(exp)
                b = to(base)
                out = b
                for _ in range(e - 1):
                    out = out * b
                return 1 / out
            raise Z3UnsupportedError(f"non-integer exponent: {sp.sstr(node)}")
        if isinstance(node, sp.Equality):
            return to(node.lhs) == to(node.rhs)
        if isinstance(node, sp.StrictLessThan):
            return to(node.lhs) < to(node.rhs)
        if isinstance(node, sp.LessThan):
            return to(node.lhs) <= to(node.rhs)
        if isinstance(node, sp.StrictGreaterThan):
            return to(node.lhs) > to(node.rhs)
        if isinstance(node, sp.GreaterThan):
            return to(node.lhs) >= to(node.rhs)
        raise Z3UnsupportedError(
            f"unsupported SymPy node {type(node).__name__}: {sp.sstr(node)}"
        )

    return to(sp.sympify(expr))


def _z3_value_to_sympy(val: "z3.ExprRef") -> sp.Basic:
    """Convert a Z3 numeric value to a SymPy literal."""
    if z3.is_int_value(val):
        return sp.Integer(val.as_long())
    if z3.is_rational_value(val):
        num = val.numerator_as_long()
        den = val.denominator_as_long()
        return sp.Rational(num, den)
    # algebraic numbers / RealVal: best-effort decimal fallback
    try:
        return sp.Float(val.as_decimal(20).rstrip("?"))
    except Exception:
        return sp.sympify(str(val))


# --- Approaches -------------------------------------------------------------

def _residual(expr: sp.Basic) -> sp.Basic:
    return expr.lhs - expr.rhs if isinstance(expr, sp.Equality) else expr


def _solve_for_all_roots(problem: ParsedProblem, *, force_int: bool
                         ) -> tuple[list[sp.Basic], list[str]]:
    expr = _residual(problem.expression)
    free = sorted(expr.free_symbols, key=lambda s: s.name)
    if not free:
        raise Z3UnsupportedError("z3.solve: no free variables")
    target = problem.target_symbol or free[0]
    z_expr = sympy_to_z3(expr, force_int=force_int)
    z_target = sympy_to_z3(target, force_int=force_int)

    s = z3.Solver()
    s.set("timeout", 5000)
    s.add(z_expr == 0)
    found: list[sp.Basic] = []
    steps = [
        f"Translate {sp.sstr(expr)} = 0 to Z3",
        f"{'Int' if force_int else 'Real'} domain over variable {target}",
    ]
    while s.check() == z3.sat:
        model = s.model()
        if z_target.decl() not in [d for d in model.decls()]:
            break
        v = model[z_target.decl()]
        if v is None:
            break
        sol = _z3_value_to_sympy(v)
        found.append(sol)
        s.add(z_target != v)
        if len(found) >= 16:
            steps.append("Stopped after 16 distinct roots")
            break
    if not found:
        raise Z3UnsupportedError("z3.solve: no satisfying assignment found")
    steps.append(f"Z3 enumerated {len(found)} distinct root(s)")
    return found, steps


def _solve_real(problem: ParsedProblem):
    return _solve_for_all_roots(problem, force_int=False)


def _solve_int(problem: ParsedProblem):
    return _solve_for_all_roots(problem, force_int=True)


def _prove_identity(problem: ParsedProblem) -> tuple[bool, list[str]]:
    """For a PROVE-style problem (lhs == rhs), assert the negation and
    check unsat. Returns ``True`` (proved) or raises if the prover times
    out / returns sat / cannot translate."""
    expr = problem.expression
    if not isinstance(expr, sp.Equality):
        raise Z3UnsupportedError("z3.prove: expects an Equality")
    z_lhs = sympy_to_z3(expr.lhs)
    z_rhs = sympy_to_z3(expr.rhs)
    s = z3.Solver()
    s.set("timeout", 5000)
    s.add(z_lhs != z_rhs)
    res = s.check()
    if res == z3.unsat:
        return True, [
            f"Translate {sp.sstr(expr)} to Z3",
            "Assert ¬(lhs = rhs); Z3 returns unsat ⇒ identity holds",
        ]
    if res == z3.sat:
        m = s.model()
        raise Z3UnsupportedError(f"z3.prove: counter-example {m}")
    raise Z3UnsupportedError(f"z3.prove: solver returned {res}")


_APPROACHES = {
    PT.SOLVE: [
        ("z3.solve", _solve_real),
        ("z3.solve.int", _solve_int),
    ],
    PT.PROVE: [
        ("z3.prove", _prove_identity),
    ],
}


def candidate_approaches(problem_type: str) -> list[str]:
    return [name for name, _ in _APPROACHES.get(problem_type, [])]


# --- Tool wrapper ----------------------------------------------------------

def _fmt_solutions(solutions: list[sp.Basic]) -> tuple[str, str]:
    srep = "[" + ", ".join(sp.srepr(v) for v in solutions) + "]"
    pretty = "[" + ", ".join(sp.sstr(v) for v in solutions) + "]"
    return srep, pretty


class Z3Tool(Tool):
    name = TOOL_NAME
    # Z3 actually *proves* equality, so it's the strongest cross-verifier
    # the engine has when the problem fits the SMT subset.
    cross_verify_priority = 100

    def is_available(self) -> bool:
        return _Z3_AVAILABLE

    def candidate_approaches(self, problem_type: str):
        return candidate_approaches(problem_type) if _Z3_AVAILABLE else []

    def can_handle(self, fingerprint):
        if not _Z3_AVAILABLE:
            return 0.0
        ptype = fingerprint.get("problem_type")
        if ptype not in _APPROACHES:
            return 0.0
        # Z3 shines on polynomial/integer/linear constraints; anything with
        # transcendental functions is a likely failure.
        flags = fingerprint.get("function_flags") or {}
        if any(flags.get(k) for k in
               ("trig", "inv_trig", "hyp", "log", "exp", "factorial", "gamma")):
            return 0.1
        deg = fingerprint.get("polynomial_degree")
        if deg is not None and deg <= 6:
            return 0.7
        return 0.4

    def _solve_with(self, problem: ParsedProblem, approach: str) -> ToolResult:
        if not _Z3_AVAILABLE:
            return ToolResult(tool=self.name, approach=approach, success=False,
                              error="z3-solver is not installed")
        approaches = dict((n, f) for lst in _APPROACHES.values() for n, f in lst)
        fn = approaches.get(approach)
        if fn is None:
            return ToolResult(tool=self.name, approach=approach, success=False,
                              error=f"unknown z3 approach: {approach!r}")
        t0 = time.perf_counter()
        try:
            result, steps = fn(problem)
        except Z3UnsupportedError as exc:
            dt = (time.perf_counter() - t0) * 1000.0
            return ToolResult(tool=self.name, approach=approach, success=False,
                              time_ms=dt, error=f"Z3UnsupportedError: {exc}")
        except Exception as exc:
            dt = (time.perf_counter() - t0) * 1000.0
            return ToolResult(tool=self.name, approach=approach, success=False,
                              time_ms=dt, error=f"{type(exc).__name__}: {exc}")
        dt = (time.perf_counter() - t0) * 1000.0
        if isinstance(result, list):
            srep, pretty = _fmt_solutions(result)
        elif isinstance(result, bool):
            srep = "True" if result else "False"
            pretty = "proved" if result else "not proved"
        else:
            srep, pretty = sp.srepr(result), sp.sstr(result)
        return ToolResult(
            tool=self.name, approach=approach, success=True,
            result=result, result_repr=srep, result_pretty=pretty,
            time_ms=dt, steps=steps,
            meta={"problem_type": problem.problem_type},
        )

    # Domain-specific cross-verification: try to *prove* the candidate is a
    # solution (or matches the symbolic form). For SOLVE problems we
    # substitute each candidate root and assert ¬(residual = 0).
    def cross_verify(self, problem, candidate) -> CrossVerification:
        if not _Z3_AVAILABLE:
            return CrossVerification(self.name, "unsupported", "z3 not installed")
        if problem.problem_type != PT.SOLVE:
            # Fallback to the default (solve-fresh-and-compare).
            return super().cross_verify(problem, candidate)
        try:
            residual = _residual(problem.expression)
            target = problem.target_symbol or sorted(
                residual.free_symbols, key=lambda s: s.name,
            )[0]
            if not isinstance(candidate, (list, tuple, set)):
                candidate = [candidate]
            for sol in candidate:
                substituted = sp.simplify(residual.subs(target, sol))
                if substituted == 0:
                    continue
                z_resid = sympy_to_z3(substituted)
                s = z3.Solver()
                s.set("timeout", 3000)
                s.add(z_resid != 0)
                if s.check() == z3.unsat:
                    continue
                return CrossVerification(self.name, "disagree",
                                         f"Z3: residual at {sol} is non-zero")
            return CrossVerification(self.name, "agree",
                                     f"Z3: substituted {len(candidate)} root(s) → 0")
        except Z3UnsupportedError as exc:
            return CrossVerification(self.name, "unsupported", str(exc))
        except Exception as exc:
            return CrossVerification(self.name, "inconclusive",
                                     f"{type(exc).__name__}: {exc}")
