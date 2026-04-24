from __future__ import annotations

import sympy as sp

from pru_math import problem_types as PT
from pru_math.fingerprint import compute_fingerprint, similarity


x, y = sp.symbols("x y")


def test_fingerprint_is_deterministic():
    e = x**2 + 3 * x - 4
    a = compute_fingerprint(e, problem_type=PT.SIMPLIFY)
    b = compute_fingerprint(e, problem_type=PT.SIMPLIFY)
    assert a == b
    assert a["signature"] == b["signature"]


def test_fingerprint_structural_equivalence_ignores_variable_names():
    sig_x = compute_fingerprint(x**2 + x, problem_type=PT.FACTOR)["signature"]
    sig_y = compute_fingerprint(y**2 + y, problem_type=PT.FACTOR)["signature"]
    assert sig_x == sig_y


def test_fingerprint_changes_with_problem_type():
    e = x**2 - 5 * x + 6
    fs = compute_fingerprint(e, problem_type=PT.SOLVE)["signature"]
    fi = compute_fingerprint(e, problem_type=PT.FACTOR)["signature"]
    assert fs != fi


def test_fingerprint_detects_trig_and_log():
    fp_trig = compute_fingerprint(sp.sin(x) + sp.cos(x), problem_type=PT.SIMPLIFY)
    assert fp_trig["function_flags"]["trig"] is True
    assert fp_trig["function_flags"]["log"] is False

    fp_log = compute_fingerprint(sp.log(x + 1), problem_type=PT.DIFFERENTIATE)
    assert fp_log["function_flags"]["log"] is True
    assert fp_log["function_flags"]["trig"] is False


def test_polynomial_degree_recorded():
    fp = compute_fingerprint(x**3 + x, problem_type=PT.FACTOR)
    assert fp["polynomial_degree"] == 3


def test_similarity_bounds():
    a = compute_fingerprint(x**2 + x, problem_type=PT.FACTOR)
    b = compute_fingerprint(x**3 + x, problem_type=PT.FACTOR)
    s = similarity(a, b)
    assert 0.0 <= s <= 1.0
    # Same problem type + overlapping operator sets => clearly above 0.5.
    assert s > 0.5


def test_identical_fingerprints_have_similarity_one():
    e = x**2 + x
    a = compute_fingerprint(e, problem_type=PT.FACTOR)
    b = compute_fingerprint(e, problem_type=PT.FACTOR)
    assert similarity(a, b) == 1.0


def test_different_types_drop_similarity():
    a = compute_fingerprint(x**2 + x, problem_type=PT.FACTOR)
    b = compute_fingerprint(x**2 + x, problem_type=PT.INTEGRATE)
    assert similarity(a, b) < 0.8
