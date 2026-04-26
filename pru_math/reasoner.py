"""Reasoner orchestrator.

Phase 3 flow:

    parse → fingerprint → retrieval → rank approaches → try (≤budget):
        tool_call → verify → persist attempt
        stop early on verified
    → graph_update → emit trace

The decision is grounded in the data: the learner reads ``tool_outcomes``
from the store, scores every candidate approach with UCB1, and the
reasoner tries them in that order. Every attempt — successful or not —
is persisted, and the next solve sees the updated stats.

The trace surfaces the candidate table, the chosen approach, every
intermediate failure, and the stat deltas so a user can audit not just
"what was the answer" but "why did the system try this first".
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from .config import CONFIG
from .fingerprint import compute_fingerprint
from .graph import RelationalGraph
from .learner import CandidateStats, Learner
from .parser import ParseError, ParsedProblem, parse
from .retrieval import SimilarProblem, find_similar_problems
from .store import Store
from .tools import sympy_tool
from .tools.base import ToolResult
from .verifier import VerificationResult, verify


@dataclass
class TraceStep:
    """A single step in the reasoning trace, surfaced to the UI."""

    kind: str   # parse | fingerprint | retrieval | decision | tool_call |
                # verify | persist | learn | graph_update
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class AttemptOutcome:
    """One iteration of the multi-attempt loop."""

    approach: str
    success: bool
    result_pretty: str | None
    verification_status: str | None
    verification_detail: str | None
    time_ms: float
    error: str | None
    attempt_id: int | None       # primary key in `attempts` table


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
    tool: str | None                # tool of the chosen attempt
    approach: str | None            # approach of the chosen attempt
    time_ms: float                  # wall time of the chosen attempt
    total_time_ms: float = 0.0      # sum across all attempts in this solve
    verification_status: str | None = None
    verification_detail: str | None = None
    error: str | None = None
    attempts: list[AttemptOutcome] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    similar: list[dict[str, Any]] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trace"] = [asdict(s) for s in self.trace]
        return d


class Reasoner:
    def __init__(self,
                 store: Store | None = None,
                 graph: RelationalGraph | None = None,
                 learner: Learner | None = None,
                 max_attempts: int | None = None):
        self.store = store or Store()
        self.graph = graph or RelationalGraph()
        self.learner = learner or Learner(self.store)
        self.max_attempts = max(1, max_attempts if max_attempts is not None
                                else CONFIG.max_attempts)

    # -------------------------------------------------------------------

    def solve(self, text: str) -> SolveOutcome:
        trace: list[TraceStep] = []

        # 1. Parse
        try:
            parsed: ParsedProblem = parse(text)
        except ParseError as exc:
            trace.append(TraceStep(
                kind="parse", summary="Parsing failed",
                detail={"error": str(exc)},
            ))
            return SolveOutcome(
                ok=False, problem_id=None, answer_pretty=None, answer_repr=None,
                problem_type=None, source_format=None, parsed_pretty=None,
                fingerprint=None, tool=None, approach=None, time_ms=0.0,
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

        # 3. Retrieval
        similar: list[SimilarProblem] = find_similar_problems(
            fp, graph=self.graph, store=self.store, k=CONFIG.similar_top_k,
        )
        if similar:
            top_summary = ", ".join(
                f"#{s.problem.id} ({s.score:.2f})" for s in similar[:3]
            )
            trace.append(TraceStep(
                kind="retrieval",
                summary=f"Found {len(similar)} similar past problem(s): {top_summary}",
                detail={
                    "k": CONFIG.similar_top_k,
                    "neighbours": [
                        {
                            "problem_id": s.problem.id,
                            "score": round(s.score, 4),
                            "problem_type": s.problem.problem_type,
                            "parsed_pretty": s.problem.parsed_pretty,
                            "best_approach": s.best_attempt.approach if s.best_attempt else None,
                            "best_verification": s.best_attempt.verification_status if s.best_attempt else None,
                        }
                        for s in similar
                    ],
                },
            ))
        else:
            trace.append(TraceStep(
                kind="retrieval",
                summary="No similar past problems yet — first of its kind in the graph.",
                detail={"k": CONFIG.similar_top_k, "neighbours": []},
            ))

        # 4. Persist the problem (before any tool call so failures are recorded)
        problem_id = self.store.insert_problem(
            raw_input=text,
            source_format=parsed.source_format,
            problem_type=parsed.problem_type,
            parsed_expr=parsed.expr_repr(),
            parsed_pretty=parsed.pretty(),
            fingerprint=fp,
        )

        # 5. Rank approaches
        approach_names = sympy_tool.candidate_approaches(parsed.problem_type)
        candidates: list[tuple[str, str]] = [
            (sympy_tool.TOOL_NAME, name) for name in approach_names
        ]
        ranked: list[CandidateStats] = self.learner.rank(
            signature=fp["signature"],
            problem_type=parsed.problem_type,
            candidates=candidates,
        )
        ranked_names = [r.approach for r in ranked]
        trace.append(TraceStep(
            kind="decision",
            summary=(
                f"Ranked {len(ranked)} approach(es): "
                + ", ".join(f"{r.approach} ({r.score:.2f})" for r in ranked[:5])
            ),
            detail={
                "candidates": [r.to_dict() for r in ranked],
                "policy": "UCB1",
                "exploration_c": self.learner.exploration_c,
                "max_attempts": self.max_attempts,
            },
        ))

        # 6. Multi-attempt loop
        attempts: list[AttemptOutcome] = []
        chosen_idx: int | None = None
        chosen_attempt_id: int | None = None
        chosen_result: ToolResult | None = None
        chosen_verification: VerificationResult | None = None
        total_time_ms = 0.0

        budget = min(self.max_attempts, len(ranked))
        for i in range(budget):
            cand = ranked[i]
            tool_name = cand.tool
            approach_name = cand.approach

            # Tool call
            if tool_name == sympy_tool.TOOL_NAME:
                result = sympy_tool.solve_with_approach(parsed, approach_name)
            else:
                # Phase 3 only registers SymPy approaches; this branch is here
                # so Phase 4 can register additional tools without changing
                # the loop. Until then, treat unknown tools as a hard fail.
                result = ToolResult(
                    tool=tool_name, approach=approach_name, success=False,
                    error=f"unknown tool: {tool_name}",
                )
            total_time_ms += result.time_ms
            trace.append(TraceStep(
                kind="tool_call",
                summary=f"[{i+1}/{budget}] {result.approach} → "
                        f"{'ok' if result.success else 'error'} in {result.time_ms:.1f} ms",
                detail={
                    "tool": result.tool,
                    "approach": result.approach,
                    "success": result.success,
                    "result_pretty": result.result_pretty,
                    "steps": result.steps,
                    "error": result.error,
                    "ucb_score": cand.score,
                    "rationale": cand.rationale,
                },
            ))

            # Verify (only if tool produced a candidate)
            verification: VerificationResult | None = None
            if result.success:
                verification = verify(parsed, result.result)
                trace.append(TraceStep(
                    kind="verify",
                    summary=f"[{i+1}/{budget}] verification: {verification.status} "
                            f"({verification.checks} check(s))",
                    detail={
                        "status": verification.status,
                        "detail": verification.detail,
                    },
                ))

            # Persist attempt + outcome aggregate (regardless of success)
            error_tag = self._error_tag(result, verification)
            attempt_id = self.store.insert_attempt(
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
                error=error_tag,
            )

            attempts.append(AttemptOutcome(
                approach=result.approach,
                success=result.success,
                result_pretty=result.result_pretty or None,
                verification_status=verification.status if verification else None,
                verification_detail=verification.detail if verification else None,
                time_ms=result.time_ms,
                error=result.error,
                attempt_id=attempt_id,
            ))

            # Stop when we hit a verified answer.
            if verification and verification.status == "verified":
                chosen_idx = i
                chosen_attempt_id = attempt_id
                chosen_result = result
                chosen_verification = verification
                break

        # If nothing verified, choose the *best* among what we tried as the
        # surface answer: prefer success, then "inconclusive", then errors.
        if chosen_result is None:
            best_i = self._choose_best_unverified(attempts)
            if best_i is not None:
                chosen_idx = best_i
                a = attempts[best_i]
                chosen_attempt_id = a.attempt_id
                # Recreate a synthetic ToolResult-like view from attempt fields.
                chosen_result = ToolResult(
                    tool=ranked[best_i].tool,
                    approach=ranked[best_i].approach,
                    success=a.success,
                    result_pretty=a.result_pretty or "",
                    result_repr="",
                    time_ms=a.time_ms,
                    error=a.error,
                )
                chosen_verification = (
                    VerificationResult(a.verification_status, a.verification_detail or "")
                    if a.verification_status else None
                )

        trace.append(TraceStep(
            kind="persist",
            summary=f"Stored {len(attempts)} attempt(s) on problem_id={problem_id}",
            detail={"problem_id": problem_id, "attempts": [a.attempt_id for a in attempts]},
        ))

        # 7. Learner-update trace step (purely descriptive — the data was
        #    already written by upsert_tool_outcome above; here we
        #    re-rank for the *next* problem and surface the deltas).
        post_ranked = self.learner.rank(
            signature=fp["signature"],
            problem_type=parsed.problem_type,
            candidates=candidates,
        )
        deltas = self._rank_deltas(ranked, post_ranked)
        trace.append(TraceStep(
            kind="learn",
            summary=f"Updated tool_outcomes; rank deltas: " + (", ".join(deltas) if deltas else "no movement"),
            detail={
                "before": [r.to_dict() for r in ranked],
                "after":  [r.to_dict() for r in post_ranked],
            },
        ))

        # 8. Graph update
        self.graph.add_problem(
            problem_id=problem_id,
            problem_type=parsed.problem_type,
            signature=fp["signature"],
            fingerprint=fp,
            raw_input=text,
            parsed_pretty=parsed.pretty(),
        )
        # Link to the *chosen* approach (the one whose result is surfaced),
        # but also record a `solved_by` edge per attempted approach so the
        # graph view shows every approach the system tried on this problem.
        for a in attempts:
            self.graph.link_solved_by(
                problem_id=problem_id,
                tool=sympy_tool.TOOL_NAME,
                approach=a.approach,
                success=a.success,
                verified=a.verification_status == "verified",
                time_ms=a.time_ms,
            )
        edges_added = self.graph.add_similarity_edges(
            new_problem_id=problem_id,
            candidates=[(s.problem.id, s.score) for s in similar],
        )
        self.graph.commit()
        trace.append(TraceStep(
            kind="graph_update",
            summary=f"Graph: +1 problem, +{edges_added} similarity edge(s); "
                    f"linked {len(attempts)} approach(es)",
            detail={
                "problem_node": f"p:{problem_id}",
                "similarity_edges_added": edges_added,
                "graph_nodes": self.graph.node_count(),
                "graph_edges": self.graph.edge_count(),
            },
        ))

        # Final outcome
        if chosen_result is None:
            return SolveOutcome(
                ok=False, problem_id=problem_id,
                answer_pretty=None, answer_repr=None,
                problem_type=parsed.problem_type,
                source_format=parsed.source_format,
                parsed_pretty=parsed.pretty(),
                fingerprint=fp, tool=None, approach=None, time_ms=0.0,
                total_time_ms=total_time_ms,
                error="no candidates produced an answer",
                attempts=attempts,
                candidates=[r.to_dict() for r in post_ranked],
                similar=[s.to_dict() for s in similar],
                trace=trace,
            )

        chosen_attempt = attempts[chosen_idx] if chosen_idx is not None else attempts[-1]
        return SolveOutcome(
            ok=chosen_attempt.verification_status == "verified" or chosen_attempt.success,
            problem_id=problem_id,
            answer_pretty=chosen_attempt.result_pretty,
            answer_repr=chosen_result.result_repr or None,
            problem_type=parsed.problem_type,
            source_format=parsed.source_format,
            parsed_pretty=parsed.pretty(),
            fingerprint=fp,
            tool=chosen_result.tool,
            approach=chosen_attempt.approach,
            time_ms=chosen_attempt.time_ms,
            total_time_ms=total_time_ms,
            verification_status=chosen_attempt.verification_status,
            verification_detail=chosen_attempt.verification_detail,
            error=chosen_attempt.error,
            attempts=attempts,
            candidates=[r.to_dict() for r in post_ranked],
            similar=[s.to_dict() for s in similar],
            trace=trace,
        )

    # --- helpers -------------------------------------------------------

    @staticmethod
    def _error_tag(result: ToolResult,
                   verification: VerificationResult | None) -> str | None:
        """Compact tag stored in `tool_outcomes.failure_modes_json`."""
        if not result.success and result.error:
            # Take the exception class name (before the colon) for clustering.
            head = result.error.split(":", 1)[0]
            return head.strip() or result.error[:40]
        if verification and verification.status != "verified":
            return f"verify:{verification.status}"
        return None

    @staticmethod
    def _choose_best_unverified(attempts: list[AttemptOutcome]) -> int | None:
        """When no attempt verified, prefer (in order): inconclusive
        verification > tool success without verification > tool error."""
        if not attempts:
            return None
        def key(a: AttemptOutcome) -> tuple[int, int, float]:
            v_rank = {
                "verified": 3,
                "inconclusive": 2,
                None: 1,
                "refuted": 0,
            }.get(a.verification_status, 1)
            return (v_rank, 1 if a.success else 0, -a.time_ms)
        best = max(range(len(attempts)), key=lambda i: key(attempts[i]))
        return best

    @staticmethod
    def _rank_deltas(before: list[CandidateStats],
                     after: list[CandidateStats]) -> list[str]:
        """Human-readable summary of how rank changed for each candidate."""
        idx_before = {c.approach: i for i, c in enumerate(before)}
        out: list[str] = []
        for j, c in enumerate(after):
            i = idx_before.get(c.approach)
            if i is None or i == j:
                continue
            arrow = "↑" if j < i else "↓"
            out.append(f"{c.approach} {i+1}→{j+1} {arrow}")
        return out
