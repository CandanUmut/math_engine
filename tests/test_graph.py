"""RelationalGraph behavior — node/edge add, similarity edges, persistence,
and cytoscape serialisation."""
from __future__ import annotations

from pathlib import Path

from pru_math.graph import (
    RelationalGraph,
    EDGE_HAS_SIG,
    EDGE_HAS_TYPE,
    EDGE_SIMILAR,
    EDGE_SOLVED_BY,
    NODE_PROBLEM,
    NODE_PROBLEM_TYPE,
    NODE_SIGNATURE,
    NODE_TOOL,
)


def _fp(sig: str = "abc", problem_type: str = "solve",
        ops=("Add", "Mul", "Pow", "Eq"), node_count: int = 10,
        var_count: int = 1, deg: int = 2) -> dict:
    return {
        "signature": sig,
        "problem_type": problem_type,
        "operator_counts": {k: 1 for k in ops},
        "function_flags": {k: False for k in
                            ("trig", "inv_trig", "hyp", "log", "exp",
                             "abs", "piecewise", "factorial", "gamma")},
        "variable_count": var_count,
        "node_count": node_count,
        "polynomial_degree": deg,
        "variables": ["x"][:var_count],
        "max_depth": 3,
    }


def test_add_problem_creates_typed_nodes_and_edges(tmp_graph: RelationalGraph):
    fp = _fp()
    tmp_graph.add_problem(
        problem_id=1, problem_type="solve", signature="abc",
        fingerprint=fp, raw_input="x^2 = 0", parsed_pretty="x**2 = 0",
    )
    stats = tmp_graph.stats()
    assert stats["nodes_by_kind"][NODE_PROBLEM] == 1
    assert stats["nodes_by_kind"][NODE_PROBLEM_TYPE] == 1
    assert stats["nodes_by_kind"][NODE_SIGNATURE] == 1
    edges = stats["edges_by_kind"]
    assert edges[EDGE_HAS_TYPE] == 1
    assert edges[EDGE_HAS_SIG] == 1


def test_link_solved_by_creates_tool_node_and_edge(tmp_graph: RelationalGraph):
    tmp_graph.add_problem(
        problem_id=1, problem_type="solve", signature="abc",
        fingerprint=_fp(), raw_input="x", parsed_pretty="x",
    )
    tmp_graph.link_solved_by(
        problem_id=1, tool="sympy", approach="sympy.solve",
        success=True, verified=True, time_ms=4.2,
    )
    stats = tmp_graph.stats()
    assert stats["nodes_by_kind"][NODE_TOOL] == 1
    assert stats["edges_by_kind"][EDGE_SOLVED_BY] == 1


def test_similarity_edges_respect_threshold(tmp_graph: RelationalGraph):
    fp1 = _fp(sig="s1")
    fp2 = _fp(sig="s2")
    tmp_graph.add_problem(problem_id=1, problem_type="solve", signature="s1",
                          fingerprint=fp1, raw_input="a", parsed_pretty="a")
    tmp_graph.add_problem(problem_id=2, problem_type="solve", signature="s2",
                          fingerprint=fp2, raw_input="b", parsed_pretty="b")
    # Score above threshold => stored
    added = tmp_graph.add_similarity_edges(
        new_problem_id=2, candidates=[(1, 0.9)],
    )
    assert added == 1
    # Score below threshold => not stored
    tmp_graph.add_problem(problem_id=3, problem_type="solve", signature="s3",
                          fingerprint=_fp(sig="s3"), raw_input="c", parsed_pretty="c")
    added2 = tmp_graph.add_similarity_edges(
        new_problem_id=3, candidates=[(1, 0.10), (2, 0.10)],
    )
    assert added2 == 0
    # Symmetric storage: both directions exist for the kept edge
    pairs = [(u, v) for u, v, d in tmp_graph.graph.edges(data=True)
             if d.get("kind") == EDGE_SIMILAR]
    assert ("p:1", "p:2") in pairs
    assert ("p:2", "p:1") in pairs


def test_neighbours_of_problem_sorted_by_score(tmp_graph: RelationalGraph):
    for pid in (1, 2, 3):
        tmp_graph.add_problem(problem_id=pid, problem_type="solve",
                              signature=f"s{pid}", fingerprint=_fp(sig=f"s{pid}"),
                              raw_input=str(pid), parsed_pretty=str(pid))
    tmp_graph.add_similarity_edges(
        new_problem_id=1, candidates=[(2, 0.7), (3, 0.95)],
    )
    neigh = tmp_graph.neighbours_of_problem(1)
    assert [n.problem_id for n in neigh] == [3, 2]


def test_find_similar_to_fingerprint_boosts_signature_match(tmp_graph: RelationalGraph):
    # Same signature should score above 0.99 even with otherwise low overlap.
    tmp_graph.add_problem(problem_id=1, problem_type="solve",
                          signature="match", fingerprint=_fp(sig="match"),
                          raw_input="a", parsed_pretty="a")
    tmp_graph.add_problem(problem_id=2, problem_type="solve",
                          signature="other", fingerprint=_fp(sig="other"),
                          raw_input="b", parsed_pretty="b")
    target_fp = _fp(sig="match")
    out = tmp_graph.find_similar_to_fingerprint(target_fp, top_k=2)
    assert out[0].problem_id == 1
    assert out[0].score >= 0.999


def test_persistence_round_trip(tmp_path: Path):
    p = tmp_path / "g.gpickle"
    g1 = RelationalGraph(path=p, autosave=True)
    g1.add_problem(problem_id=1, problem_type="solve", signature="s",
                   fingerprint=_fp(sig="s"), raw_input="x", parsed_pretty="x")
    g1.commit()
    assert p.is_file()
    g2 = RelationalGraph(path=p, autosave=False)
    assert g2.node_count() >= 3
    assert g2.get_problem_data(1) is not None


def test_corrupt_pickle_starts_fresh(tmp_path: Path):
    p = tmp_path / "g.gpickle"
    p.write_bytes(b"not a real pickle")
    g = RelationalGraph(path=p, autosave=False)
    # We get a fresh graph and the corrupt file is moved aside.
    assert g.node_count() == 0
    assert (tmp_path / "g.gpickle.corrupt").is_file()


def test_to_cytoscape_returns_typed_nodes(tmp_graph: RelationalGraph):
    tmp_graph.add_problem(problem_id=1, problem_type="solve", signature="s",
                          fingerprint=_fp(sig="s"), raw_input="x", parsed_pretty="x")
    tmp_graph.link_solved_by(problem_id=1, tool="sympy",
                             approach="sympy.solve", success=True,
                             verified=True, time_ms=1.0)
    cy = tmp_graph.to_cytoscape()
    kinds = {n["data"]["kind"] for n in cy["nodes"]}
    assert {"problem", "tool", "problem_type", "signature"} <= kinds
    # All edges have valid endpoints in the node list
    node_ids = {n["data"]["id"] for n in cy["nodes"]}
    for e in cy["edges"]:
        assert e["data"]["source"] in node_ids
        assert e["data"]["target"] in node_ids


def test_subgraph_around_problem(tmp_graph: RelationalGraph):
    tmp_graph.add_problem(problem_id=1, problem_type="solve", signature="s",
                          fingerprint=_fp(sig="s"), raw_input="x", parsed_pretty="x")
    sub = tmp_graph.subgraph_around_problem(1, radius=1)
    assert sub["nodes"]
    ids = {n["data"]["id"] for n in sub["nodes"]}
    assert "p:1" in ids
    # Empty subgraph for unknown id
    assert tmp_graph.subgraph_around_problem(999, radius=1) == {"nodes": [], "edges": []}
