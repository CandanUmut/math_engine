"""Z3 tool: SymPy->Z3 translation, solve, prove, cross-verify."""
from __future__ import annotations

import pytest
import sympy as sp

from pru_math import problem_types as PT
from pru_math.parser import parse
from pru_math.tools.z3_tool import (
    Z3Tool,
    Z3UnsupportedError,
    sympy_to_z3,
    _Z3_AVAILABLE,
)


pytestmark = pytest.mark.skipif(not _Z3_AVAILABLE, reason="z3-solver not installed")

tool = Z3Tool()


def test_z3_available():
    assert tool.is_available()


def test_translator_handles_polynomial():
    expr = sympy_to_z3(sp.Symbol("x") ** 2 + 3 * sp.Symbol("x") + 1)
    assert expr is not None  # smoke


def test_translator_rejects_transcendental():
    with pytest.raises(Z3UnsupportedError):
        sympy_to_z3(sp.sin(sp.Symbol("x")))


def test_z3_solves_quadratic_real():
    r = tool.solve_with(parse("Eq(x**2 - 5*x + 6, 0)"), "z3.solve")
    assert r.success
    vals = {sp.Rational(v) for v in r.result}
    assert vals == {sp.Rational(2), sp.Rational(3)}


def test_z3_solves_quadratic_int_domain():
    r = tool.solve_with(parse("Eq(x**2 - 5*x + 6, 0)"), "z3.solve.int")
    assert r.success
    vals = {int(v) for v in r.result}
    assert vals == {2, 3}


def test_z3_unsupported_transcendental():
    # sin(x) - 0.5 = 0 — Z3 cannot translate sin
    r = tool.solve_with(parse("Eq(sin(x) - 1/2, 0)"), "z3.solve")
    assert not r.success
    assert "Z3UnsupportedError" in (r.error or "") or "unsupported" in (r.error or "").lower()


def test_can_handle_zero_when_trig_present():
    fp = {"problem_type": "solve", "polynomial_degree": None,
          "function_flags": {"trig": True}}
    assert tool.can_handle(fp) <= 0.2


def test_can_handle_high_for_low_degree_polynomial():
    fp = {"problem_type": "solve", "polynomial_degree": 3,
          "function_flags": {}}
    assert tool.can_handle(fp) >= 0.5


def test_cross_verify_agree_on_correct_roots():
    problem = parse("Eq(x**2 - 5*x + 6, 0)")
    cv = tool.cross_verify(problem, [sp.Integer(2), sp.Integer(3)])
    assert cv.status == "agree"


def test_cross_verify_disagree_on_wrong_roots():
    problem = parse("Eq(x**2 - 5*x + 6, 0)")
    cv = tool.cross_verify(problem, [sp.Integer(99), sp.Integer(100)])
    assert cv.status == "disagree"


def test_cross_verify_unsupported_for_non_solve():
    # Z3 cross_verify falls back to default for non-solve types; with a
    # transcendental simplify problem, it should report unsupported or
    # inconclusive but never agree falsely.
    problem = parse("sin(x)**2 + cos(x)**2")
    cv = tool.cross_verify(problem, sp.Integer(1))
    assert cv.status in {"agree", "inconclusive", "unsupported"}
