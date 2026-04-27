"""Wolfram Alpha tool — optional HTTP-backed solver.

Activated only when ``WOLFRAM_APP_ID`` is set in the environment. When
absent, :meth:`WolframTool.is_available` returns ``False`` and the
registry filters the tool out — no network calls are ever made.

Approaches
----------
- ``wolfram.short`` — the Short Answers API (single line of text)
- ``wolfram.full``  — the Full Results API (subpod text grouped by pod)

Wolfram answers are returned as raw strings; the verifier sees them as
opaque text and falls back to its identity-style numeric checks where
possible. This is a deliberate trade-off: Wolfram's strength is breadth
of coverage, not machine-checkable formality.
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import httpx

from .. import problem_types as PT
from ..parser import ParsedProblem
from .base import Tool, ToolResult


TOOL_NAME = "wolfram"

# Wolfram supports many problem types; we don't try to enumerate them
# here — the tool is happy to attempt anything and return whatever the
# API says. The learner will down-rank approaches that don't pan out.
_DEFAULT_TYPES = (
    PT.SOLVE, PT.SIMPLIFY, PT.INTEGRATE, PT.DIFFERENTIATE,
    PT.FACTOR, PT.EXPAND, PT.EVALUATE, PT.LIMIT, PT.SERIES,
)


def _app_id() -> str | None:
    val = os.getenv("WOLFRAM_APP_ID", "").strip()
    return val or None


def _phrase(problem: ParsedProblem) -> str:
    """Best-effort English phrasing of the problem for the Wolfram API."""
    expr = problem.pretty()
    pt = problem.problem_type
    if pt == PT.SOLVE:
        return f"solve {expr}"
    if pt == PT.SIMPLIFY:
        return f"simplify {expr}"
    if pt == PT.INTEGRATE:
        return f"integrate {expr}"
    if pt == PT.DIFFERENTIATE:
        return f"differentiate {expr}"
    if pt == PT.FACTOR:
        return f"factor {expr}"
    if pt == PT.EXPAND:
        return f"expand {expr}"
    if pt == PT.EVALUATE:
        return f"evaluate {expr}"
    if pt == PT.LIMIT:
        return expr
    if pt == PT.SERIES:
        return f"series of {expr}"
    return expr


def _short(phrase: str, app_id: str, timeout: float) -> str:
    url = f"https://api.wolframalpha.com/v1/result?appid={quote(app_id)}&i={quote(phrase)}"
    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text.strip()


def _full(phrase: str, app_id: str, timeout: float) -> str:
    """Fetch the JSON Full Results API and concatenate the most relevant
    plaintext fields (`Result`, `Solution`, `IndefiniteIntegral`, ...)."""
    url = (
        "https://api.wolframalpha.com/v2/query"
        f"?appid={quote(app_id)}&output=json&format=plaintext&input={quote(phrase)}"
    )
    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json().get("queryresult") or {}
    if not data.get("success"):
        raise RuntimeError(f"Wolfram returned no results: {data.get('error') or data}")

    pods = data.get("pods") or []
    primary_pods = [
        p for p in pods
        if (p.get("title") or "").lower() in {
            "result", "results", "solution", "solutions",
            "indefinite integral", "definite integral", "derivative",
            "factored form", "expanded form",
        }
    ]
    target_pods = primary_pods or pods[:1]
    parts: list[str] = []
    for p in target_pods:
        for sub in p.get("subpods") or []:
            text = (sub.get("plaintext") or "").strip()
            if text:
                parts.append(text)
    if not parts:
        raise RuntimeError("Wolfram pods carried no plaintext content")
    return "\n".join(parts)


class WolframTool(Tool):
    name = TOOL_NAME

    def __init__(self, *, timeout: float = 10.0):
        self.timeout = timeout

    def is_available(self) -> bool:
        return _app_id() is not None

    def candidate_approaches(self, problem_type: str):
        if not self.is_available():
            return []
        if problem_type in _DEFAULT_TYPES:
            return ["wolfram.short", "wolfram.full"]
        return []

    def can_handle(self, fingerprint):
        if not self.is_available():
            return 0.0
        # Wolfram is a generalist HTTP fallback: never zero, never one.
        return 0.5

    def solve_with(self, problem: ParsedProblem, approach: str) -> ToolResult:
        app_id = _app_id()
        if not app_id:
            return ToolResult(tool=self.name, approach=approach, success=False,
                              error="WOLFRAM_APP_ID is not set")
        phrase = _phrase(problem)
        t0 = time.perf_counter()
        try:
            if approach == "wolfram.short":
                text = _short(phrase, app_id, self.timeout)
            elif approach == "wolfram.full":
                text = _full(phrase, app_id, self.timeout)
            else:
                return ToolResult(tool=self.name, approach=approach, success=False,
                                  error=f"unknown wolfram approach: {approach!r}")
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            dt = (time.perf_counter() - t0) * 1000.0
            return ToolResult(tool=self.name, approach=approach, success=False,
                              time_ms=dt, error=f"{type(exc).__name__}: {exc}")
        dt = (time.perf_counter() - t0) * 1000.0
        return ToolResult(
            tool=self.name, approach=approach, success=True,
            result=text, result_repr=repr(text), result_pretty=text,
            time_ms=dt,
            steps=[f"GET wolframalpha · {approach}", f"phrase: {phrase}"],
            meta={"problem_type": problem.problem_type, "phrase": phrase},
        )

    def can_cross_verify(self, problem):
        # Wolfram returns opaque strings; we don't trust it as a cross-verifier.
        return False
