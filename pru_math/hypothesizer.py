"""Phase 5 hypothesizer — propose, verify, and persist new identities,
shortcuts, and routing specialisations from graph + store data.

This is the "generative meets fact-based" layer. The detectors scan
existing problems, attempts, and tool_outcomes for patterns that suggest
a useful claim; the verifier runs each claim through SymPy / numeric
sampling / Z3 (where applicable) and records the outcome. Verified
claims are materialised as ``rule`` nodes in the relational graph with
``uses_rule`` edges back to the supporting problems. Refuted hypotheses
are kept too — the graph remembers what didn't work.

The detectors are deliberately bounded and deterministic:

- ``detect_specializations`` — for each problem type, is one tool the
  obvious choice? (verify-rate ≥ ``MIN_SPEC_RATE``, n ≥ ``MIN_N``)
- ``detect_recurring_approaches`` — within a signature class, is one
  approach dominating? (same threshold)
- ``detect_identities`` — pairs of distinct problems whose verified
  results simplify to the same canonical form ⇒ candidate identity

Each detector returns a list of :class:`Hypothesis` objects with a
deterministic ``fingerprint`` so re-running ``scan`` adds evidence to
existing hypotheses rather than duplicating them.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from typing import Any, Sequence

import sympy as sp

from . import problem_types as PT
from .graph import RelationalGraph, rule_node
from .store import HypothesisRecord, Store
from .verifier import _verify_identity   # noqa: SLF001 — intentional reuse


# Thresholds — tunable via env in a future revision.
MIN_N = 3                  # minimum observations before we trust a stat
MIN_SPEC_RATE = 0.70       # tool needs >=70% verify rate to "specialise"
MIN_RECURRING_RATE = 0.80  # approach needs >=80% verify rate at sig-class
MAX_PROBLEMS_FOR_IDENTITIES = 200  # cap O(n) input for the identity scan


KIND_SPECIALIZATION = "specialization"
KIND_RECURRING = "recurring_approach"
KIND_IDENTITY = "identity"

STATUS_PROPOSED = "proposed"
STATUS_VERIFIED = "verified"
STATUS_REFUTED = "refuted"
STATUS_INCONCLUSIVE = "inconclusive"


@dataclass
class Hypothesis:
    """A proposal the hypothesizer is willing to defend with evidence."""

    kind: str
    claim: str
    claim_repr: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_PROPOSED
    method: str | None = None
    verification_detail: str | None = None
    fingerprint: str = ""
    persisted_id: int | None = None

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = _fingerprint(self.kind, self.claim_repr or self.claim)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.persisted_id,
            "kind": self.kind,
            "claim": self.claim,
            "claim_repr": self.claim_repr,
            "evidence": self.evidence,
            "status": self.status,
            "method": self.method,
            "verification_detail": self.verification_detail,
            "fingerprint": self.fingerprint,
        }


def _fingerprint(kind: str, claim_repr: str) -> str:
    h = hashlib.sha1(f"{kind}|{claim_repr}".encode("utf-8")).hexdigest()
    return h[:20]


def _canonical(expr_repr: str) -> str | None:
    """Return a stable canonical sp.srepr after sympy.simplify, with free
    symbols renamed to x0, x1, ... so structurally-equivalent results
    collapse together. Returns ``None`` if the input can't be parsed."""
    try:
        e = sp.sympify(expr_repr)
    except Exception:
        return None
    try:
        e = sp.simplify(e)
    except Exception:
        # simplify can be heavy; if it gives up, use the input unchanged.
        pass
    free = sorted(getattr(e, "free_symbols", set()), key=lambda s: s.name)
    mapping = {s: sp.Symbol(f"x{i}") for i, s in enumerate(free)}
    try:
        e = e.xreplace(mapping) if mapping else e
        return sp.srepr(e)
    except Exception:
        return None


def record_to_hypothesis(rec: HypothesisRecord) -> Hypothesis:
    return Hypothesis(
        kind=rec.kind, claim=rec.claim, claim_repr=rec.claim_repr,
        evidence=dict(rec.evidence), status=rec.status, method=rec.method,
        verification_detail=rec.verification_detail,
        fingerprint=rec.fingerprint, persisted_id=rec.id,
    )


class Hypothesizer:
    """Read store + graph; propose, verify, and persist hypotheses."""

    def __init__(self, store: Store, graph: RelationalGraph):
        self.store = store
        self.graph = graph

    # Detectors and verifier are added in subsequent edits.
    # Public entrypoint:
    def scan(self, *, verify: bool = True) -> list[Hypothesis]:
        """Run every detector, persist (with evidence merging), optionally
        run the verification pipeline, and return the resulting list."""
        proposals: list[Hypothesis] = []
        proposals.extend(self.detect_specializations())
        proposals.extend(self.detect_recurring_approaches())
        proposals.extend(self.detect_identities())

        out: list[Hypothesis] = []
        for h in proposals:
            persisted = self._persist(h)
            if verify and persisted.status == STATUS_PROPOSED:
                self.verify(persisted)
            out.append(persisted)
        return out

    # The methods below are filled in by later edits.
    def detect_specializations(self) -> list[Hypothesis]:
        """For each problem type, propose a "route to tool X first" claim
        when one tool dominates verified attempts at that type."""
        # Aggregate (problem_type, tool) -> (n_verified, n_attempts, problem_ids)
        type_to_tool: dict[tuple[str, str], dict[str, Any]] = {}
        with self.store._cursor() as cur:   # noqa: SLF001
            rows = cur.execute(
                """
                SELECT p.problem_type AS pt, o.tool AS tool,
                       SUM(o.n_attempts) AS n, SUM(o.n_verified) AS v
                FROM tool_outcomes o
                JOIN problems p ON p.signature = o.signature
                GROUP BY p.problem_type, o.tool
                """,
            ).fetchall()
        out: list[Hypothesis] = []
        # Group by problem_type so we can identify the leader.
        by_type: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_type.setdefault(r["pt"], []).append({
                "tool": r["tool"],
                "n": int(r["n"] or 0),
                "v": int(r["v"] or 0),
            })
        for pt, tools in by_type.items():
            if not tools:
                continue
            # Need at least MIN_N total verified attempts across all tools to
            # have an opinion about which one dominates.
            total_v = sum(t["v"] for t in tools)
            if total_v < MIN_N:
                continue
            best = max(tools, key=lambda t: (t["v"], t["n"]))
            if best["n"] < MIN_N or best["v"] / max(best["n"], 1) < MIN_SPEC_RATE:
                continue
            # Avoid trivial claims when there's only one candidate tool.
            if len(tools) == 1:
                continue
            evidence = {
                "problem_type": pt,
                "leader": {"tool": best["tool"],
                           "verified": best["v"], "attempts": best["n"]},
                "others": [t for t in tools if t["tool"] != best["tool"]],
            }
            claim = (
                f"For problem_type={pt}, prefer tool {best['tool']} "
                f"({best['v']}/{best['n']} verified)"
            )
            claim_repr = f"specialization:{pt}->{best['tool']}"
            out.append(Hypothesis(
                kind=KIND_SPECIALIZATION,
                claim=claim, claim_repr=claim_repr,
                evidence=evidence,
            ))
        return out

    def detect_recurring_approaches(self) -> list[Hypothesis]:
        """Within a signature class, propose "this approach dominates" when
        one approach has the lion's share of verified attempts."""
        out: list[Hypothesis] = []
        with self.store._cursor() as cur:   # noqa: SLF001
            sigs = cur.execute(
                "SELECT DISTINCT signature FROM tool_outcomes",
            ).fetchall()
        for row in sigs:
            sig = row["signature"]
            outcomes = self.store.get_tool_outcomes_by_signature(sig)
            if len(outcomes) <= 1:
                continue
            total_v = sum(o.n_verified for o in outcomes)
            if total_v < MIN_N:
                continue
            best = max(outcomes,
                       key=lambda o: (o.n_verified, o.verify_rate, o.n_attempts))
            if best.n_attempts < MIN_N:
                continue
            if best.verify_rate < MIN_RECURRING_RATE:
                continue
            evidence = {
                "signature": sig,
                "leader": {
                    "tool": best.tool, "approach": best.approach,
                    "verified": best.n_verified, "attempts": best.n_attempts,
                    "avg_time_ms": round(best.avg_time_ms, 2),
                },
                "others": [
                    {"tool": o.tool, "approach": o.approach,
                     "verified": o.n_verified, "attempts": o.n_attempts}
                    for o in outcomes if o.approach != best.approach
                ],
            }
            claim = (
                f"For signature {sig[:8]}, prefer {best.approach} "
                f"({best.n_verified}/{best.n_attempts} verified)"
            )
            claim_repr = f"recurring:{sig}->{best.tool}.{best.approach}"
            out.append(Hypothesis(
                kind=KIND_RECURRING,
                claim=claim, claim_repr=claim_repr,
                evidence=evidence,
            ))
        return out

    def detect_identities(self) -> list[Hypothesis]:
        """Group verified SIMPLIFY/EXPAND/FACTOR results by their canonical
        form. Pairs of *distinct* parsed inputs that produced the same
        canonical result are candidate identities lhs ≡ rhs.

        We restrict to types where the result is meant to be an alternative
        form of the input (SIMPLIFY/EXPAND/FACTOR) — for SOLVE the result
        is a root list, for INTEGRATE/DIFFERENTIATE it's a different
        expression, so equality there isn't an identity in the same sense.
        """
        target_types = {PT.SIMPLIFY, PT.EXPAND, PT.FACTOR}
        # Pull verified attempts joined to problems within the target types.
        with self.store._cursor() as cur:   # noqa: SLF001
            rows = cur.execute(
                """
                SELECT p.id AS pid, p.problem_type AS pt, p.parsed_expr AS expr,
                       p.parsed_pretty AS pretty, a.result_repr AS result
                FROM attempts a
                JOIN problems p ON p.id = a.problem_id
                WHERE a.verification_status = 'verified'
                  AND a.result_repr IS NOT NULL
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (MAX_PROBLEMS_FOR_IDENTITIES,),
            ).fetchall()

        # Group by canonicalised result
        by_canonical: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            if r["pt"] not in target_types:
                continue
            try:
                canon = _canonical(r["result"])
            except Exception:
                continue
            if canon is None:
                continue
            entry = {
                "pid": int(r["pid"]),
                "expr": r["expr"],
                "pretty": r["pretty"],
            }
            by_canonical.setdefault(canon, []).append(entry)

        out: list[Hypothesis] = []
        for canon, members in by_canonical.items():
            # Group members by their parsed_expr (some problems are
            # literally the same input solved more than once).
            unique: dict[str, dict[str, Any]] = {}
            for m in members:
                if m["expr"] not in unique:
                    unique[m["expr"]] = m
                else:
                    # Track all problem ids supporting this side of the pair
                    unique[m["expr"]].setdefault("supports", set()).add(m["pid"])
                    unique[m["expr"]]["supports"].add(unique[m["expr"]]["pid"])
            distinct = list(unique.values())
            if len(distinct) < 2:
                continue
            # Generate pairwise hypotheses; keep the one with the most
            # support per pair so we aren't drowning in trivia. Cap pairs
            # per cluster to something manageable.
            for a, b in itertools.combinations(distinct, 2):
                if len(out) >= 50:
                    return out
                lhs_pretty = a["pretty"]
                rhs_pretty = b["pretty"]
                if lhs_pretty == rhs_pretty:
                    continue
                lhs_repr, rhs_repr = sorted([a["expr"], b["expr"]])
                supports_a = list(a.get("supports", {a["pid"]}))
                supports_b = list(b.get("supports", {b["pid"]}))
                evidence = {
                    "lhs_pretty": lhs_pretty,
                    "rhs_pretty": rhs_pretty,
                    "common_canonical": canon[:60] + ("…" if len(canon) > 60 else ""),
                    "support_problem_ids": sorted(set(supports_a) | set(supports_b)),
                }
                claim = f"{lhs_pretty}  ≡  {rhs_pretty}"
                claim_repr = f"identity:{lhs_repr}<=>{rhs_repr}"
                out.append(Hypothesis(
                    kind=KIND_IDENTITY,
                    claim=claim, claim_repr=claim_repr,
                    evidence=evidence,
                ))
        return out

    def verify(self, h: Hypothesis) -> Hypothesis:
        """Dispatch verification by kind. Mutates ``h`` and writes back to
        the store, including a graph rule node for verified identities.
        """
        if h.kind == KIND_IDENTITY:
            status, method, detail = self._verify_identity(h)
        elif h.kind in (KIND_SPECIALIZATION, KIND_RECURRING):
            status, method, detail = self._verify_stat(h)
        else:
            status, method, detail = STATUS_INCONCLUSIVE, None, "unknown kind"

        rule_node_id: str | None = None
        if status == STATUS_VERIFIED and h.kind == KIND_IDENTITY and h.persisted_id:
            rule_node_id = self._materialise_rule_node(h)

        if h.persisted_id is not None:
            self.store.update_hypothesis_status(
                hypothesis_id=h.persisted_id,
                status=status, method=method, verification_detail=detail,
                rule_node=rule_node_id,
            )
        h.status, h.method, h.verification_detail = status, method, detail
        return h

    # --- verifiers per kind --------------------------------------------

    def _verify_identity(self, h: Hypothesis) -> tuple[str, str | None, str]:
        """Try simplify, then numeric sampling, then Z3 (if available)."""
        # Pull lhs / rhs out of evidence rather than reparsing the claim.
        lhs_pretty = (h.evidence or {}).get("lhs_pretty")
        rhs_pretty = (h.evidence or {}).get("rhs_pretty")
        if not lhs_pretty or not rhs_pretty:
            return STATUS_INCONCLUSIVE, None, "missing lhs/rhs in evidence"
        try:
            lhs = sp.sympify(lhs_pretty)
            rhs = sp.sympify(rhs_pretty)
        except Exception as exc:
            return STATUS_INCONCLUSIVE, None, f"sympify failed: {exc}"

        # 1) symbolic simplify
        try:
            if sp.simplify(lhs - rhs) == 0:
                return STATUS_VERIFIED, "sympy", "simplify(lhs - rhs) reduced to 0"
        except Exception as exc:
            # fall through to numeric
            symbolic_detail = f"simplify raised {type(exc).__name__}"
        else:
            symbolic_detail = "simplify did not reduce to 0"

        # 2) numeric sampling via the existing identity verifier
        try:
            v = _verify_identity(lhs, rhs, seed=99)
        except Exception as exc:
            return STATUS_INCONCLUSIVE, "numeric", f"numeric check raised: {exc}"
        if v.status == "verified":
            return STATUS_VERIFIED, "numeric", v.detail
        if v.status == "refuted":
            return STATUS_REFUTED, "numeric", v.detail

        # 3) Z3 (best-effort, polynomial subset only)
        try:
            from .tools.z3_tool import _Z3_AVAILABLE, sympy_to_z3, Z3UnsupportedError
            if _Z3_AVAILABLE:
                import z3
                z_lhs = sympy_to_z3(lhs)
                z_rhs = sympy_to_z3(rhs)
                s = z3.Solver()
                s.set("timeout", 3000)
                s.add(z_lhs != z_rhs)
                res = s.check()
                if res == z3.unsat:
                    return STATUS_VERIFIED, "z3", "Z3 proved lhs == rhs (unsat negation)"
                if res == z3.sat:
                    return STATUS_REFUTED, "z3", f"Z3 counter-model: {s.model()}"
        except Z3UnsupportedError:
            pass
        except Exception:
            pass

        return STATUS_INCONCLUSIVE, "numeric", symbolic_detail + "; sampling: " + v.detail

    def _verify_stat(self, h: Hypothesis) -> tuple[str, str | None, str]:
        """For stat-style hypotheses (specialisation, recurring approach),
        re-check the underlying threshold against the *current* store
        state. Verified iff the leader still holds the criterion."""
        ev = h.evidence or {}
        if h.kind == KIND_SPECIALIZATION:
            pt = ev.get("problem_type")
            leader_tool = (ev.get("leader") or {}).get("tool")
            if not pt or not leader_tool:
                return STATUS_INCONCLUSIVE, None, "missing problem_type or leader"
            outcomes = self.store.get_tool_outcomes_by_problem_type(pt)
            if not outcomes:
                return STATUS_INCONCLUSIVE, "stat", "no current observations"
            agg: dict[str, dict[str, int]] = {}
            for o in outcomes:
                d = agg.setdefault(o.tool, {"v": 0, "n": 0})
                d["v"] += o.n_verified; d["n"] += o.n_attempts
            if leader_tool not in agg:
                return STATUS_REFUTED, "stat", f"{leader_tool} no longer present"
            leader = agg[leader_tool]
            if leader["n"] < MIN_N:
                return STATUS_INCONCLUSIVE, "stat", f"only {leader['n']} attempts"
            rate = leader["v"] / max(leader["n"], 1)
            best_other = max(
                (a for k, a in agg.items() if k != leader_tool),
                key=lambda a: a["v"], default={"v": 0, "n": 0},
            )
            if rate >= MIN_SPEC_RATE and leader["v"] >= best_other["v"]:
                return STATUS_VERIFIED, "stat", (
                    f"{leader_tool}: {leader['v']}/{leader['n']} verified "
                    f"({rate:.0%}); rivals max {best_other['v']}"
                )
            return STATUS_REFUTED, "stat", (
                f"{leader_tool} rate {rate:.0%} < threshold {MIN_SPEC_RATE:.0%} "
                f"or rival {best_other['v']} > {leader['v']}"
            )

        # KIND_RECURRING
        sig = ev.get("signature")
        leader_app = (ev.get("leader") or {}).get("approach")
        leader_tool = (ev.get("leader") or {}).get("tool")
        if not sig or not leader_app:
            return STATUS_INCONCLUSIVE, None, "missing signature or leader"
        outcomes = self.store.get_tool_outcomes_by_signature(sig)
        cur_leader = next(
            (o for o in outcomes
             if o.approach == leader_app and o.tool == leader_tool),
            None,
        )
        if not cur_leader:
            return STATUS_REFUTED, "stat", "leader no longer in tool_outcomes"
        if cur_leader.n_attempts < MIN_N:
            return STATUS_INCONCLUSIVE, "stat", f"only {cur_leader.n_attempts} attempts"
        if cur_leader.verify_rate >= MIN_RECURRING_RATE:
            return STATUS_VERIFIED, "stat", (
                f"{leader_app} verify rate {cur_leader.verify_rate:.0%} "
                f"on {cur_leader.n_attempts} attempts at signature {sig[:8]}"
            )
        return STATUS_REFUTED, "stat", (
            f"{leader_app} verify rate {cur_leader.verify_rate:.0%} < "
            f"{MIN_RECURRING_RATE:.0%}"
        )

    # --- graph integration ---------------------------------------------

    def _materialise_rule_node(self, h: Hypothesis) -> str:
        """Add a `rule` node to the graph for a verified hypothesis and
        link every supporting problem to it via `uses_rule`."""
        node_name = f"hyp_{h.persisted_id}"
        ev = h.evidence or {}
        # Reuse the existing rule-node helper from graph.py
        rn_id = rule_node(node_name)
        # Add the node with descriptive attributes; idempotent.
        self.graph.graph.add_node(
            rn_id, kind="rule", name=node_name,
            hypothesis_id=h.persisted_id,
            kind_of_rule=h.kind,
            claim=h.claim,
        )
        for pid in (ev.get("support_problem_ids") or []):
            try:
                self.graph.link_uses_rule(int(pid), node_name)
            except Exception:
                continue
        self.graph.commit()
        return rn_id

    # ------------------------------------------------------------------

    def _persist(self, h: Hypothesis) -> Hypothesis:
        hid, _was_new = self.store.upsert_hypothesis(
            kind=h.kind, claim=h.claim, claim_repr=h.claim_repr,
            fingerprint=h.fingerprint, evidence=h.evidence,
            status=h.status, method=h.method,
            verification_detail=h.verification_detail,
        )
        h.persisted_id = hid
        # Pull the current row back so we know the existing status (in case
        # we're re-proposing an already-verified one).
        rec = self.store.get_hypothesis(hid)
        if rec is not None:
            h.status = rec.status
            h.method = rec.method
            h.verification_detail = rec.verification_detail
            h.evidence = dict(rec.evidence)
        return h
