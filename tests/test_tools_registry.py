"""Registry contract: tool availability filtering, candidate generation,
and cross-verifier picking."""
from __future__ import annotations

import pytest

from pru_math import problem_types as PT
from pru_math.parser import parse
from pru_math.tools import (
    CandidatePair,
    Tool,
    ToolRegistry,
    ToolResult,
    default_registry,
)
from pru_math.tools.base import CrossVerification


class _StubTool(Tool):
    """Minimal Tool used by the registry tests."""

    def __init__(self, name, *, available=True, approaches=None,
                 confidence=0.5, can_xv=False):
        self.name = name
        self._available = available
        self._approaches = approaches or {}
        self._confidence = confidence
        self._can_xv = can_xv

    def is_available(self):
        return self._available

    def candidate_approaches(self, problem_type):
        return list(self._approaches.get(problem_type, []))

    def can_handle(self, fingerprint):
        return self._confidence

    def solve_with(self, problem, approach):
        return ToolResult(
            tool=self.name, approach=approach, success=True,
            result="ok", result_pretty="ok", result_repr="'ok'",
        )

    def can_cross_verify(self, problem):
        return self._can_xv

    def cross_verify(self, problem, candidate):
        return CrossVerification(self.name, "agree", "stub")


def test_registry_filters_unavailable_tools():
    a = _StubTool("a", available=True, approaches={PT.SOLVE: ["a.x"]})
    b = _StubTool("b", available=False, approaches={PT.SOLVE: ["b.x"]})
    r = ToolRegistry([a, b])
    assert {t.name for t in r.available_tools()} == {"a"}
    cands = r.candidates_for(problem_type=PT.SOLVE, fingerprint={})
    assert [c.tool for c in cands] == ["a"]


def test_candidates_sorted_by_confidence_desc():
    a = _StubTool("a", approaches={PT.SOLVE: ["a.x"]}, confidence=0.4)
    b = _StubTool("b", approaches={PT.SOLVE: ["b.x"]}, confidence=0.9)
    c = _StubTool("c", approaches={PT.SOLVE: ["c.x"]}, confidence=0.7)
    r = ToolRegistry([a, b, c])
    cands = r.candidates_for(problem_type=PT.SOLVE, fingerprint={})
    assert [c.tool for c in cands] == ["b", "c", "a"]


def test_zero_confidence_excluded_below_min_threshold():
    a = _StubTool("a", approaches={PT.SOLVE: ["a.x"]}, confidence=0.0)
    b = _StubTool("b", approaches={PT.SOLVE: ["b.x"]}, confidence=0.5)
    r = ToolRegistry([a, b])
    cands = r.candidates_for(problem_type=PT.SOLVE, fingerprint={},
                             min_confidence=0.01)
    assert [c.tool for c in cands] == ["b"]


def test_pick_cross_verifier_skips_primary_and_unsupported():
    primary = _StubTool("primary", approaches={PT.SOLVE: ["x"]}, can_xv=True)
    secondary = _StubTool("secondary", approaches={PT.SOLVE: ["y"]}, can_xv=True)
    other = _StubTool("other", approaches={PT.SOLVE: []}, can_xv=False)
    r = ToolRegistry([primary, secondary, other])
    problem = parse("Eq(x**2 - 4, 0)")
    picked = r.pick_cross_verifier(primary_tool="primary", problem=problem)
    assert picked is not None and picked.name == "secondary"


def test_pick_cross_verifier_returns_none_when_only_primary():
    only = _StubTool("only", approaches={PT.SOLVE: ["x"]}, can_xv=True)
    r = ToolRegistry([only])
    problem = parse("Eq(x**2 - 4, 0)")
    assert r.pick_cross_verifier(primary_tool="only", problem=problem) is None


def test_default_registry_has_expected_tools():
    r = default_registry()
    names = {t.name for t in r.all_tools()}
    assert names == {"sympy", "numeric", "z3", "wolfram"}
    # SymPy and numeric are unconditionally available.
    assert any(t.name == "sympy" and t.is_available() for t in r.all_tools())
    assert any(t.name == "numeric" and t.is_available() for t in r.all_tools())


def test_solve_with_unknown_tool_returns_error_result():
    r = ToolRegistry([_StubTool("a", approaches={PT.SOLVE: ["a.x"]})])
    res = r.solve_with(tool="ghost", approach="ghost.x", problem=parse("x"))
    assert not res.success
    assert "not registered" in (res.error or "")


def test_candidate_pair_to_pair():
    cp = CandidatePair(tool="t", approach="t.x", confidence=0.9)
    assert cp.to_pair() == ("t", "t.x")
