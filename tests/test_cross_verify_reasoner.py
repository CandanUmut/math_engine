"""End-to-end Phase 4: reasoner uses the full registry, cross-verifies,
persists cross-verification fields on the attempt."""
from __future__ import annotations

import sympy as sp

from pru_math.graph import RelationalGraph
from pru_math.reasoner import Reasoner
from pru_math.store import Store


def _make(tmp_store: Store, tmp_graph: RelationalGraph,
          *, cross_verify: bool) -> Reasoner:
    return Reasoner(store=tmp_store, graph=tmp_graph, cross_verify=cross_verify)


def test_reasoner_uses_multiple_tools_in_candidates(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    out = _make(tmp_store, tmp_graph, cross_verify=False).solve(
        "Eq(x**2 - 5*x + 6, 0)",
    )
    assert out.ok
    # The decision step should list candidates from at least 2 tools.
    decision = next(s for s in out.trace if s.kind == "decision")
    tools_seen = {c["tool"] for c in decision.detail["candidates"]}
    assert "sympy" in tools_seen
    assert len(tools_seen) >= 2  # sympy + numeric (Z3 may also be there)


def test_cross_verify_runs_when_enabled(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    out = _make(tmp_store, tmp_graph, cross_verify=True).solve(
        "Integral(x**2, (x, 0, 1))",
    )
    assert out.ok
    # cross_verify trace step exists on a verified primary
    cv_steps = [s for s in out.trace if s.kind == "cross_verify"]
    assert cv_steps, "expected a cross_verify trace step"
    cv = cv_steps[0]
    assert cv.detail.get("status") in {"agree", "disagree", "inconclusive", "unsupported"}


def test_cross_verify_status_persisted_on_attempt(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    out = _make(tmp_store, tmp_graph, cross_verify=True).solve(
        "Integral(x**2, (x, 0, 1))",
    )
    rec = tmp_store.list_attempts(out.problem_id)
    chosen = [a for a in rec if a.verification_status == "verified"]
    assert chosen
    # At least one of them should carry a cross-verify outcome.
    assert any(a.cross_verify_status for a in chosen)


def test_cross_verify_skipped_when_disabled(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    out = _make(tmp_store, tmp_graph, cross_verify=False).solve(
        "Integral(x**2, (x, 0, 1))",
    )
    assert out.ok
    cv_steps = [s for s in out.trace if s.kind == "cross_verify"]
    assert cv_steps == []


def test_cross_verify_skipped_when_no_second_tool_can_handle(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    # SIMPLIFY is only handled by the SymPy tool in the default registry,
    # so the picker returns None and we should see a "no second tool"
    # trace step rather than an exception.
    out = _make(tmp_store, tmp_graph, cross_verify=True).solve(
        "sin(x)**2 + cos(x)**2",
    )
    assert out.ok
    cv_steps = [s for s in out.trace if s.kind == "cross_verify"]
    assert cv_steps
    assert "no second tool" in cv_steps[0].summary.lower()
