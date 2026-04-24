"""Phase 1 reasoner.

For Phase 1 the orchestration is deliberately simple:

    parse → fingerprint → SymPy → verify → persist → emit trace

Phase 2 inserts a graph-retrieval step before the tool call, Phase 3 adds
a ranked multi-approach loop, Phase 4 adds alternate tools. The interface
returned from :meth:`solve` is already shaped to carry a reasoning trace
so those additions do not change the API.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from .fingerprint import compute_fingerprint
from .parser import ParseError, ParsedProblem, parse
from .store import Store
from .tools import sympy_tool
from .tools.base import ToolResult
from .verifier import VerificationResult, verify


@dataclass
class TraceStep:
    """A single step in the reasoning trace, surfaced to the UI."""

    kind: str                       # "parse" | "fingerprint" | "tool_call" | "verify" | "persist"
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class SolveOutcome:
    ok: bool
    problem_id: int | None
    answer_pretty: str | None
    answer_repr: str | None
    problem_type: str | None
    source_format: str | None
    parsed_pretty: str | None
    fingerprint: dict[str, Any] | None
    tool: str | None
    approach: str | None
    time_ms: float
    verification_status: str | None
    verification_detail: str | None
    error: str | None
    trace: list[TraceStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trace"] = [asdict(s) for s in self.trace]
        return d


class Reasoner:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    # -------------------------------------------------------------------

    def solve(self, text: str) -> SolveOutcome:
        trace: list[TraceStep] = []

        # 1. Parse
        try:
            parsed: ParsedProblem = parse(text)
        except ParseError as exc:
            trace.append(TraceStep(
                kind="parse",
                summary="Parsing failed",
                detail={"error": str(exc)},
            ))
            return SolveOutcome(
                ok=False, problem_id=None, answer_pretty=None, answer_repr=None,
                problem_type=None, source_format=None, parsed_pretty=None,
                fingerprint=None, tool=None, approach=None, time_ms=0.0,
                verification_status=None, verification_detail=None,
                error=f"parse error: {exc}", trace=trace,
            )

        trace.append(TraceStep(
            kind="parse",
            summary=f"Parsed as {parsed.source_format} → problem_type={parsed.problem_type}",
            detail={
                "source_format": parsed.source_format,
                "problem_type": parsed.problem_type,
                "pretty": parsed.pretty(),
            },
        ))

        # 2. Fingerprint
        fp = compute_fingerprint(
            parsed.expression,
            problem_type=parsed.problem_type,
            target_symbol=parsed.target_symbol,
        )
        trace.append(TraceStep(
            kind="fingerprint",
            summary=f"Signature {fp['signature']} · {fp['node_count']} nodes, "
                    f"{fp['variable_count']} vars",
            detail=fp,
        ))

        # 3. Persist the problem (before tool call, so failures are also recorded)
        problem_id = self.store.insert_problem(
            raw_input=text,
            source_format=parsed.source_format,
            problem_type=parsed.problem_type,
            parsed_expr=parsed.expr_repr(),
            parsed_pretty=parsed.pretty(),
            fingerprint=fp,
        )

        # 4. Call SymPy
        result: ToolResult = sympy_tool.solve(parsed)
        trace.append(TraceStep(
            kind="tool_call",
            summary=f"{result.approach} → "
                    f"{'ok' if result.success else 'error'} in {result.time_ms:.1f} ms",
            detail={
                "tool": result.tool,
                "approach": result.approach,
                "success": result.success,
                "result_pretty": result.result_pretty,
                "steps": result.steps,
                "error": result.error,
            },
        ))

        # 5. Verify (only if the tool returned a candidate)
        verification: VerificationResult | None = None
        if result.success:
            verification = verify(parsed, result.result)
            trace.append(TraceStep(
                kind="verify",
                summary=f"Verification: {verification.status} ({verification.checks} check(s))",
                detail={"status": verification.status, "detail": verification.detail},
            ))

        # 6. Persist attempt + outcome aggregate
        self.store.insert_attempt(
            problem_id=problem_id,
            tool=result.tool,
            approach=result.approach,
            success=result.success,
            result_repr=result.result_repr or None,
            result_pretty=result.result_pretty or None,
            verification_status=verification.status if verification else None,
            verification_detail=verification.detail if verification else None,
            time_ms=result.time_ms,
            error=result.error,
            steps=result.steps,
        )
        self.store.upsert_tool_outcome(
            signature=fp["signature"],
            tool=result.tool,
            approach=result.approach,
            success=result.success,
            verified=bool(verification and verification.status == "verified"),
            time_ms=result.time_ms,
        )
        trace.append(TraceStep(
            kind="persist",
            summary=f"Stored attempt on problem_id={problem_id}",
            detail={"problem_id": problem_id},
        ))

        return SolveOutcome(
            ok=result.success,
            problem_id=problem_id,
            answer_pretty=result.result_pretty or None,
            answer_repr=result.result_repr or None,
            problem_type=parsed.problem_type,
            source_format=parsed.source_format,
            parsed_pretty=parsed.pretty(),
            fingerprint=fp,
            tool=result.tool,
            approach=result.approach,
            time_ms=result.time_ms,
            verification_status=verification.status if verification else None,
            verification_detail=verification.detail if verification else None,
            error=result.error,
            trace=trace,
        )
