"""Shared types and the Tool abstract base class.

Phase 4 introduces the multi-tool registry. Each backend (SymPy, numeric,
Z3, Wolfram) implements :class:`Tool`; the reasoner asks the registry for
the candidate ``(tool_name, approach_name)`` pairs and the learner ranks
them across tools, not just within SymPy.

Phase 6 wraps every tool call in a wall-clock budget enforced by
:mod:`pru_math.tools.timeout`. Subclasses don't have to do anything —
the base ``solve_with`` does the wrapping and turns timeouts into
ordinary failed :class:`ToolResult` rows so the learner records them
as a failure mode like any other error.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..parser import ParsedProblem


@dataclass
class ToolResult:
    """Outcome of a single tool invocation.

    A tool returns ``success=True`` when it produced a candidate answer,
    regardless of whether verification passes. Verification is a separate
    concern (:mod:`pru_math.verifier`).
    """

    tool: str
    approach: str                     # e.g. "sympy.solve", "numeric.fsolve"
    success: bool
    result: Any = None                # SymPy expression, list, float, str, ...
    result_repr: str = ""             # ``sp.srepr`` of result, for storage
    result_pretty: str = ""           # human-readable form
    time_ms: float = 0.0
    error: str | None = None
    steps: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CrossVerification:
    """Outcome of a second-tool re-check on a primary result."""

    tool: str
    status: str          # "agree" | "disagree" | "inconclusive" | "unsupported"
    detail: str = ""
    time_ms: float = 0.0


class ToolError(RuntimeError):
    """Raised by a tool for an unrecoverable internal error."""


class Tool(ABC):
    """Common interface every backend (SymPy, numeric, Z3, Wolfram) implements.

    The registry consults ``is_available`` once at startup and ``can_handle``
    on every solve to filter candidates. The learner ranks the surviving
    candidates by their per-(signature, tool, approach) statistics.
    """

    name: str = "abstract"

    # --- Capability ----------------------------------------------------

    def is_available(self) -> bool:
        """Returns whether the underlying backend is usable in this process.

        For Python-backed tools (SymPy, mpmath/scipy, Z3) this typically
        means ``True`` once dependencies import cleanly. For HTTP-backed
        tools (Wolfram) it means the API key / endpoint is configured.
        Unavailable tools are excluded from the registry.
        """
        return True

    @abstractmethod
    def candidate_approaches(self, problem_type: str) -> Sequence[str]:
        """List the approach names this tool offers for ``problem_type``.
        Empty list means "this tool does not handle this type at all"."""

    def can_handle(self, fingerprint: dict[str, Any]) -> float:
        """Self-reported confidence in [0, 1]. ``0`` means the tool does
        not want to be considered (registry will filter it out). The
        default is a neutral 0.5; concrete tools override with simple
        rules ("if polynomial degree > 0, I can probably solve it")."""
        return 0.5

    # Phase 6: per-tool wall-clock budget. ``0`` or ``None`` disables
    # the timeout for this tool (e.g. some tests want to opt out).
    timeout_s: float | None = None

    # Phase 6: priority used by the registry to pick a *cross-verifier*
    # — higher wins. Subclasses override; default is neutral.
    cross_verify_priority: int = 0

    def solve_with(self, problem: ParsedProblem, approach: str) -> ToolResult:
        """Run a specific approach with the per-tool wall-clock budget.

        Subclasses implement :meth:`_solve_with`; this wrapper enforces
        the budget and turns any :class:`ToolTimeoutError` into a normal
        failed :class:`ToolResult` (so the learner records it like any
        other failure mode).
        """
        from .timeout import ToolTimeoutError, run_with_timeout
        from ..config import CONFIG
        budget = self.timeout_s if self.timeout_s is not None else CONFIG.tool_timeout_s
        if budget is None or budget <= 0:
            return self._solve_with(problem, approach)
        try:
            return run_with_timeout(self._solve_with, budget, problem, approach)
        except ToolTimeoutError as exc:
            return ToolResult(
                tool=self.name, approach=approach, success=False,
                time_ms=float(budget) * 1000.0,
                error=f"ToolTimeoutError: {exc}",
                meta={"timeout_s": float(budget),
                      "problem_type": problem.problem_type},
            )

    @abstractmethod
    def _solve_with(self, problem: ParsedProblem, approach: str) -> ToolResult:
        """The actual tool work. Subclasses implement this; callers go
        through :meth:`solve_with` so the timeout is enforced uniformly."""

    # --- Cross-verification (optional) --------------------------------

    def can_cross_verify(self, problem: ParsedProblem) -> bool:
        """Whether this tool can re-check another tool's answer for the
        given problem. Default: only when the tool can solve it itself."""
        return bool(self.candidate_approaches(problem.problem_type))

    def cross_verify(
        self,
        problem: ParsedProblem,
        candidate: Any,
    ) -> CrossVerification:
        """Default implementation: try to solve the problem fresh with this
        tool, then check that the produced answer matches ``candidate``
        symbolically/numerically. Subclasses may override with cheaper
        domain-specific checks (e.g. Z3 doing an SMT proof of equality)."""
        from .. import verifier as v   # local import avoids a cycle
        approaches = list(self.candidate_approaches(problem.problem_type))
        if not approaches:
            return CrossVerification(self.name, "unsupported",
                                     f"{self.name} does not handle "
                                     f"problem_type={problem.problem_type}")
        result = self.solve_with(problem, approaches[0])
        if not result.success:
            return CrossVerification(self.name, "inconclusive",
                                     result.error or "tool failed to solve",
                                     time_ms=result.time_ms)
        try:
            check = v._verify_identity(   # noqa: SLF001 — intentional reuse
                _as_sympy(candidate), _as_sympy(result.result), seed=11,
            )
        except Exception as exc:
            return CrossVerification(self.name, "inconclusive",
                                     f"comparison failed: {exc}",
                                     time_ms=result.time_ms)
        if check.status == "verified":
            return CrossVerification(self.name, "agree", check.detail,
                                     time_ms=result.time_ms)
        if check.status == "refuted":
            return CrossVerification(self.name, "disagree", check.detail,
                                     time_ms=result.time_ms)
        return CrossVerification(self.name, "inconclusive", check.detail,
                                 time_ms=result.time_ms)


def _as_sympy(value: Any):
    """Best-effort conversion to a SymPy object for cross-verification."""
    import sympy as sp
    if isinstance(value, sp.Basic):
        return value
    if isinstance(value, (list, tuple, set)):
        items = [sp.sympify(v) for v in value]
        return sp.Tuple(*items) if items else sp.Integer(0)
    if isinstance(value, (int, float, complex)):
        return sp.sympify(value)
    if isinstance(value, str):
        try:
            return sp.sympify(value)
        except Exception:
            return sp.Symbol(value)
    return sp.sympify(value)
