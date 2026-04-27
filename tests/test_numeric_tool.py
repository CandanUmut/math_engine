"""Numeric tool: scipy/mpmath-backed approaches."""
from __future__ import annotations

import sympy as sp

from pru_math.parser import parse
from pru_math.tools.numeric_tool import NumericTool


tool = NumericTool()


def test_numeric_solves_quadratic_roots():
    r = tool.solve_with(parse("Eq(x**2 - 5*x + 6, 0)"), "numeric.fsolve")
    assert r.success
    vals = sorted(float(v) for v in r.result)
    assert len(vals) == 2
    assert abs(vals[0] - 2.0) < 1e-6
    assert abs(vals[1] - 3.0) < 1e-6


def test_brentq_finds_sign_change_roots():
    # cubic with three real roots at 1, 2, 3
    r = tool.solve_with(parse("Eq(x**3 - 6*x**2 + 11*x - 6, 0)"), "numeric.brentq")
    assert r.success, r.error
    vals = sorted(float(v) for v in r.result)
    assert len(vals) == 3
    for got, want in zip(vals, [1.0, 2.0, 3.0]):
        assert abs(got - want) < 1e-6


def test_numeric_quad_definite_integral():
    r = tool.solve_with(parse("Integral(x**2, (x, 0, 1))"), "numeric.quad")
    assert r.success
    assert abs(float(r.result) - 1.0 / 3.0) < 1e-8


def test_numeric_quad_rejects_indefinite():
    r = tool.solve_with(parse("Integral(x**2, x)"), "numeric.quad")
    assert not r.success
    assert "definite" in (r.error or "").lower()


def test_can_handle_lower_for_low_degree_polynomial():
    fp_low = {"problem_type": "solve", "polynomial_degree": 2}
    fp_high = {"problem_type": "solve", "polynomial_degree": 8}
    fp_trans = {"problem_type": "solve", "polynomial_degree": None}
    assert tool.can_handle(fp_low) < tool.can_handle(fp_high)
    assert tool.can_handle(fp_low) < tool.can_handle(fp_trans)


def test_can_handle_zero_for_unsupported_type():
    assert tool.can_handle({"problem_type": "differentiate"}) == 0.0


def test_unknown_approach_returns_error_not_raise():
    r = tool.solve_with(parse("x"), "numeric.bogus")
    assert not r.success
    assert "unknown numeric approach" in (r.error or "")


def test_evalf_evaluates_closed_form():
    r = tool.solve_with(parse("Integral(sin(x), (x, 0, pi))"), "numeric.evalf")
    assert r.success
    assert abs(float(r.result) - 2.0) < 1e-12


def test_evalf_rejects_free_symbols():
    r = tool.solve_with(parse("x + 1"), "numeric.evalf")
    assert not r.success
