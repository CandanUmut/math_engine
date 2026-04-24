"""Shared types for tool wrappers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Outcome of a single tool invocation.

    A tool returns ``success=True`` when it produced a candidate answer,
    regardless of whether verification passes. Verification is a separate
    concern (:mod:`pru_math.verifier`).
    """

    tool: str
    approach: str                     # e.g. "sympy.solve", "sympy.integrate"
    success: bool
    result: Any = None                # SymPy expression, list, or string
    result_repr: str = ""             # ``sp.srepr`` of result, for storage
    result_pretty: str = ""           # human-readable form
    time_ms: float = 0.0
    error: str | None = None
    steps: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class ToolError(RuntimeError):
    """Raised by a tool for an unrecoverable internal error."""
