from __future__ import annotations

import sympy as sp

from pru_math import problem_types as PT
from pru_math.parser import parse
from pru_math.tools import sympy_tool


def test_solve_quadratic():
    r = sympy_tool.solve(parse("Eq(x**2 - 5*x + 6, 0)"))
    assert r.success
    vals = {int(sp.sympify(v)) for v in r.result}
    assert vals == {2, 3}


def test_integrate_definite():
    r = sympy_tool.solve(parse("Integral(x**2, (x, 0, 1))"))
    assert r.success
    assert sp.sympify(r.result) == sp.Rational(1, 3)


def test_integrate_indefinite():
    # Take the derivative of the answer and check it equals the integrand.
    r = sympy_tool.solve(parse("Integral(cos(x), x)"))
    assert r.success
    x = sp.Symbol("x")
    assert sp.simplify(sp.diff(r.result, x) - sp.cos(x)) == 0


def test_differentiate():
    r = sympy_tool.solve(parse("Derivative(sin(x), x)"))
    assert r.success
    assert sp.simplify(sp.sympify(r.result) - sp.cos(sp.Symbol("x"))) == 0


def test_simplify_trig_identity():
    # The parser classifies bare expressions as SIMPLIFY.
    r = sympy_tool.solve(parse("sin(x)**2 + cos(x)**2"))
    assert r.success
    assert sp.simplify(sp.sympify(r.result) - 1) == 0


def test_factor():
    # We fabricate a FACTOR problem manually; parser classifies bare
    # expressions as SIMPLIFY, not FACTOR.
    parsed = parse("x**2 - 5*x + 6")
    parsed.problem_type = PT.FACTOR
    r = sympy_tool.solve(parsed)
    assert r.success
    expected = sp.factor(sp.Symbol("x") ** 2 - 5 * sp.Symbol("x") + 6)
    assert sp.simplify(sp.sympify(r.result) - expected) == 0


def test_tool_reports_error_without_raising():
    # Deliberately craft something that will fail: differentiate with no variable.
    parsed = parse("42")
    parsed.problem_type = PT.DIFFERENTIATE
    parsed.target_symbol = None
    r = sympy_tool.solve(parsed)
    assert r.success is False
    assert r.error and "no variable" in r.error
