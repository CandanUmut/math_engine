"""Reasoner orchestrator.

Phase 4 flow:

    parse → fingerprint → retrieval → rank approaches (across all tools) →
        try (≤budget):
            tool_call → verify → persist attempt
            stop early on verified
        → cross_verify (optional, second tool re-checks the answer)
        → graph_update → emit trace

The decision is grounded in the data: the learner reads ``tool_outcomes``
from the store, scores every candidate approach with UCB1, and the
reasoner tries them in that order. Every attempt — successful or not —
is persisted, and the next solve sees the updated stats.

Phase 4 widens the candidate set from "SymPy approaches only" to "every
approach from every available tool" via :class:`pru_math.tools.ToolRegistry`.
Cross-verification re-runs the chosen problem through a *different* tool
(numeric vs. SymPy, Z3 vs. SymPy, ...) and stores ``agree`` /
``disagree`` / ``inconclusive`` on the attempt row.

The trace surfaces the candidate table, the chosen approach, every
intermediate failure, the stat deltas, and the cross-verification
outcome so a user can audit not just "what was the answer" but "why did
the system try this first" and "did a second tool back it up".
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
from . import settings as runtime_settings
from .store import Store
from .tools import sympy_tool
from .tools.base import CrossVerification, ToolResult
from .tools.registry import ToolRegistry, default_registry
from .verifier import VerificationResult, verify


@dataclass
class TraceStep:
    """A single step in the reasoning trace, surfaced to the UI."""

    kind: str   # parse | fingerprint | retrieval | decision | tool_call |
                # verify | cross_verify | persist | learn | graph_update
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class AttemptOutcome:
    """One iteration of the multi-attempt loop."""

    tool: str
    approach: str
    success: bool
    result_pretty: str | None
    verification_status: str | None
    verification_detail: str | None
    time_ms: float
    error: str | None
    attempt_id: int | None       # primary key in `attempts` table
    cross_verify_tool: str | None = None
    cross_verify_status: str | None = None
    cross_verify_detail: str | None = None


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
                 registry: ToolRegistry | None = None,
                 max_attempts: int | None = None,
                 cross_verify: bool | None = None,
                 hypothesizer: Any | None = None):
        self.store = store or Store()
        self.graph = graph or RelationalGraph()
        self.learner = learner or Learner(self.store)
        self.registry = registry or default_registry()
        # Phase 6: explicit constructor overrides win; otherwise we read
        # from runtime settings on every solve so a PUT /config flip
        # takes effect immediately.
        self._explicit_max_attempts = max_attempts
        self._explicit_cross_verify = cross_verify
        # Phase 6 also threads in the hypothesizer for auto-scan, lazily
        # created on first use so a Reasoner without a hypothesizer dep
        # still works (test suites often pass none).
        self._hypothesizer = hypothesizer
        self._solves_since_scan = 0

    @property
    def max_attempts(self) -> int:
        if self._explicit_max_attempts is not None:
            return max(1, int(self._explicit_max_attempts))
        return max(1, int(runtime_settings.get("max_attempts")))

    @property
    def cross_verify(self) -> bool:
        if self._explicit_cross_verify is not None:
            return bool(self._explicit_cross_verify)
        return bool(runtime_settings.get("cross_verify"))

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
            fp, graph=self.graph, store=self.store, k=runtime_settings.get("similar_top_k"),
        )
        if similar:
            top_summary = ", ".join(
                f"#{s.problem.id} ({s.score:.2f})" for s in similar[:3]
            )
            trace.append(TraceStep(
                kind="retrieval",
                summary=f"Found {len(similar)} similar past problem(s): {top_summary}",
                detail={
                    "k": runtime_settings.get("similar_top_k"),
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
                detail={"k": runtime_settings.get("similar_top_k"), "neighbours": []},
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

        # 5. Rank approaches across every available tool.
        registry_candidates = self.registry.candidates_for(
            problem_type=parsed.problem_type, fingerprint=fp,
        )
        candidates: list[tuple[str, str]] = [
            c.to_pair() for c in registry_candidates
        ]
        # Confidence map so the trace can show what each tool said about
        # itself before the learner re-ordered them.
        confidence_map = {(c.tool, c.approach): c.confidence
                          for c in registry_candidates}
        ranked: list[CandidateStats] = self.learner.rank(
            signature=fp["signature"],
            problem_type=parsed.problem_type,
            candidates=candidates,
        )
        trace.append(TraceStep(
            kind="decision",
            summary=(
                f"Ranked {len(ranked)} approach(es) across "
                f"{len({c.tool for c in registry_candidates})} tool(s): "
                + ", ".join(
                    f"{r.tool}.{r.approach.split('.', 1)[-1]} ({r.score:.2f})"
                    for r in ranked[:5]
                )
            ),
            detail={
                "candidates": [
                    {**r.to_dict(),
                     "confidence": confidence_map.get((r.tool, r.approach), None)}
                    for r in ranked
                ],
                "policy": "UCB1",
                "exploration_c": self.learner.exploration_c,
                "max_attempts": self.max_attempts,
                "tools_available": [t.name for t in self.registry.available_tools()],
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

            result = self.registry.solve_with(
                tool=tool_name, approach=approach_name, problem=parsed,
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
                tool=result.tool,
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

        # 6b. Cross-verification (Phase 4). Only fires when the chosen
        #     attempt verified — there's no point cross-checking a refuted
        #     or inconclusive answer.
        if (
            self.cross_verify
            and chosen_idx is not None
            and chosen_result is not None
            and chosen_verification
            and chosen_verification.status == "verified"
        ):
            second_tool = self.registry.pick_cross_verifier(
                primary_tool=chosen_result.tool, problem=parsed,
            )
            if second_tool is None:
                trace.append(TraceStep(
                    kind="cross_verify",
                    summary="Cross-verify enabled but no second tool can handle this problem.",
                    detail={"primary_tool": chosen_result.tool},
                ))
            else:
                cv: CrossVerification = second_tool.cross_verify(
                    parsed, chosen_result.result,
                )
                trace.append(TraceStep(
                    kind="cross_verify",
                    summary=f"Cross-checked with {cv.tool}: {cv.status}"
                            + (f" — {cv.detail}" if cv.detail else ""),
                    detail={
                        "primary_tool": chosen_result.tool,
                        "primary_approach": chosen_result.approach,
                        "tool": cv.tool,
                        "status": cv.status,
                        "detail": cv.detail,
                        "time_ms": cv.time_ms,
                    },
                ))
                if chosen_attempt_id is not None:
                    self.store.update_cross_verify(
                        attempt_id=chosen_attempt_id,
                        tool=cv.tool, status=cv.status,
                        detail=cv.detail or None, time_ms=cv.time_ms,
                    )
                if chosen_idx is not None:
                    attempts[chosen_idx].cross_verify_tool = cv.tool
                    attempts[chosen_idx].cross_verify_status = cv.status
                    attempts[chosen_idx].cross_verify_detail = cv.detail or None

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
                tool=a.tool,
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

        # Phase 6: auto-scan. When auto_scan_every_n > 0 and we've crossed
        # the threshold, run the hypothesizer in-process and surface a
        # trace step so the user sees what was discovered.
        self._maybe_autoscan(trace)

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

    # --- Auto-scan (Phase 6) -------------------------------------------

    def _ensure_hypothesizer(self) -> Any | None:
        """Lazily create a Hypothesizer the first time auto-scan needs it.
        Imported here to avoid a hard module-level cycle (hypothesizer
        imports verifier, which is fine; reasoner importing hypothesizer
        creates a needless dependency for users who don't auto-scan)."""
        if self._hypothesizer is None:
            try:
                from .hypothesizer import Hypothesizer
                self._hypothesizer = Hypothesizer(store=self.store, graph=self.graph)
            except Exception:
                return None
        return self._hypothesizer

    def _maybe_autoscan(self, trace: list[TraceStep]) -> None:
        try:
            every_n = int(runtime_settings.get("auto_scan_every_n"))
        except Exception:
            every_n = 0
        if every_n <= 0:
            return
        self._solves_since_scan += 1
        if self._solves_since_scan < every_n:
            return
        self._solves_since_scan = 0
        h = self._ensure_hypothesizer()
        if h is None:
            return
        try:
            results = h.scan(verify=True)
        except Exception as exc:
            trace.append(TraceStep(
                kind="auto_scan",
                summary=f"auto-scan failed: {type(exc).__name__}: {exc}",
                detail={"every_n": every_n},
            ))
            return
        verified = [r for r in results if r.status == "verified"]
        trace.append(TraceStep(
            kind="auto_scan",
            summary=(
                f"auto-scan: {len(results)} hypothesis(es), "
                f"{len(verified)} verified"
            ),
            detail={
                "every_n": every_n,
                "items": [r.to_dict() for r in results[:8]],
            },
        ))
