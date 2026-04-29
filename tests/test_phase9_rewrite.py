"""Phase 9 rewrite-based search:

- Wild-based rule construction with directionality guard
- direct + sub-Add pattern application
- load_rules_from_store reads verified-identity hypotheses
- generate_rewrites caps at max_rewrites and skips no-ops
- the reasoner only invokes rewrites AFTER primary attempts fail to
  verify, and a rewritten attempt is persisted as a normal attempt row
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sympy as sp

from pru_math import settings as runtime_settings
from pru_math.graph import RelationalGraph
from pru_math.hypothesizer import Hypothesizer
from pru_math.parser import parse
from pru_math.reasoner import Reasoner
from pru_math.rewriter import (
    RewriteRule,
    _build_rule_pair,
    _direction_valid,
    generate_rewrites,
    load_rules_from_store,
)
from pru_math.store import Store


# ── Direction guard ────────────────────────────────────────────────


def test_direction_valid_drops_unbound_target_symbols():
    x = sp.Symbol("x")
    # LHS uses x, RHS is just 1 → forward valid, reverse invalid.
    assert _direction_valid(sp.sin(x)**2 + sp.cos(x)**2, sp.Integer(1))
    assert not _direction_valid(sp.Integer(1), sp.sin(x)**2 + sp.cos(x)**2)


def test_build_rule_pair_only_keeps_well_formed_directions():
    rules = _build_rule_pair(1, "sin(x)**2 + cos(x)**2", "1")
    assert len(rules) == 1
    assert rules[0].direction == "lhs_to_rhs"


def test_build_rule_pair_keeps_both_when_symmetric():
    # (a+b)**2 ≡ a**2 + 2*a*b + b**2 — both sides share {a, b}.
    rules = _build_rule_pair(7, "(a+b)**2", "a**2 + 2*a*b + b**2")
    dirs = {r.direction for r in rules}
    assert dirs == {"lhs_to_rhs", "rhs_to_lhs"}


# ── Pattern application ────────────────────────────────────────────


def test_apply_direct_match():
    rule = _build_rule_pair(1, "sin(x)**2 + cos(x)**2", "1")[0]
    target = sp.sin(sp.Symbol("y"))**2 + sp.cos(sp.Symbol("y"))**2
    assert rule.apply(target) == sp.Integer(1)


def test_apply_sub_add_subset_match():
    # Bigger Add containing the pattern as a subset of terms.
    rule = _build_rule_pair(1, "sin(x)**2 + cos(x)**2", "1")[0]
    z = sp.Symbol("z")
    target = sp.sin(z)**2 + sp.cos(z)**2 - 1
    rewritten = rule.apply(target)
    assert rewritten is not None
    # Math: 1 + (-1) = 0 (structural form may vary).
    assert sp.simplify(rewritten) == 0


def test_apply_returns_none_when_no_match():
    rule = _build_rule_pair(1, "sin(x)**2 + cos(x)**2", "1")[0]
    target = sp.sympify("z + 1")
    assert rule.apply(target) is None


# ── Store integration ──────────────────────────────────────────────


def _seed_pythagorean(tmp_store: Store, tmp_graph: RelationalGraph) -> None:
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    for p in ["sin(x)**2 + cos(x)**2", "1"]:
        r.solve(p)
    Hypothesizer(store=tmp_store, graph=tmp_graph).scan(verify=True)


def test_load_rules_from_store_returns_verified_only(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    _seed_pythagorean(tmp_store, tmp_graph)
    rules = load_rules_from_store(tmp_store)
    assert rules
    assert all(isinstance(r, RewriteRule) for r in rules)
    # Exactly one well-formed direction survives the symbol-binding check.
    # (The detector stores identity sides alphabetically, so the
    # hypothesis is "1 ≡ sin(x)**2 + cos(x)**2"; only the direction
    # whose target's symbols are a subset of the source's is kept.)
    assert len(rules) == 1
    rule = rules[0]
    # The rule's own LHS must contain the trig sum (the side with vars).
    assert "sin" in rule.lhs_pretty
    assert rule.rhs_pretty == "1"


def test_generate_rewrites_caps_at_max_rewrites(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    _seed_pythagorean(tmp_store, tmp_graph)
    rules = load_rules_from_store(tmp_store)
    parsed = parse("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    rws = generate_rewrites(parsed, rules, max_rewrites=1)
    assert len(rws) <= 1


def test_generate_rewrites_skips_no_op(tmp_store, tmp_graph):
    _seed_pythagorean(tmp_store, tmp_graph)
    rules = load_rules_from_store(tmp_store)
    # Problem with no trig — every rule is a no-op.
    parsed = parse("Eq(x**2 - 4, 0)")
    rws = generate_rewrites(parsed, rules, max_rewrites=4)
    assert rws == []


# ── Reasoner integration ───────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRU_SETTINGS_PATH", str(tmp_path / "settings.json"))
    runtime_settings.reload_for_tests()
    yield
    runtime_settings.reload_for_tests()


def test_reasoner_does_not_rewrite_when_primary_verifies(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    _seed_pythagorean(tmp_store, tmp_graph)
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    # An easy quadratic — primary attempts verify; no need to rewrite.
    out = r.solve("Eq(x**2 - 5*x + 6, 0)")
    assert out.verification_status == "verified"
    rewrites = [s for s in out.trace if s.kind == "rewrite"]
    assert rewrites == []


def test_reasoner_invokes_rewrite_after_primary_fails(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    _seed_pythagorean(tmp_store, tmp_graph)
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    # A problem whose primary attempts return inconclusive results
    # (because the equation is mathematically trivial after the
    # identity collapses it). The rewrite phase should fire.
    out = r.solve("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    rewrites = [s for s in out.trace if s.kind == "rewrite"]
    assert rewrites, "expected at least one rewrite trace step"
    assert "rule #" in rewrites[0].summary


def test_rewriting_disabled_setting_skips_phase(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    _seed_pythagorean(tmp_store, tmp_graph)
    runtime_settings.set_many({"enable_rewriting": False})
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    rewrites = [s for s in out.trace if s.kind == "rewrite"]
    assert rewrites == []


def test_rewrite_attempts_are_persisted_with_metadata(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    _seed_pythagorean(tmp_store, tmp_graph)
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    attempts = tmp_store.list_attempts(out.problem_id)
    rewrite_attempts = [a for a in attempts
                        if any("rewrite via rule" in s for s in a.steps)]
    assert rewrite_attempts, (
        "expected at least one persisted attempt to carry rewrite metadata"
    )


def test_max_rewrite_attempts_zero_disables_phase(
    tmp_store: Store, tmp_graph: RelationalGraph,
):
    _seed_pythagorean(tmp_store, tmp_graph)
    runtime_settings.set_many({"max_rewrite_attempts": 0})
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(sin(z)**2 + cos(z)**2 - 1, 0)")
    rewrites = [s for s in out.trace if s.kind == "rewrite"]
    assert rewrites == []
