"""Cross-verifier priority (Phase 6).

Z3 (proof) should beat numeric (empirical) which beats SymPy (re-derivation)
when the registry picks a second tool to confirm a verified primary.
"""
from __future__ import annotations

import pytest

from pru_math.parser import parse
from pru_math.tools.registry import default_registry
from pru_math.tools.z3_tool import _Z3_AVAILABLE


def test_default_priority_ordering():
    r = default_registry()
    sympy = r.get("sympy")
    numeric = r.get("numeric")
    z3 = r.get("z3")
    assert sympy and numeric and z3
    assert z3.cross_verify_priority > numeric.cross_verify_priority
    assert numeric.cross_verify_priority > sympy.cross_verify_priority


@pytest.mark.skipif(not _Z3_AVAILABLE, reason="z3-solver not installed")
def test_picker_chooses_z3_over_numeric_for_solve_when_primary_is_sympy():
    r = default_registry()
    problem = parse("Eq(x**2 - 5*x + 6, 0)")
    picked = r.pick_cross_verifier(primary_tool="sympy", problem=problem)
    assert picked is not None
    assert picked.name == "z3"


def test_picker_returns_none_when_no_eligible_tool():
    r = default_registry()
    # SIMPLIFY is only handled by the SymPy tool in the default registry.
    problem = parse("sin(x)**2 + cos(x)**2")
    picked = r.pick_cross_verifier(primary_tool="sympy", problem=problem)
    assert picked is None


def test_picker_skips_primary_tool():
    r = default_registry()
    problem = parse("Eq(x**2 - 5*x + 6, 0)")
    picked = r.pick_cross_verifier(primary_tool="z3", problem=problem)
    # When Z3 is the primary, it must not pick itself; numeric is next.
    assert picked is not None
    assert picked.name != "z3"
