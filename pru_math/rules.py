"""Phase 7: identity-aware ranking — graph traversal helpers.

Every verified hypothesis materialises a ``rule`` node connected via
``uses_rule`` edges back to the problems that supported it. This module
exposes the small piece of bookkeeping the learner needs to surface
those rules at ranking time:

    > "This (tool, approach) has previously witnessed a verified rule on
       a problem that's structurally close to the one we're about to
       solve."

The witness count then becomes a small additive bias on the UCB score.
The signal is intentionally weak (a few percent of the value range) —
verification rate is still the dominant term — but it lets the
hypothesizer's discoveries close the loop and *influence* future solves
instead of sitting passively in the graph.
"""
from __future__ import annotations

from typing import Iterable

from .graph import (
    EDGE_HAS_SIG,
    EDGE_SOLVED_BY,
    EDGE_USES_RULE,
    NODE_PROBLEM,
    NODE_RULE,
    RelationalGraph,
    problem_node,
    signature_node,
)


def _problem_nodes_with_signature(graph: RelationalGraph, signature: str
                                  ) -> set[str]:
    """All problem nodes connected to the signature cluster ``signature``."""
    if not signature:
        return set()
    sig_id = signature_node(signature)
    g = graph.graph
    if sig_id not in g:
        return set()
    out: set[str] = set()
    for u, v, data in g.in_edges(sig_id, data=True):
        if data.get("kind") == EDGE_HAS_SIG and g.nodes.get(u, {}).get("kind") == NODE_PROBLEM:
            out.add(u)
    return out


def _rule_nodes_via_problems(graph: RelationalGraph, problem_nodes: Iterable[str]
                             ) -> set[str]:
    """All ``rule`` nodes connected to any of ``problem_nodes`` via
    ``uses_rule`` edges (in either direction)."""
    g = graph.graph
    rules: set[str] = set()
    for p in problem_nodes:
        if p not in g:
            continue
        for _u, v, data in g.out_edges(p, data=True):
            if data.get("kind") == EDGE_USES_RULE and g.nodes.get(v, {}).get("kind") == NODE_RULE:
                rules.add(v)
        for u, _v, data in g.in_edges(p, data=True):
            if data.get("kind") == EDGE_USES_RULE and g.nodes.get(u, {}).get("kind") == NODE_RULE:
                rules.add(u)
    return rules


def _supporting_problem_nodes(graph: RelationalGraph, rule_id: str) -> set[str]:
    """Problem nodes connected to ``rule_id`` via ``uses_rule``."""
    g = graph.graph
    if rule_id not in g:
        return set()
    out: set[str] = set()
    for u, _v, data in g.in_edges(rule_id, data=True):
        if data.get("kind") == EDGE_USES_RULE and g.nodes.get(u, {}).get("kind") == NODE_PROBLEM:
            out.add(u)
    for _u, v, data in g.out_edges(rule_id, data=True):
        if data.get("kind") == EDGE_USES_RULE and g.nodes.get(v, {}).get("kind") == NODE_PROBLEM:
            out.add(v)
    return out


def witness_counts(graph: RelationalGraph, signature: str) -> dict[tuple[str, str], int]:
    """Count, per ``(tool, approach)`` pair, how many verified-rule witnesses
    exist on problems that share the given signature.

    A "witness" is a triple ``(rule, problem, solved_by_edge)`` where:

    1. the problem participates in the signature class,
    2. the problem is connected to a verified rule via ``uses_rule``,
    3. the problem was solved by the listed approach with ``verified=True``.

    Returns an empty dict when the signature has no rule-bearing
    neighbours, so the learner can call this unconditionally.
    """
    if not signature:
        return {}
    g = graph.graph
    seed_problems = _problem_nodes_with_signature(graph, signature)
    if not seed_problems:
        return {}
    rules = _rule_nodes_via_problems(graph, seed_problems)
    if not rules:
        return {}
    # Build the witness count by walking each rule's supporting problems
    # and looking at their ``solved_by`` edges.
    counts: dict[tuple[str, str], int] = {}
    seen_pairs: set[tuple[str, str, str]] = set()
    for rule in rules:
        for p in _supporting_problem_nodes(graph, rule):
            for _u, t, data in g.out_edges(p, data=True):
                if data.get("kind") != EDGE_SOLVED_BY:
                    continue
                if not data.get("verified"):
                    continue
                tool = g.nodes.get(t, {}).get("name") or t.removeprefix("t:")
                approach = data.get("approach")
                if not approach:
                    continue
                key = (tool, approach)
                # De-dupe at the (rule, problem, approach) level so two
                # attempts on the same problem don't double-count.
                triple = (rule, p, approach)
                if triple in seen_pairs:
                    continue
                seen_pairs.add(triple)
                counts[key] = counts.get(key, 0) + 1
    return counts
