"""Parser coverage across SymPy syntax and LaTeX. The natural-language path
is not exercised here because it requires Ollama; it is smoke-tested by
mocking in ``test_parser_nl.py``."""
from __future__ import annotations

import sympy as sp

from pru_math import problem_types as PT
from pru_math.parser import ParseError, parse


def _free(expr):
    return {s.name for s in expr.free_symbols}


def test_sympify_simple_polynomial():
    p = parse("x**2 + 3*x - 4")
    assert p.source_format == "sympy"
    assert _free(p.expression) == {"x"}


def test_sympify_equation_routes_to_solve():
    p = parse("Eq(x**2 - 5*x + 6, 0)")
    assert p.source_format == "sympy"
    assert p.problem_type == PT.SOLVE


def test_sympify_integral_routes_to_integrate():
    p = parse("Integral(x**2, (x, 0, 1))")
    assert p.source_format == "sympy"
    assert p.problem_type == PT.INTEGRATE


def test_sympify_derivative_routes_to_differentiate():
    p = parse("Derivative(sin(x), x)")
    assert p.source_format == "sympy"
    assert p.problem_type == PT.DIFFERENTIATE


def test_implicit_multiplication():
    # "2x" should become 2*x thanks to implicit multiplication.
    p = parse("2x + 3")
    assert isinstance(p.expression, sp.Basic)
    assert _free(p.expression) == {"x"}


def test_caret_as_power():
    p = parse("x^2 + 1")
    assert p.expression.equals(sp.Symbol("x") ** 2 + 1)


def test_latex_integral():
    try:
        p = parse(r"\int_{0}^{1} x^2 dx")
    except Exception as exc:
        # antlr-based LaTeX parser may be unavailable; accept a skip.
        import pytest
        pytest.skip(f"LaTeX parser unavailable: {exc}")
    assert p.source_format == "latex"
    assert p.problem_type == PT.INTEGRATE


def test_latex_trig_identity():
    try:
        p = parse(r"\sin(x)^2 + \cos(x)^2")
    except Exception as exc:
        import pytest
        pytest.skip(f"LaTeX parser unavailable: {exc}")
    assert p.source_format == "latex"


def test_empty_input_raises():
    import pytest
    with pytest.raises(ParseError):
        parse("")


def test_unparseable_input_raises():
    import pytest
    # No backslash so LaTeX is skipped; Ollama disabled via env.
    with pytest.raises(ParseError):
        parse("this is not a math expression at all !!!")
