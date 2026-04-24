from __future__ import annotations

import sympy as sp

from pru_math.parser import parse
from pru_math.tools import sympy_tool
from pru_math.verifier import verify, VERIFIED, REFUTED


def test_verify_solve_correct():
    parsed = parse("Eq(x**2 - 5*x + 6, 0)")
    out = sympy_tool.solve(parsed)
    v = verify(parsed, out.result)
    assert v.status == VERIFIED


def test_verify_solve_refutes_wrong_answer():
    parsed = parse("Eq(x**2 - 5*x + 6, 0)")
    v = verify(parsed, [sp.Integer(99)])
    assert v.status == REFUTED


def test_verify_integrate_indefinite():
    parsed = parse("Integral(cos(x), x)")
    out = sympy_tool.solve(parsed)
    v = verify(parsed, out.result)
    assert v.status == VERIFIED


def test_verify_integrate_definite():
    parsed = parse("Integral(x**2, (x, 0, 1))")
    out = sympy_tool.solve(parsed)
    v = verify(parsed, out.result)
    assert v.status == VERIFIED


def test_verify_differentiate():
    parsed = parse("Derivative(sin(x), x)")
    out = sympy_tool.solve(parsed)
    v = verify(parsed, out.result)
    assert v.status == VERIFIED


def test_verify_simplify_identity():
    parsed = parse("sin(x)**2 + cos(x)**2")
    out = sympy_tool.solve(parsed)
    v = verify(parsed, out.result)
    assert v.status == VERIFIED


def test_verify_simplify_detects_wrong_candidate():
    parsed = parse("sin(x)**2 + cos(x)**2")
    # wrong candidate: 2 is not equal to 1
    v = verify(parsed, sp.Integer(2))
    assert v.status == REFUTED
