"""Phase 5 hypothesizer: detectors, verification, persistence, graph
integration."""
from __future__ import annotations

from pru_math.graph import RelationalGraph, NODE_RULE
from pru_math.hypothesizer import (
    Hypothesizer,
    KIND_IDENTITY,
    KIND_RECURRING,
    KIND_SPECIALIZATION,
    STATUS_VERIFIED,
    STATUS_REFUTED,
    STATUS_PROPOSED,
)
from pru_math.reasoner import Reasoner
from pru_math.store import Store


def _seed_simplify_problems(reasoner: Reasoner, exprs: list[str]) -> None:
    for e in exprs:
        reasoner.solve(e)


def test_detect_identity_pythagorean(tmp_store: Store, tmp_graph: RelationalGraph):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    _seed_simplify_problems(r, [
        "sin(x)**2 + cos(x)**2",   # canonicalises to 1
        "1",                        # already 1
    ])
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    proposals = h.detect_identities()
    # We expect at least one identity proposal pairing the two inputs.
    assert any(p.kind == KIND_IDENTITY for p in proposals)
    pair = [p for p in proposals if p.kind == KIND_IDENTITY][0]
    pretty = pair.evidence["lhs_pretty"], pair.evidence["rhs_pretty"]
    assert "sin(x)**2 + cos(x)**2" in pretty
    assert "1" in pretty


def test_verify_identity_via_sympy(tmp_store: Store, tmp_graph: RelationalGraph):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    _seed_simplify_problems(r, ["sin(x)**2 + cos(x)**2", "1"])
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    results = h.scan(verify=True)
    identity = next(p for p in results if p.kind == KIND_IDENTITY)
    assert identity.status == STATUS_VERIFIED
    assert identity.method == "sympy"


def test_verify_identity_refutes_wrong_pair(tmp_store: Store, tmp_graph: RelationalGraph):
    """If two parsed inputs *don't* simplify to the same thing, the
    detector won't propose them. But we can manually craft a refutable
    hypothesis and feed it through the verifier to confirm refutation."""
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    from pru_math.hypothesizer import Hypothesis
    bad = Hypothesis(
        kind=KIND_IDENTITY,
        claim="sin(x)  ≡  2*sin(x)",
        claim_repr="identity:Sin(x)<=>2*Sin(x)",
        evidence={"lhs_pretty": "sin(x)", "rhs_pretty": "2*sin(x)",
                  "support_problem_ids": []},
    )
    persisted = h._persist(bad)         # noqa: SLF001
    h.verify(persisted)
    refreshed = tmp_store.get_hypothesis(persisted.persisted_id)
    assert refreshed.status == STATUS_REFUTED


def test_specialization_detected_when_one_tool_dominates(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    # Manually populate tool_outcomes via the store so we don't need to
    # solve a hundred problems to drive the threshold.
    sigs = ["sigA", "sigB", "sigC"]
    # Insert minimal problem rows for each signature so the JOIN in
    # detect_specializations picks them up.
    for i, sig in enumerate(sigs, start=1):
        tmp_store.insert_problem(
            raw_input=str(i), source_format="sympy",
            problem_type="solve",
            parsed_expr=f"Symbol('x{i}')", parsed_pretty=f"x{i}",
            fingerprint={"signature": sig, "problem_type": "solve"},
        )
    # Tool A: 6/6 verified across sigs; Tool B: 1/3.
    for sig in sigs:
        for _ in range(2):
            tmp_store.upsert_tool_outcome(
                signature=sig, tool="alpha", approach="alpha.solve",
                success=True, verified=True, time_ms=2.0,
            )
        tmp_store.upsert_tool_outcome(
            signature=sig, tool="beta", approach="beta.solve",
            success=True, verified=False, time_ms=3.0,
        )
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    props = h.detect_specializations()
    assert any(p.kind == KIND_SPECIALIZATION
               and "alpha" in p.claim for p in props)


def test_recurring_approach_detected(tmp_store: Store, tmp_graph: RelationalGraph):
    sig = "shared_sig"
    tmp_store.insert_problem(
        raw_input="x", source_format="sympy", problem_type="solve",
        parsed_expr="Symbol('x')", parsed_pretty="x",
        fingerprint={"signature": sig, "problem_type": "solve"},
    )
    # Two approaches: 'good' verifies 5/5; 'bad' verifies 0/4.
    for _ in range(5):
        tmp_store.upsert_tool_outcome(
            signature=sig, tool="t", approach="t.good",
            success=True, verified=True, time_ms=1.0,
        )
    for _ in range(4):
        tmp_store.upsert_tool_outcome(
            signature=sig, tool="t", approach="t.bad",
            success=True, verified=False, time_ms=1.0,
        )
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    props = h.detect_recurring_approaches()
    assert any(p.kind == KIND_RECURRING and "t.good" in p.claim_repr
               for p in props)


def test_verified_identity_creates_rule_node(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    _seed_simplify_problems(r, ["sin(x)**2 + cos(x)**2", "1"])
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    h.scan(verify=True)
    rule_nodes = [
        n for n, d in tmp_graph.graph.nodes(data=True) if d.get("kind") == NODE_RULE
    ]
    assert rule_nodes, "expected at least one rule node from a verified identity"


def test_scan_is_idempotent(tmp_store: Store, tmp_graph: RelationalGraph):
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    _seed_simplify_problems(r, ["sin(x)**2 + cos(x)**2", "1"])
    h = Hypothesizer(store=tmp_store, graph=tmp_graph)
    h.scan()
    n1 = len(tmp_store.list_hypotheses())
    h.scan()
    n2 = len(tmp_store.list_hypotheses())
    assert n1 == n2, "scan should merge into existing hypotheses, not duplicate"
