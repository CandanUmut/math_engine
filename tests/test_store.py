from __future__ import annotations

from pru_math.store import Store


def test_insert_and_read_problem(tmp_store: Store):
    pid = tmp_store.insert_problem(
        raw_input="x**2",
        source_format="sympy",
        problem_type="simplify",
        parsed_expr="Pow(Symbol('x'), Integer(2))",
        parsed_pretty="x**2",
        fingerprint={"signature": "abc123", "problem_type": "simplify"},
    )
    rec = tmp_store.get_problem(pid)
    assert rec is not None
    assert rec.raw_input == "x**2"
    assert rec.signature == "abc123"


def test_insert_attempt_and_outcome(tmp_store: Store):
    pid = tmp_store.insert_problem(
        raw_input="x",
        source_format="sympy",
        problem_type="simplify",
        parsed_expr="Symbol('x')",
        parsed_pretty="x",
        fingerprint={"signature": "sig1", "problem_type": "simplify"},
    )
    tmp_store.insert_attempt(
        problem_id=pid, tool="sympy", approach="sympy.simplify",
        success=True, result_repr="Symbol('x')", result_pretty="x",
        verification_status="verified", verification_detail="ok",
        time_ms=3.2, error=None, steps=["step a", "step b"],
    )
    tmp_store.upsert_tool_outcome(
        signature="sig1", tool="sympy", approach="sympy.simplify",
        success=True, verified=True, time_ms=3.2,
    )
    tmp_store.upsert_tool_outcome(
        signature="sig1", tool="sympy", approach="sympy.simplify",
        success=True, verified=True, time_ms=1.8,
    )
    attempts = tmp_store.list_attempts(pid)
    assert len(attempts) == 1
    assert attempts[0].steps == ["step a", "step b"]

    stats = tmp_store.stats()
    assert stats["problems"] >= 1
    assert stats["attempts"] >= 1
    assert stats["verified_attempts"] >= 1


def test_list_problems_ordering(tmp_store: Store):
    ids = []
    for i in range(3):
        ids.append(tmp_store.insert_problem(
            raw_input=f"{i}",
            source_format="sympy",
            problem_type="simplify",
            parsed_expr=f"Integer({i})",
            parsed_pretty=str(i),
            fingerprint={"signature": f"s{i}", "problem_type": "simplify"},
        ))
    listed = tmp_store.list_problems(limit=10)
    assert [p.id for p in listed] == list(reversed(ids))
