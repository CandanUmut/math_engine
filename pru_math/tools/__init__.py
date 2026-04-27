"""Tool wrappers. Each tool exposes a uniform ``solve(problem)`` entrypoint
so the reasoner can dispatch without knowing tool internals."""

from .base import CrossVerification, Tool, ToolError, ToolResult   # noqa: F401
from .registry import CandidatePair, ToolRegistry, default_registry   # noqa: F401
