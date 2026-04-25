"""Retrieval — joins the graph (fingerprints) with the store (solutions)."""
from __future__ import annotations

from pru_math.graph import RelationalGraph
from pru_math.reasoner import Reasoner
from pru_math.retrieval import (
    find_similar_problems,
    find_similar_problems_sparse,
    fingerprint_to_vector,
)
from pru_math.store import Store


def _populate(store: Store, graph: RelationalGraph, n_quadratics: int = 4) -> None:
    r = Reasoner(store=store, graph=graph)
    # Seed with quadratics + an integral so we have multiple types.
    quadratics = [
        "Eq(x**2 - 5*x + 6, 0)",
        "Eq(x**2 - 7*x + 12, 0)",
        "Eq(x**2 - 9*x + 20, 0)",
        "Eq(x**2 + x - 6, 0)",
    ][:n_quadratics]
    for q in quadratics:
        r.solve(q)
    r.solve("Integral(cos(x), x)")


def test_find_similar_basic(tmp_store: Store, tmp_graph: RelationalGraph):
    _populate(tmp_store, tmp_graph)
    # Use the fingerprint of a brand-new quadratic that wasn't solved.
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(x**2 - 11*x + 30, 0)")
    sims = find_similar_problems(out.fingerprint, graph=tmp_graph, store=tmp_store, k=3,
                                 exclude_problem_id=out.problem_id)
    assert sims
    assert len(sims) <= 3
    # All retrieved neighbours come back with their solutions.
    for s in sims:
        assert s.problem.id != out.problem_id
        assert s.all_attempts, "expected each neighbour to have at least one attempt"
    # The verifier-passed quadratics should be ranked above the integral.
    types = [s.problem.problem_type for s in sims]
    assert types[0] == "solve"


def test_find_similar_excludes_self(tmp_store: Store, tmp_graph: RelationalGraph):
    _populate(tmp_store, tmp_graph)
    # Re-fetch one of the seeded problems and ask for its similars.
    seeded = tmp_store.list_problems()[0]
    sims = find_similar_problems(
        seeded.fingerprint, graph=tmp_graph, store=tmp_store, k=5,
        exclude_problem_id=seeded.id,
    )
    assert seeded.id not in {s.problem.id for s in sims}


def test_best_attempt_prefers_verified(tmp_store: Store, tmp_graph: RelationalGraph):
    _populate(tmp_store, tmp_graph, n_quadratics=2)
    seeded = tmp_store.list_problems()[0]
    sims = find_similar_problems(
        seeded.fingerprint, graph=tmp_graph, store=tmp_store, k=2,
        exclude_problem_id=seeded.id,
    )
    for s in sims:
        if s.best_attempt and any(a.verification_status == "verified" for a in s.all_attempts):
            assert s.best_attempt.verification_status == "verified"


def test_fingerprint_to_vector_is_stable_length():
    fp = {
        "problem_type": "solve",
        "operator_counts": {"Add": 1, "Mul": 1, "Pow": 1, "Eq": 1},
        "function_flags": {"trig": False, "log": False, "exp": False,
                            "abs": False, "piecewise": False, "factorial": False,
                            "gamma": False, "inv_trig": False, "hyp": False},
        "variable_count": 1,
        "node_count": 13,
        "polynomial_degree": 2,
    }
    v1 = fingerprint_to_vector(fp)
    v2 = fingerprint_to_vector(fp)
    assert v1.shape == v2.shape
    assert (v1 == v2).all()


def test_sparse_path_falls_back_below_threshold(tmp_store: Store, tmp_graph: RelationalGraph):
    """The sparse path falls back to the simple path under 200 nodes; the
    contract is identical for the caller, so we just confirm we still get
    similar results from a small graph without errors."""
    _populate(tmp_store, tmp_graph)
    r = Reasoner(store=tmp_store, graph=tmp_graph)
    out = r.solve("Eq(x**2 - 13*x + 42, 0)")
    sims = find_similar_problems_sparse(
        out.fingerprint, graph=tmp_graph, store=tmp_store, k=3,
        exclude_problem_id=out.problem_id,
    )
    assert sims
    assert sims[0].problem.problem_type == "solve"
