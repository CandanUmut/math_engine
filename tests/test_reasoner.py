"""End-to-end reasoner behavior, with an isolated SQLite store."""
from __future__ import annotations

from pru_math.reasoner import Reasoner
from pru_math.store import Store


def test_reasoner_solve_quadratic(tmp_store: Store):
    r = Reasoner(store=tmp_store)
    out = r.solve("Eq(x**2 - 5*x + 6, 0)")
    assert out.ok is True
    assert out.verification_status == "verified"
    assert {"2", "3"}.issubset({s.strip() for s in out.answer_pretty.strip("[]").split(",")})
    # trace must include parse, fingerprint, tool_call, verify, persist
    kinds = [s.kind for s in out.trace]
    for k in ("parse", "fingerprint", "tool_call", "verify", "persist"):
        assert k in kinds


def test_reasoner_solve_indefinite_integral(tmp_store: Store):
    r = Reasoner(store=tmp_store)
    out = r.solve("Integral(cos(x), x)")
    assert out.ok
    assert out.verification_status == "verified"
    assert out.problem_type == "integrate"


def test_reasoner_solve_definite_integral(tmp_store: Store):
    r = Reasoner(store=tmp_store)
    out = r.solve("Integral(x**2, (x, 0, 1))")
    assert out.ok
    assert out.verification_status == "verified"
    assert "1/3" in out.answer_pretty.replace(" ", "")


def test_reasoner_parse_failure_is_graceful(tmp_store: Store):
    r = Reasoner(store=tmp_store)
    out = r.solve("this is not a math expression at all !!!")
    assert out.ok is False
    assert out.error and "parse" in out.error.lower()
    # ensure no problem was persisted on parse failure
    assert out.problem_id is None


def test_reasoner_persists_fingerprint_and_attempt(tmp_store: Store):
    r = Reasoner(store=tmp_store)
    out = r.solve("sin(x)**2 + cos(x)**2")
    assert out.problem_id is not None
    rec = tmp_store.get_problem(out.problem_id)
    assert rec is not None
    assert rec.signature == out.fingerprint["signature"]
    attempts = tmp_store.list_attempts(out.problem_id)
    assert len(attempts) == 1
    assert attempts[0].tool == "sympy"
    assert attempts[0].verification_status == "verified"
