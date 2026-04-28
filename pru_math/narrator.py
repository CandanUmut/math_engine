"""Phase 8 narrator — translate a stored reasoning trace into plain English.

The single hard rule: **the LLM never decides math.** This module takes
an already-solved problem (its parsed expression, fingerprint, attempts,
verification, and trace), packages it into a constrained prompt, and
asks the local Ollama model to produce a short narrative. The math
facts (which approach was tried, what verified, what didn't) all come
from the engine's own records — the model only paraphrases.

Disabling rules:
- if ``OLLAMA_ENABLED=false``, return a deterministic plain-English
  summary built from the trace alone (no network call)
- if Ollama is unreachable, fall back to the same deterministic summary
  so the endpoint always returns something useful.
"""
from __future__ import annotations

from typing import Any

import httpx

from .config import CONFIG


_SYSTEM = """You are a careful mathematician's assistant.
You will be given a structured record of how a math engine solved one
problem, including the parsed expression, the candidate approaches it
considered, every attempt it made, the verifier's verdict, and any
cross-tool agreement.

Your job is to write a short (2–5 sentence) plain-English narration of
that record. Constraints:

- Do NOT solve the problem yourself.
- Do NOT introduce facts not present in the record.
- Refer to approaches by their names (e.g. "sympy.solve").
- If verification was anything other than "verified", say so plainly.
- Audience: a researcher who can read math but is skimming.
""".strip()


def _format_trace_for_prompt(record: dict[str, Any]) -> str:
    p = record.get("problem", {})
    attempts = record.get("attempts", [])
    fp = (p.get("fingerprint") or {})
    parts: list[str] = []
    parts.append(f"Problem: {p.get('parsed_pretty')}")
    parts.append(f"Type: {p.get('problem_type')}")
    sig = (p.get('signature') or '')[:8]
    parts.append(
        f"Signature: {sig} ({fp.get('node_count', '?')} AST nodes, "
        f"{fp.get('variable_count', '?')} vars)"
    )
    if not attempts:
        parts.append("No attempts were recorded.")
    for i, a in enumerate(attempts, 1):
        line = (
            f"Attempt {i}: {a.get('tool')}.{a.get('approach')} → "
            f"{'ok' if a.get('success') else 'error'}"
        )
        if a.get("verification_status"):
            line += f", verifier said {a['verification_status']}"
        if a.get("cross_verify_status"):
            line += (
                f", cross-checked by {a.get('cross_verify_tool')}: "
                f"{a['cross_verify_status']}"
            )
        if a.get("error"):
            line += f", error: {a['error']}"
        if a.get("result_pretty"):
            line += f". Result: {a['result_pretty']}"
        parts.append(line)
    return "\n".join(parts)


def _deterministic_summary(record: dict[str, Any]) -> str:
    p = record.get("problem", {})
    attempts = record.get("attempts", []) or []
    chosen = next(
        (a for a in attempts if a.get("verification_status") == "verified"),
        attempts[0] if attempts else None,
    )
    if not chosen:
        return f"No attempts recorded for {p.get('parsed_pretty', '?')}."
    pieces = [
        f"Problem {p.get('parsed_pretty', '?')} was tackled with "
        f"{chosen.get('tool')}.{chosen.get('approach')}."
    ]
    if chosen.get("verification_status") == "verified":
        pieces.append(f"The verifier confirmed the result {chosen.get('result_pretty')}.")
    elif chosen.get("verification_status"):
        pieces.append(
            f"The verifier returned {chosen['verification_status']} on "
            f"the candidate {chosen.get('result_pretty')}."
        )
    if chosen.get("cross_verify_status"):
        pieces.append(
            f"A second tool ({chosen.get('cross_verify_tool')}) cross-checked "
            f"and returned {chosen['cross_verify_status']}."
        )
    n_attempts = len(attempts)
    if n_attempts > 1:
        pieces.append(f"{n_attempts} approaches were tried in total.")
    return " ".join(pieces)


def explain_record(record: dict[str, Any]) -> dict[str, Any]:
    """Public entry. Returns ``{"text": str, "source": "ollama"|"deterministic"}``."""
    fallback = _deterministic_summary(record)
    if not CONFIG.ollama_enabled:
        return {"text": fallback, "source": "deterministic",
                "reason": "OLLAMA_ENABLED=false"}
    prompt = _format_trace_for_prompt(record)
    payload = {
        "model": CONFIG.ollama_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        resp = httpx.post(
            f"{CONFIG.ollama_host.rstrip('/')}/api/chat",
            json=payload, timeout=CONFIG.tool_timeout_s,
        )
        resp.raise_for_status()
        text = ((resp.json().get("message") or {}).get("content") or "").strip()
        if not text:
            return {"text": fallback, "source": "deterministic",
                    "reason": "Ollama returned empty content"}
        return {"text": text, "source": "ollama", "model": CONFIG.ollama_model}
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"text": fallback, "source": "deterministic",
                "reason": f"Ollama unreachable: {type(exc).__name__}"}
