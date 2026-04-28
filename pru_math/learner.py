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
from typing import Any, Iterable

from .store import Store, ToolOutcomeRecord


# Tunable via env so scripts and tests can override without code changes.
_EXPLORATION_C = float(os.getenv("PRU_LEARNER_EXPLORATION", "1.0"))
_NEUTRAL_PRIOR = 0.5      # used when neither sig nor type-level stats exist
_PRIOR_PSEUDO_N = 1.0     # weight given to the prior in the bonus term

# Phase 7: identity-aware ranking. Every verified-rule witness on a
# similar problem adds ``_RULE_BONUS_PER_WITNESS`` to the score, capped
# at ``_RULE_BONUS_CAP``. The bonus is a small, transparent nudge —
# verify rate stays the dominant term — but it closes the loop between
# the hypothesizer's discoveries and the next solve's ranking.
_RULE_BONUS_PER_WITNESS = float(os.getenv("PRU_LEARNER_RULE_BONUS", "0.05"))
_RULE_BONUS_CAP = float(os.getenv("PRU_LEARNER_RULE_BONUS_CAP", "0.30"))


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
    rule_bonus: float = 0.0      # Phase 7: identity-aware nudge
    rule_witnesses: int = 0       # how many witnesses contributed
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
            "rule_bonus": round(self.rule_bonus, 4),
            "rule_witnesses": self.rule_witnesses,
            "score": round(self.score, 4),
            "failure_modes": list(self.failure_modes),
            "rationale": self.rationale,
        }


class Learner:
    """Read-only ranker. Keeps no in-memory state — it queries the store
    every time so it always reflects the latest writes.

    Phase 7: optionally accepts a :class:`RelationalGraph` so the
    ranking includes a small **identity-aware bonus** for approaches
    that have witnessed verified rules on problems sharing the current
    signature. The bonus is capped and transparent — it shows up as
    ``rule_bonus`` and ``rule_witnesses`` in :class:`CandidateStats`.
    """

    def __init__(self, store: Store, *,
                 exploration_c: float | None = None,
                 graph: Any | None = None):
        self.store = store
        self.graph = graph
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

    def _score(self, c: CandidateStats, n_total_at_sig: int,
               *, rule_witnesses: dict[tuple[str, str], int] | None = None
               ) -> None:
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

        # Phase 7: identity-aware bonus (capped). Looks up how many
        # verified-rule witnesses on similar problems voted for this
        # (tool, approach) pair.
        witnesses = 0
        if rule_witnesses:
            witnesses = int(rule_witnesses.get((c.tool, c.approach), 0))
        rule_bonus = min(_RULE_BONUS_CAP, _RULE_BONUS_PER_WITNESS * witnesses)

        c.value = float(value)
        c.bonus = float(bonus)
        c.rule_bonus = float(rule_bonus)
        c.rule_witnesses = int(witnesses)
        c.score = c.value + c.bonus + c.rule_bonus

        suffix = ""
        if witnesses:
            suffix = f"; rule witnesses ×{witnesses} (+{rule_bonus:.2f})"
        if level == "sig":
            c.rationale = (
                f"sig {c.sig_verified}/{c.sig_attempts} verified "
                f"({c.value:.0%}) + UCB bonus {c.bonus:.2f}{suffix}"
            )
        elif level == "type":
            c.rationale = (
                f"type {c.type_verified}/{c.type_attempts} verified "
                f"({c.value:.0%}); never seen at this signature; "
                f"+ UCB bonus {c.bonus:.2f}{suffix}"
            )
        else:
            c.rationale = (
                f"unseen — neutral prior {c.value:.0%} + UCB bonus {c.bonus:.2f}"
                f"{suffix}"
            )

    def rank(
        self,
        *,
        signature: str,
        problem_type: str,
        candidates: Iterable[tuple[str, str]],
    ) -> list[CandidateStats]:
        """Return the candidates sorted by descending UCB score. Ties are
        broken first by lower average time (faster wins), then by the
        original input order (so callers that pass candidates ordered by
        a meaningful prior — e.g. tool self-confidence — see that prior
        respected when statistics are absent), then by approach name."""
        cand_list = list(candidates)
        order_index: dict[tuple[str, str], int] = {
            pair: i for i, pair in enumerate(cand_list)
        }
        stats = self.stats_for(
            signature=signature, problem_type=problem_type, candidates=cand_list,
        )
        # The exploration bonus scales with ln(N), where N is the total number
        # of observations across candidates. We use the larger of the
        # signature-level and type-level totals so exploration still happens
        # when a fingerprint is brand-new but the problem type has history.
        n_total = max(
            sum(s.sig_attempts for s in stats),
            sum(s.type_attempts for s in stats),
        )
        # Phase 7: identity-aware nudge. Rule-witness counts come from
        # the graph; without a graph reference (e.g. tests that don't
        # need it) we just skip this and the rank reduces to plain UCB.
        rule_witnesses = self._rule_witnesses(signature)
        for s in stats:
            self._score(s, n_total, rule_witnesses=rule_witnesses)
        stats.sort(key=lambda s: (
            -s.score,
            s.avg_time_ms,
            order_index.get((s.tool, s.approach), 10_000),
            s.approach,
        ))
        return stats

    def _rule_witnesses(self, signature: str) -> dict[tuple[str, str], int]:
        if self.graph is None or not signature:
            return {}
        try:
            from .rules import witness_counts
            return witness_counts(self.graph, signature)
        except Exception:
            # Don't let a graph-traversal bug crash the reasoner; just
            # skip the bonus and continue.
            return {}
