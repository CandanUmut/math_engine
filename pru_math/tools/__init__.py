"""Tool wrappers. Each tool exposes a uniform ``solve(problem)`` entrypoint
so the reasoner can dispatch without knowing tool internals."""

from .base import ToolResult, ToolError  # noqa: F401
