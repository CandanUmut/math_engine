"""Tool timeout enforcement (Phase 6)."""
from __future__ import annotations

import time

import pytest

from pru_math.parser import parse
from pru_math.tools.base import Tool, ToolResult
from pru_math.tools.timeout import ToolTimeoutError, run_with_timeout


def test_run_with_timeout_returns_result_when_fast():
    val = run_with_timeout(lambda: 1 + 2, 5.0)
    assert val == 3


def test_run_with_timeout_raises_on_overrun():
    with pytest.raises(ToolTimeoutError):
        run_with_timeout(lambda: time.sleep(2.0) or "done", 0.1)


def test_zero_or_negative_timeout_runs_inline():
    val = run_with_timeout(lambda: "ok", 0)
    assert val == "ok"
    val = run_with_timeout(lambda: "ok", -1)
    assert val == "ok"


class _SlowTool(Tool):
    """Synthetic tool that always sleeps longer than its budget."""
    name = "slow"
    timeout_s = 0.05

    def candidate_approaches(self, problem_type):
        return ["slow.snore"]

    def can_handle(self, fingerprint):
        return 1.0

    def _solve_with(self, problem, approach):
        time.sleep(0.5)
        return ToolResult(tool=self.name, approach=approach, success=True,
                          result="should never get here", result_pretty="x",
                          result_repr="'x'")


def test_tool_solve_with_returns_failure_on_timeout():
    tool = _SlowTool()
    res = tool.solve_with(parse("x"), "slow.snore")
    assert not res.success
    assert "ToolTimeoutError" in (res.error or "")
    # The recorded time_ms should be at least the budget, not the full 500ms,
    # since we abandon the worker.
    assert res.time_ms >= 50.0
