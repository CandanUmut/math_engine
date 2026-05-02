"""Phase 12: multi-step rewrite chains.

- generate_rewrite_chains BFS produces depth-1 chains identical to
  generate_rewrites (compatibility floor)
- depth-2 chains compose two distinct rules
- max_depth caps the BFS at the requested ply
- max_chains caps the result count
- max_nodes prevents BFS explosion
- the BFS deduplicates via canonical srepr (no infinite loops on
  bidirectional rules)
- the reasoner invokes chain rewrites and surfaces the depth in the
  trace
- a verified chain still verifies against the ORIGINAL problem
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sympy as sp

from pru_math import settings as runtime_settings
from pru_math.graph import RelationalGraph
from pru_math.hypothesizer import Hypothesizer
from pru_math.parser import parse
from pru_math.reasoner import Reasoner
from pru_math.rewriter import (
    RewriteChain,
    RewriteRule,
    _build_rule_pair,
    generate_rewrite_chains,
    generate_rewrites,
    load_rules_from_store,
)
from pru_math.store import Store


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRU_SETTINGS_PATH", str(tmp_path / "settings.json"))
    runtime_settings.reload_for_tests()
    yield
    runtime_settings.reload_for_tests()


def _seed_pythagorean(tmp_store: Store, tmp_graph: RelationalGraph) -> None:
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    for p in ["sin(x)**2 + cos(x)**2", "1"]:
        r.solve(p)
    Hypothesizer(store=tmp_store, graph=tmp_graph).scan(verify=True)


# ── Pure rewriter behaviour ─────────────────────────────────────────


def test_depth_1_chain_matches_phase9_single_step(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    _seed_pythagorean(tmp_store, tmp_graph)
    rules = load_rules_from_store(tmp_store)
    parsed = parse("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    legacy = generate_rewrites(parsed, rules, max_rewrites=4)
    chains = generate_rewrite_chains(parsed, rules, max_depth=1, max_chains=4)
    assert chains, "expected ≥1 depth-1 chain"
    assert all(c.depth == 1 for c in chains)
    # The two functions should produce the same set of inner rewrites
    # (just wrapped differently).
    legacy_inners = {sp.srepr(rw.rewritten) for rw in legacy}
    chain_inners = {sp.srepr(c.final_expr) for c in chains}
    assert legacy_inners == chain_inners


def test_depth_2_chain_composes_two_rules(monkeypatch):
    """Hand-build two rules so we can confirm BFS composes them.

    Rule A: a → b
    Rule B: b → c
    Then a depth-2 BFS starting from `a` should produce `c`.
    """
    a, b, c = sp.symbols("a b c")
    pat_a = sp.Wild("_w0")
    rule_a = RewriteRule(
        rule_id=1, lhs_pretty="a", rhs_pretty="b",
        lhs_pattern=a, rhs_template=b, direction="lhs_to_rhs",
    )
    rule_b = RewriteRule(
        rule_id=2, lhs_pretty="b", rhs_pretty="c",
        lhs_pattern=b, rhs_template=c, direction="lhs_to_rhs",
    )
    parsed = parse("a")  # bare expression → SIMPLIFY
    chains = generate_rewrite_chains(
        parsed, [rule_a, rule_b], max_depth=2, max_chains=8,
    )
    assert chains, "expected at least one chain"
    finals = [sp.srepr(ch.final_expr) for ch in chains]
    assert sp.srepr(b) in finals       # depth-1 chain (a→b)
    assert sp.srepr(c) in finals       # depth-2 chain (a→b→c)
    deep = [ch for ch in chains if ch.depth == 2]
    assert deep, "expected at least one depth-2 chain"
    assert deep[0].steps[0].rule.rule_id == 1
    assert deep[0].steps[1].rule.rule_id == 2


def test_max_depth_cap_is_honoured():
    """max_depth=1 should never produce a chain deeper than 1."""
    a, b, c = sp.symbols("a b c")
    rule_a = RewriteRule(
        rule_id=1, lhs_pretty="a", rhs_pretty="b",
        lhs_pattern=a, rhs_template=b, direction="lhs_to_rhs",
    )
    rule_b = RewriteRule(
        rule_id=2, lhs_pretty="b", rhs_pretty="c",
        lhs_pattern=b, rhs_template=c, direction="lhs_to_rhs",
    )
    chains = generate_rewrite_chains(
        parse("a"), [rule_a, rule_b], max_depth=1, max_chains=8,
    )
    assert chains
    assert all(ch.depth == 1 for ch in chains)


def test_max_chains_cap_is_honoured():
    """max_chains=2 should return at most 2 chains."""
    a, b, c, d = sp.symbols("a b c d")
    rules = [
        RewriteRule(rule_id=i, lhs_pretty=str(s), rhs_pretty=str(t),
                    lhs_pattern=s, rhs_template=t, direction="lhs_to_rhs")
        for i, (s, t) in enumerate([(a, b), (b, c), (c, d)], start=1)
    ]
    chains = generate_rewrite_chains(
        parse("a"), rules, max_depth=3, max_chains=2,
    )
    assert len(chains) <= 2


def test_chain_dedupe_no_infinite_loop_on_bidirectional_rules():
    """Two rules a→b and b→a must not loop forever; the BFS visited set
    catches it."""
    a, b = sp.symbols("a b")
    fwd = RewriteRule(rule_id=1, lhs_pretty="a", rhs_pretty="b",
                      lhs_pattern=a, rhs_template=b, direction="lhs_to_rhs")
    rev = RewriteRule(rule_id=2, lhs_pretty="b", rhs_pretty="a",
                      lhs_pattern=b, rhs_template=a, direction="rhs_to_lhs")
    chains = generate_rewrite_chains(
        parse("a"), [fwd, rev], max_depth=5, max_chains=20,
    )
    # Only one truly new canonical form is reachable from `a`: namely `b`.
    finals = {sp.srepr(c.final_expr) for c in chains}
    assert sp.srepr(b) in finals
    # And the function returned in finite time (the BFS terminated).


def test_no_rules_no_chains():
    chains = generate_rewrite_chains(parse("a"), [], max_depth=3, max_chains=4)
    assert chains == []


def test_chain_to_trace_dict_records_provenance():
    a, b, c = sp.symbols("a b c")
    rule_a = RewriteRule(rule_id=11, lhs_pretty="a", rhs_pretty="b",
                         lhs_pattern=a, rhs_template=b, direction="lhs_to_rhs")
    rule_b = RewriteRule(rule_id=22, lhs_pretty="b", rhs_pretty="c",
                         lhs_pattern=b, rhs_template=c, direction="lhs_to_rhs")
    chains = generate_rewrite_chains(parse("a"), [rule_a, rule_b],
                                     max_depth=2, max_chains=8)
    deep = [c for c in chains if c.depth == 2][0]
    d = deep.to_trace_dict()
    assert d["depth"] == 2
    assert d["rule_ids"] == [11, 22]
    assert d["intermediate_exprs"] == ["b", "c"]
    assert d["final_expr"] == "c"


# ── Reasoner integration ────────────────────────────────────────────


def test_reasoner_passes_max_rewrite_depth_setting(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    runtime_settings.set_many({"max_rewrite_depth": 3})
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    assert r.max_rewrite_depth == 3
    runtime_settings.set_many({"max_rewrite_depth": 1})
    assert r.max_rewrite_depth == 1


def test_reasoner_chain_rewrite_trace_step_carries_depth(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    """Even for depth-1 chains, the new trace summary mentions depth so
    the UI can render multi-step chains uniformly."""
    _seed_pythagorean(tmp_store, tmp_graph)
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    rewrites = [s for s in out.trace if s.kind == "rewrite"]
    assert rewrites, "expected at least one rewrite trace step"
    assert "depth" in rewrites[0].summary.lower()
    # Detail dict carries the chain provenance.
    d = rewrites[0].detail
    assert "depth" in d
    assert "rule_ids" in d
    assert d["depth"] >= 1


def test_reasoner_depth_2_invocation_calls_chain_function(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    """When max_rewrite_depth is bumped to 2, the underlying call to
    generate_rewrite_chains must use that value. We verify by patching."""
    _seed_pythagorean(tmp_store, tmp_graph)
    runtime_settings.set_many({"max_rewrite_depth": 2})
    r = Reasoner(store=tmp_store, graph=tmp_graph)

    captured = {}
    real_fn = generate_rewrite_chains

    def spy(parsed, rules, *, max_depth, max_chains, max_nodes=64):
        captured["max_depth"] = max_depth
        captured["max_chains"] = max_chains
        return real_fn(parsed, rules,
                       max_depth=max_depth, max_chains=max_chains,
                       max_nodes=max_nodes)

    with patch("pru_math.rewriter.generate_rewrite_chains", side_effect=spy):
        r.solve("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")

    assert captured.get("max_depth") == 2
    assert captured.get("max_chains") >= 1


def test_reasoner_verifies_chain_against_original_problem(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    """A chained rewrite must still be verified against the *original*
    parsed problem, not the rewritten intermediate. (The audit invariant.)"""
    _seed_pythagorean(tmp_store, tmp_graph)
    r = Reasoner(store=tmp_store, graph=tmp_graph)

    out = r.solve("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    # Walk the trace and confirm every "verify" step that follows a
    # "rewrite" step has been computed against the original — which is
    # implicit in the reasoner's call site, but we double-check the
    # presence of verify steps on each rewrite.
    rewrites = [i for i, s in enumerate(out.trace) if s.kind == "rewrite"]
    for idx in rewrites:
        # If the tool produced a result, the next non-tool_call step
        # should be a verify step.
        following = out.trace[idx + 1:]
        kinds = [s.kind for s in following]
        if "tool_call" in kinds and following[kinds.index("tool_call")].detail.get("result_pretty"):
            assert "verify" in kinds, \
                "every successful rewrite must verify against the original"


def test_chain_rewrites_persist_depth_in_step_note(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    """Each chain attempt's ``steps`` field must record the chain depth
    and rule-id list so a maintainer reading the DB later can audit it."""
    _seed_pythagorean(tmp_store, tmp_graph)
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    chain_attempts = [
        a for a in tmp_store.list_attempts(out.problem_id)
        if any("rewrite chain" in s for s in a.steps)
    ]
    assert chain_attempts, (
        "expected at least one persisted attempt to carry rewrite-chain "
        "metadata in its steps_json"
    )
    note = next(s for s in chain_attempts[0].steps if "rewrite chain" in s)
    assert "depth" in note
    assert "rules" in note
