"""Tool registry — the single entry point the reasoner uses to discover
candidate ``(tool_name, approach_name)`` pairs across every backend.

The registry filters out:

- tools whose ``is_available()`` returns False (e.g. Wolfram without an
  API key, Z3 if ``z3-solver`` isn't installed)
- tools whose ``can_handle(fingerprint)`` returns 0 for the current
  problem (e.g. Z3 on a transcendental integrand)

Whatever remains is handed to the learner, which ranks across the union
of (signature, tool, approach) statistics from the SQLite store.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..parser import ParsedProblem
from .base import CrossVerification, Tool, ToolResult
from .numeric_tool import NumericTool
from .sympy_tool import SymPyTool
from .wolfram_tool import WolframTool
from .z3_tool import Z3Tool


@dataclass
class CandidatePair:
    tool: str
    approach: str
    confidence: float

    def to_pair(self) -> tuple[str, str]:
        return (self.tool, self.approach)


class ToolRegistry:
    """Holds the set of registered :class:`Tool` instances.

    Construct via :func:`default_registry` to get all built-in tools, or
    pass a custom list (handy for tests)."""

    def __init__(self, tools: Iterable[Tool]):
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self._tools[t.name] = t

    # --- Discovery -----------------------------------------------------

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def available_tools(self) -> list[Tool]:
        return [t for t in self._tools.values() if t.is_available()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    # --- Candidate generation -----------------------------------------

    def candidates_for(
        self,
        *,
        problem_type: str,
        fingerprint: dict,
        min_confidence: float = 0.0,
    ) -> list[CandidatePair]:
        """All ``(tool, approach)`` pairs that any available tool reports
        it can attempt. Sorted by descending self-reported confidence,
        then by tool name then approach name (deterministic)."""
        out: list[CandidatePair] = []
        for tool in self.available_tools():
            confidence = tool.can_handle(fingerprint)
            if confidence < min_confidence:
                continue
            for approach in tool.candidate_approaches(problem_type):
                out.append(CandidatePair(tool=tool.name, approach=approach,
                                          confidence=float(confidence)))
        out.sort(key=lambda c: (-c.confidence, c.tool, c.approach))
        return out

    # --- Solve dispatch ------------------------------------------------

    def solve_with(
        self, *, tool: str, approach: str, problem: ParsedProblem,
    ) -> ToolResult:
        t = self._tools.get(tool)
        if t is None or not t.is_available():
            return ToolResult(tool=tool, approach=approach, success=False,
                              error=f"tool {tool!r} is not registered or available")
        return t.solve_with(problem, approach)

    # --- Cross-verification --------------------------------------------

    def pick_cross_verifier(
        self, *, primary_tool: str, problem: ParsedProblem,
    ) -> Tool | None:
        """Pick a different available tool that can cross-check ``primary_tool``'s
        result for this problem.

        Phase 6: candidates are sorted by ``cross_verify_priority``
        (descending), so Z3 (proof) beats numeric (empirical agreement)
        beats SymPy (symbolic re-derivation). Ties are broken by tool
        name for determinism."""
        eligible = [
            t for t in self.available_tools()
            if t.name != primary_tool and t.can_cross_verify(problem)
        ]
        if not eligible:
            return None
        eligible.sort(key=lambda t: (-int(t.cross_verify_priority), t.name))
        return eligible[0]

    # --- Inspection ----------------------------------------------------

    def status(self) -> list[dict]:
        """Status snapshot for the UI / ``/tools`` endpoint."""
        out: list[dict] = []
        for name, tool in self._tools.items():
            out.append({
                "name": name,
                "available": tool.is_available(),
                "class": type(tool).__name__,
            })
        return out


def default_registry() -> ToolRegistry:
    """The standard set of built-in tools, in their default ordering.

    SymPy comes first because it's the generalist; numeric and Z3 are
    specialists; Wolfram is the optional HTTP fallback. Order only affects
    tie-breaking among approaches with identical confidence; the learner
    is what actually decides which one is tried first.
    """
    return ToolRegistry([
        SymPyTool(),
        NumericTool(),
        Z3Tool(),
        WolframTool(),
    ])
