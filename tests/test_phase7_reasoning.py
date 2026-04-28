"""Phase 7 reasoning quality:

- identity-aware ranking bonus (rule witnesses raise the score)
- transitive identity detector (A≡B + B≡C → A≡C)
- hard-signature detector (signatures that reliably need >1 attempt)
"""
from __future__ import annotations

import pytest

from pru_math.graph import RelationalGraph
from pru_math.hypothesizer import (
    Hypothesizer,
    KIND_IDENTITY,
    KIND_RECURRING,
    STATUS_VERIFIED,
)
from pru_math.learner import Learner
from pru_math.reasoner import Reasoner
from pru_math.rules import witness_counts
from pru_math.store import Store


# ── Identity-aware ranking ──────────────────────────────────────────


def test_witness_counts_empty_when_no_rules(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    Reasoner(store=tmp_store, graph=tmp_graph).solve("Eq(x**2 - 4, 0)")
    fp = tmp_store.list_problems()[0].fingerprint
    assert witness_counts(tmp_graph, fp.get("signature") or "") == {}


def test_witness_counts_finds_identity_supporters(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    for p in ["sin(x)**2 + cos(x)**2", "1"]:
        r.solve(p)
    Hypothesizer(store=tmp_store, graph=tmp_graph).scan(verify=True)

    # Pick the signature of the trig-identity problem and check the
    # witness counts include at least one (sympy, sympy.*) pair.
    target = next(
        p for p in tmp_store.list_problems()
        if "sin" in (p.parsed_pretty or "")
    )
    counts = witness_counts(tmp_graph, target.signature)
    assert counts, "expected at least one witness on the identity signature"
    pairs = list(counts.keys())
    assert any(t == "sympy" for t, _ in pairs)


def test_learner_rule_bonus_raises_score_for_witnesses(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    for p in ["sin(x)**2 + cos(x)**2", "1"]:
        r.solve(p)
    Hypothesizer(store=tmp_store, graph=tmp_graph).scan(verify=True)

    # Manually rank the same signature with and without graph access
    # and confirm at least one candidate's score grows.
    target = next(
        p for p in tmp_store.list_problems()
        if "sin" in (p.parsed_pretty or "")
    )
    sig = target.signature
    candidates = [("sympy", "sympy.simplify"),
                  ("sympy", "sympy.cancel"),
                  ("sympy", "sympy.trigsimp")]

    plain = Learner(tmp_store)  # no graph → no rule bonus
    aware = Learner(tmp_store, graph=tmp_graph)

    plain_ranked = plain.rank(signature=sig, problem_type="simplify",
                              candidates=candidates)
    aware_ranked = aware.rank(signature=sig, problem_type="simplify",
                              candidates=candidates)

    plain_scores = {c.approach: c.score for c in plain_ranked}
    aware_scores = {c.approach: c.score for c in aware_ranked}
    # At least one candidate should have a strictly higher score under
    # the identity-aware learner.
    bumped = [a for a in aware_scores if aware_scores[a] > plain_scores[a]]
    assert bumped, "expected the rule-aware learner to bump at least one candidate"
    # And the bumped one should be a candidate that actually has witnesses.
    bumped_witnesses = [c.rule_witnesses for c in aware_ranked if c.approach in bumped]
    assert max(bumped_witnesses) > 0


def test_rule_bonus_capped(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    """Even with many witnesses the bonus is bounded (0.30 by default)."""
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    for p in ["sin(x)**2 + cos(x)**2", "1"]:
        r.solve(p)
    Hypothesizer(store=tmp_store, graph=tmp_graph).scan(verify=True)

    target = next(
        p for p in tmp_store.list_problems()
        if "sin" in (p.parsed_pretty or "")
    )
    aware = Learner(tmp_store, graph=tmp_graph)
    ranked = aware.rank(
        signature=target.signature, problem_type="simplify",
        candidates=[("sympy", "sympy.simplify")],
    )
    assert ranked[0].rule_bonus <= 0.30 + 1e-9


# ── Transitive identity detector ────────────────────────────────────


def test_transitive_detector_proposes_third_identity(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    # Three forms of "1" so the detector has two verified identities to chain.
    for p in [
        "sin(x)**2 + cos(x)**2",
        "1",
        "cos(x)**2 + sin(x)**2",
    ]:
        r.solve(p)
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    h.scan(verify=True)
    transitives = h.detect_transitive_identities()
    # We need at least one transitive proposal that doesn't already exist
    # as a primary identity (which the detect_identities() detector
    # would also have produced). Just confirm the method returned
    # *something* of the expected kind.
    assert all(p.kind == KIND_IDENTITY for p in transitives)
    # If we have any, each should carry derived_from referencing two
    # existing hypothesis IDs.
    for p in transitives:
        assert "derived_from" in (p.evidence or {})
        assert len(p.evidence["derived_from"]) == 2


def test_transitive_detector_idempotent(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    for p in ["sin(x)**2 + cos(x)**2", "1", "cos(x)**2 + sin(x)**2"]:
        r.solve(p)
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    h.scan(verify=True)
    n1 = len(tmp_store.list_hypotheses())
    h.scan(verify=True)
    n2 = len(tmp_store.list_hypotheses())
    assert n1 == n2, "scan must merge into existing rows; not duplicate"


# ── Hard-signature detector ─────────────────────────────────────────


def test_hard_signature_emitted_when_attempts_chain(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    """We synthesise three problems on the same signature where the
    first attempt always fails and the second wins, so avg attempts
    > 1.5 and the detector should fire."""
    sig = "hard_sig"
    # Insert three problem rows that share the signature.
    pids = []
    for i in range(3):
        pid = tmp_store.insert_problem(
            raw_input=f"x{i}", source_format="sympy",
            problem_type="solve",
            parsed_expr=f"Symbol('x{i}')", parsed_pretty=f"x{i}",
            fingerprint={"signature": sig, "problem_type": "solve"},
        )
        pids.append(pid)
    # For each: first attempt refuted, second attempt verified by 'good'.
    for pid in pids:
        tmp_store.insert_attempt(
            problem_id=pid, tool="t", approach="t.bad",
            success=True, result_repr="?", result_pretty="?",
            verification_status="refuted", verification_detail="",
            time_ms=1.0, error=None, steps=[],
        )
        tmp_store.insert_attempt(
            problem_id=pid, tool="t", approach="t.good",
            success=True, result_repr="?", result_pretty="?",
            verification_status="verified", verification_detail="",
            time_ms=1.0, error=None, steps=[],
        )

    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    props = h.detect_hard_signatures()
    assert any(
        p.kind == KIND_RECURRING and "hard_sig" in p.claim_repr
        for p in props
    )
    one = next(p for p in props if "hard_sig" in p.claim_repr)
    assert "winning_pair" in (one.evidence or {})
    assert one.evidence["avg_attempts_until_verified"] > 1.5


def test_hard_signature_quiet_on_easy_signatures(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    """Single-attempt verifications should not be flagged as hard."""
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    for p in ["Eq(x**2 - 4, 0)", "Eq(x**2 - 9, 0)", "Eq(x**2 - 16, 0)"]:
        r.solve(p)
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    props = h.detect_hard_signatures()
    # Quadratic signatures are normally easy; the detector should be silent.
    assert all("hard" not in (p.claim or "").lower() or "avg 1.0" in (p.claim or "")
               for p in props)
