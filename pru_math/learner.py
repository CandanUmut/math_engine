"""Phase 3 learner — UCB1 ranking over historical tool outcomes.

The learner is **read-only**. The store accumulates per-(signature, tool,
approach) statistics on every solve, and the learner consumes them to
order candidate approaches when the reasoner needs to pick what to try
first. There is no separate model state; reproducing a ranking from
scratch is just rerunning the SQL.

Scoring policy (UCB1)
---------------------
For each candidate ``(tool, approach)`` we compute:

    value(c) = n_verified(c) / max(n_attempts(c), 1)
    bonus(c) = c_explore * sqrt(2 * ln(N_total + 1) / max(n_attempts(c), 1))
    score(c) = value(c) + bonus(c)

where ``N_total`` is the sum of ``n_attempts`` across all candidates at
this signature, and ``c_explore`` is a tunable constant (default 1.0).
For unseen signatures, we fall back to *problem-type-level* aggregates
(joined via ``problems.signature``) and add a stronger exploration bonus.
For approaches with no observations at any level, we use a neutral prior
(0.5 verify-rate) plus the maximum bonus.

We optimise for **verify rate**, not "tool succeeded" — a tool can
"succeed" by returning an unverified candidate. Using verification as
the reward keeps the learner aligned with the auditability goal.

Determinism: the formula has no randomness, so given the same database
state and the same candidate list, ranks are reproducible. The reasoner
seeds its trace from this exact data, so users can verify the choice.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Iterable

from .store import Store, ToolOutcomeRecord


# Tunable via env so scripts and tests can override without code changes.
_EXPLORATION_C = float(os.getenv("PRU_LEARNER_EXPLORATION", "1.0"))
_NEUTRAL_PRIOR = 0.5      # used when neither sig nor type-level stats exist
_PRIOR_PSEUDO_N = 1.0     # weight given to the prior in the bonus term


@dataclass
class CandidateStats:
    """All the numbers behind a single candidate's score."""
    tool: str
    approach: str
    # Signature-level counts (the canonical level)
    sig_attempts: int = 0
    sig_verified: int = 0
    sig_success: int = 0
    sig_total_time_ms: float = 0.0
    # Type-level counts (fallback when signature is unseen)
    type_attempts: int = 0
    type_verified: int = 0
    # Recent failure modes at this signature
    failure_modes: tuple[str, ...] = ()
    # Computed scoring fields (filled by Learner.rank)
    value: float = _NEUTRAL_PRIOR
    bonus: float = 0.0
    score: float = _NEUTRAL_PRIOR
    rationale: str = ""

    @property
    def avg_time_ms(self) -> float:
        return self.sig_total_time_ms / self.sig_attempts if self.sig_attempts else 0.0

    @property
    def verify_rate(self) -> float:
        if self.sig_attempts:
            return self.sig_verified / self.sig_attempts
        if self.type_attempts:
            return self.type_verified / self.type_attempts
        return _NEUTRAL_PRIOR

    @property
    def is_unseen(self) -> bool:
        return self.sig_attempts == 0 and self.type_attempts == 0

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "approach": self.approach,
            "sig_attempts": self.sig_attempts,
            "sig_verified": self.sig_verified,
            "sig_success": self.sig_success,
            "type_attempts": self.type_attempts,
            "type_verified": self.type_verified,
            "avg_time_ms": round(self.avg_time_ms, 2),
            "verify_rate": round(self.verify_rate, 4),
            "value": round(self.value, 4),
            "bonus": round(self.bonus, 4),
            "score": round(self.score, 4),
            "failure_modes": list(self.failure_modes),
            "rationale": self.rationale,
        }


class Learner:
    """Read-only ranker. Keeps no in-memory state — it queries the store
    every time so it always reflects the latest writes."""

    def __init__(self, store: Store, *, exploration_c: float | None = None):
        self.store = store
        self.exploration_c = (
            _EXPLORATION_C if exploration_c is None else float(exploration_c)
        )

    # --- Stats assembly -------------------------------------------------

    def stats_for(
        self,
        *,
        signature: str,
        problem_type: str,
        candidates: Iterable[tuple[str, str]],
    ) -> list[CandidateStats]:
        """Materialise per-candidate stats with both signature- and type-level
        counts. ``candidates`` is the list of ``(tool, approach)`` pairs the
        reasoner is willing to try; the learner ranks within that set."""
        sig_outcomes: dict[tuple[str, str], ToolOutcomeRecord] = {
            (o.tool, o.approach): o
            for o in self.store.get_tool_outcomes_by_signature(signature)
        }
        type_outcomes: dict[tuple[str, str], ToolOutcomeRecord] = {
            (o.tool, o.approach): o
            for o in self.store.get_tool_outcomes_by_problem_type(problem_type)
        }

        out: list[CandidateStats] = []
        for tool, approach in candidates:
            sig = sig_outcomes.get((tool, approach))
            typ = type_outcomes.get((tool, approach))
            out.append(CandidateStats(
                tool=tool, approach=approach,
                sig_attempts=sig.n_attempts if sig else 0,
                sig_verified=sig.n_verified if sig else 0,
                sig_success=sig.n_success if sig else 0,
                sig_total_time_ms=sig.total_time_ms if sig else 0.0,
                type_attempts=typ.n_attempts if typ else 0,
                type_verified=typ.n_verified if typ else 0,
                failure_modes=tuple(sig.failure_modes) if sig else (),
            ))
        return out

    # --- Scoring --------------------------------------------------------

    def _score(self, c: CandidateStats, n_total_at_sig: int) -> None:
        # Pick the level with data.
        if c.sig_attempts > 0:
            value = c.sig_verified / c.sig_attempts
            n = c.sig_attempts
            level = "sig"
        elif c.type_attempts > 0:
            value = c.type_verified / c.type_attempts
            n = c.type_attempts
            level = "type"
        else:
            value = _NEUTRAL_PRIOR
            n = _PRIOR_PSEUDO_N
            level = "prior"

        # UCB1 exploration bonus. Use the signature-level total trials so
        # candidates that are unseen at this signature get more pull.
        n_eff = max(n_total_at_sig, 1)
        bonus = self.exploration_c * math.sqrt(2.0 * math.log(n_eff + 1) / n)

        c.value = float(value)
        c.bonus = float(bonus)
        c.score = c.value + c.bonus

        if level == "sig":
            c.rationale = (
                f"sig {c.sig_verified}/{c.sig_attempts} verified "
                f"({c.value:.0%}) + UCB bonus {c.bonus:.2f}"
            )
        elif level == "type":
            c.rationale = (
                f"type {c.type_verified}/{c.type_attempts} verified "
                f"({c.value:.0%}); never seen at this signature; "
                f"+ UCB bonus {c.bonus:.2f}"
            )
        else:
            c.rationale = (
                f"unseen — neutral prior {c.value:.0%} + UCB bonus {c.bonus:.2f}"
            )

    def rank(
        self,
        *,
        signature: str,
        problem_type: str,
        candidates: Iterable[tuple[str, str]],
    ) -> list[CandidateStats]:
        """Return the candidates sorted by descending UCB score. Ties are
        broken first by lower average time (faster wins), then by approach
        name (stable, deterministic)."""
        stats = self.stats_for(
            signature=signature, problem_type=problem_type, candidates=candidates,
        )
        # The exploration bonus scales with ln(N), where N is the total number
        # of observations across candidates. We use the larger of the
        # signature-level and type-level totals so exploration still happens
        # when a fingerprint is brand-new but the problem type has history.
        n_total = max(
            sum(s.sig_attempts for s in stats),
            sum(s.type_attempts for s in stats),
        )
        for s in stats:
            self._score(s, n_total)
        stats.sort(key=lambda s: (-s.score, s.avg_time_ms, s.approach))
        return stats
