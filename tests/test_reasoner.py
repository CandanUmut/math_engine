"""End-to-end reasoner behavior, with an isolated SQLite store and graph."""
from __future__ import annotations

from pru_math.graph import RelationalGraph
from pru_math.reasoner import Reasoner
from pru_math.store import Store


def _make(tmp_store: Store, tmp_graph: RelationalGraph) -> Reasoner:
    return Reasoner(store=tmp_store, graph=tmp_graph)


def test_reasoner_solve_quadratic(tmp_store: Store, tmp_graph: RelationalGraph):
    r = _make(tmp_store, tmp_graph)
    out = r.solve("Eq(x**2 - 5*x + 6, 0)")
    assert out.ok is True
    assert out.verification_status == "verified"
    assert {"2", "3"}.issubset({s.strip() for s in out.answer_pretty.strip("[]").split(",")})
    kinds = [s.kind for s in out.trace]
    for k in ("parse", "fingerprint", "retrieval", "tool_call", "verify",
              "persist", "graph_update"):
        assert k in kinds


def test_reasoner_solve_indefinite_integral(tmp_store: Store, tmp_graph: RelationalGraph):
    out = _make(tmp_store, tmp_graph).solve("Integral(cos(x), x)")
    assert out.ok
    assert out.verification_status == "verified"
    assert out.problem_type == "integrate"


def test_reasoner_solve_definite_integral(tmp_store: Store, tmp_graph: RelationalGraph):
    out = _make(tmp_store, tmp_graph).solve("Integral(x**2, (x, 0, 1))")
    assert out.ok
    assert out.verification_status == "verified"
    assert "1/3" in out.answer_pretty.replace(" ", "")


def test_reasoner_parse_failure_is_graceful(tmp_store: Store, tmp_graph: RelationalGraph):
    out = _make(tmp_store, tmp_graph).solve("this is not a math expression at all !!!")
    assert out.ok is False
    assert out.error and "parse" in out.error.lower()
    assert out.problem_id is None
    # parse failure must not pollute the graph
    assert tmp_graph.node_count() == 0


def test_reasoner_persists_fingerprint_and_attempt(tmp_store: Store, tmp_graph: RelationalGraph):
    r = _make(tmp_store, tmp_graph)
    out = r.solve("sin(x)**2 + cos(x)**2")
    assert out.problem_id is not None
    rec = tmp_store.get_problem(out.problem_id)
    assert rec is not None
    assert rec.signature == out.fingerprint["signature"]
    attempts = tmp_store.list_attempts(out.problem_id)
    assert len(attempts) == 1
    assert attempts[0].tool == "sympy"
    assert attempts[0].verification_status == "verified"


def test_reasoner_first_problem_has_no_neighbours(
    tmp_store: Store, tmp_graph: RelationalGraph
):
    out = _make(tmp_store, tmp_graph).solve("Eq(x**2 - 4, 0)")
    assert out.similar == []
    retrieval = next(s for s in out.trace if s.kind == "retrieval")
    assert "No similar past problems" in retrieval.summary


def test_reasoner_second_similar_problem_finds_first(
    tmp_store: Store, tmp_graph: RelationalGraph
):
    r = _make(tmp_store, tmp_graph)
    first = r.solve("Eq(x**2 - 5*x + 6, 0)")
    second = r.solve("Eq(x**2 - 7*x + 12, 0)")
    assert second.similar, "expected the second quadratic-solve to find the first"
    ids = [s["problem"]["id"] for s in second.similar]
    assert first.problem_id in ids
    # graph should now have at least 2 problem nodes plus a tool, type, and signature
    stats = tmp_graph.stats()
    assert stats["nodes_by_kind"].get("problem", 0) >= 2
    assert stats["nodes_by_kind"].get("tool", 0) >= 1
    assert stats["nodes_by_kind"].get("problem_type", 0) >= 1


def test_reasoner_dissimilar_problem_yields_low_or_no_neighbours(
    tmp_store: Store, tmp_graph: RelationalGraph
):
    r = _make(tmp_store, tmp_graph)
    r.solve("Eq(x**2 - 5*x + 6, 0)")            # quadratic solve
    out = r.solve("Integral(cos(x), x)")        # indefinite integral
    # Either no similar problems, or strictly below the highest possible score.
    if out.similar:
        assert all(s["score"] < 0.95 for s in out.similar)
