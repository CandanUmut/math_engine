"""Tool-call timeout enforcement.

Phase 6 closes a real gap: ``CONFIG.tool_timeout_s`` was read but never
applied. Z3's ``risch=True`` integral approach, Wolfram's HTTP fall-throughs,
and the occasional symbolic timeout in SymPy could each hang a request.

This module provides a single helper, :func:`run_with_timeout`, that runs
any callable in a worker thread and returns whatever it produced — or
raises :class:`ToolTimeoutError` when the wall-clock budget is exceeded.

Why threads, not signals or processes:

- ``signal.SIGALRM`` only works on the main thread of the main interpreter,
  so it breaks under FastAPI's threadpool.
- ``multiprocessing`` would force tool inputs to be picklable. SymPy
  expressions and our ``ParsedProblem`` aren't, in general.
- Threads can't be safely killed in CPython, so ``run_with_timeout``
  abandons the still-running worker rather than killing it. That leaks a
  thread per timeout — an acceptable trade for the correctness win, and
  bounded by the budget cap (``PRU_MAX_ATTEMPTS`` per problem).
"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Callable


_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_DEFAULT_MAX_WORKERS = 8


class ToolTimeoutError(TimeoutError):
    """Raised when a tool call exceeds its budget."""


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=_DEFAULT_MAX_WORKERS,
            thread_name_prefix="pru-tool",
        )
    return _EXECUTOR


def run_with_timeout(fn: Callable[..., Any], timeout_s: float, *args: Any,
                     **kwargs: Any) -> Any:
    """Run ``fn(*args, **kwargs)`` with a wall-clock budget.

    On timeout, returns control to the caller immediately and raises
    :class:`ToolTimeoutError`; the worker thread keeps running until ``fn``
    notices its result is unwanted (most don't — that's fine, the next
    GC will let it go). On success, returns whatever ``fn`` returned.

    A non-positive ``timeout_s`` disables the budget and runs ``fn``
    inline so callers can opt out cheaply.
    """
    if timeout_s is None or timeout_s <= 0:
        return fn(*args, **kwargs)
    future = _executor().submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError as exc:
        raise ToolTimeoutError(
            f"tool exceeded {timeout_s:.2f}s budget"
        ) from exc
