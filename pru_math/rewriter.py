"""Phase 9 rewrite-based search.

Verified identity hypotheses get a second life as **rewriting rules**.
When the primary multi-attempt loop fails to produce a verified result,
the reasoner can ask this module: "given this problem, are there
verified identities I can use to rewrite it into a form one of my tools
can handle?" If yes, the rewrite is tried as an extra attempt; the
verifier still checks the answer against the *original* problem so the
audit story stays clean.

Design rules
------------

- **One rule, one rewrite, one match** per attempt. We don't search a
  rewrite tree; we just try the top-K applicable rules each as a
  single-step rewrite. Combinatorial chains are out of scope for now.
- **Both directions**. ``A ≡ B`` is symmetric, so we try ``A → B`` and
  ``B → A`` as separate candidates. The cap (``max_rewrite_attempts``,
  default 2) bounds the total.
- **Variable-agnostic matching**. Free symbols in the rule's LHS become
  ``sp.Wild`` so ``sin(x)**2 + cos(x)**2 ≡ 1`` matches ``sin(y)**2 +
  cos(y)**2`` without any per-symbol configuration.
- **Verification is unchanged**. We hand the rewritten problem through
  the same toolchain; whatever answer comes out is verified against the
  original parsed problem (not the rewrite), so the verifier confirms
  identity-preservation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import sympy as sp

from .parser import ParsedProblem
from .store import HypothesisRecord, Store


@dataclass
class RewriteRule:
    """A verified identity rendered as a one-shot rewriting rule.

    Each rule carries the original hypothesis id (for trace / graph
    cross-references) plus a pre-built SymPy ``Wild``-based pattern
    we can hand straight to ``expr.replace``.
    """

    rule_id: int                       # hypothesis id
    lhs_pretty: str
    rhs_pretty: str
    lhs_pattern: sp.Basic               # uses Wild('_w0', '_w1', ...)
    rhs_template: sp.Basic              # uses the same Wilds
    direction: str = "lhs_to_rhs"       # also: "rhs_to_lhs"

    def apply(self, target: sp.Basic) -> sp.Basic | None:
        """Try to apply this rule to ``target``. Returns the rewritten
        expression on a successful match, or ``None`` if the rule
        doesn't apply (no structural change).

        Two phases:

        1. Direct ``target.replace(pattern, template)`` — works when the
           pattern appears as an entire sub-tree.
        2. For Add-shaped patterns, fall back to "subset of an Add":
           append a ``Wild('_rest')`` to the pattern and rebuild the
           result as ``template + rest``. This catches things like
           rewriting ``sin(x)**2 + cos(x)**2`` inside the larger Add
           ``sin(x)**2 + cos(x)**2 - 1``, which SymPy's plain ``replace``
           does not.

        We use ``sp.srepr`` for the no-op check — *not*
        ``sp.simplify(rewritten - target) == 0`` — because every
        verified rule is equivalence-preserving by construction.
        """
        # Phase 1: direct replace.
        try:
            rewritten = target.replace(self.lhs_pattern, self.rhs_template)
        except Exception:
            rewritten = target
        if rewritten is not None and sp.srepr(rewritten) != sp.srepr(target):
            return rewritten

        # Phase 2: Add-subset matching for sum patterns.
        if isinstance(self.lhs_pattern, sp.Add):
            extended = self._apply_add_subset(target)
            if extended is not None:
                return extended
        return None

    def _apply_add_subset(self, target: sp.Basic) -> sp.Basic | None:
        """Try to match the rule's Add-pattern as a subset of any Add
        in ``target``. Walks every sub-expression of the target that's
        itself an Add and is at least as wide as the pattern."""
        rest = sp.Wild("_rest")
        extended_pattern = self.lhs_pattern + rest
        # SymPy's match is per-expression, not recursive — so walk the
        # tree ourselves looking for Add nodes wide enough to contain
        # the pattern.
        for sub in sp.preorder_traversal(target):
            if not isinstance(sub, sp.Add):
                continue
            try:
                m = sub.match(extended_pattern)
            except Exception:
                continue
            if not m:
                continue
            rebuilt = self.rhs_template.xreplace(m)
            if rest in m:
                rebuilt = rebuilt + m[rest]
            try:
                rewritten = target.xreplace({sub: rebuilt})
            except Exception:
                continue
            if sp.srepr(rewritten) == sp.srepr(target):
                continue
            return rewritten
        return None


def _to_pattern(expr: sp.Basic) -> tuple[sp.Basic, dict[sp.Symbol, sp.Wild]]:
    """Build a Wild-based pattern from an expression. Returns the
    pattern and the symbol→Wild mapping used (so the same mapping can
    be applied to the RHS template)."""
    free = sorted(getattr(expr, "free_symbols", set()), key=lambda s: s.name)
    mapping = {s: sp.Wild(f"_w{i}") for i, s in enumerate(free)}
    if mapping:
        return expr.xreplace(mapping), mapping
    return expr, mapping


def _direction_valid(source: sp.Basic, target: sp.Basic) -> bool:
    """A rewrite ``source → target`` is well-formed only when every
    free symbol in ``target`` also appears in ``source`` — otherwise
    the rewrite would conjure an unbound variable out of nowhere."""
    s_syms = getattr(source, "free_symbols", set())
    t_syms = getattr(target, "free_symbols", set())
    return t_syms.issubset(s_syms)


def _build_rule_pair(
    rule_id: int, lhs_pretty: str, rhs_pretty: str,
) -> list[RewriteRule]:
    """Build directions of a verified identity that are well-formed
    rewrites. Each direction is only included when the target side's
    free symbols are a subset of the source side's — otherwise applying
    the rule would substitute symbols the pattern never bound."""
    try:
        lhs = sp.sympify(lhs_pretty)
        rhs = sp.sympify(rhs_pretty)
    except Exception:
        return []
    out: list[RewriteRule] = []

    # LHS → RHS
    if _direction_valid(lhs, rhs):
        pat, mapping = _to_pattern(lhs)
        template = rhs.xreplace(mapping) if mapping else rhs
        if pat != template:
            out.append(RewriteRule(
                rule_id=rule_id,
                lhs_pretty=lhs_pretty, rhs_pretty=rhs_pretty,
                lhs_pattern=pat, rhs_template=template,
                direction="lhs_to_rhs",
            ))

    # RHS → LHS (the identity is symmetric, so try the reverse too)
    if _direction_valid(rhs, lhs):
        pat2, mapping2 = _to_pattern(rhs)
        template2 = lhs.xreplace(mapping2) if mapping2 else lhs
        if pat2 != template2:
            out.append(RewriteRule(
                rule_id=rule_id,
                lhs_pretty=rhs_pretty, rhs_pretty=lhs_pretty,
                lhs_pattern=pat2, rhs_template=template2,
                direction="rhs_to_lhs",
            ))
    return out


def load_rules_from_store(store: Store, *, limit: int = 200) -> list[RewriteRule]:
    """Materialise every verified-identity hypothesis as a pair of
    :class:`RewriteRule` objects (one per direction)."""
    records = store.list_hypotheses(status="verified", kind="identity",
                                     limit=limit)
    out: list[RewriteRule] = []
    for h in records:
        ev = h.evidence or {}
        lhs = ev.get("lhs_pretty")
        rhs = ev.get("rhs_pretty")
        if not lhs or not rhs:
            continue
        out.extend(_build_rule_pair(int(h.id), str(lhs), str(rhs)))
    return out


@dataclass
class Rewrite:
    """One candidate rewrite produced by :func:`generate_rewrites`."""

    rule: RewriteRule
    rewritten: sp.Basic                # the rewritten target expression
    parsed: ParsedProblem               # a fresh ParsedProblem for the rewrite

    def to_trace_dict(self) -> dict:
        return {
            "rule_id": self.rule.rule_id,
            "direction": self.rule.direction,
            "from": str(self.rule.lhs_pretty),
            "to": str(self.rule.rhs_pretty),
            "rewritten_expr": sp.sstr(self.rewritten),
        }


def _rewrite_target_expr(parsed: ParsedProblem, new_inner: sp.Basic) -> sp.Basic:
    """Return a top-level expression for the rewritten problem. Most
    problem types just swap the bare expression; SOLVE wraps it back
    into an Eq(_, 0) since the original probably did too."""
    expr = parsed.expression
    if isinstance(expr, sp.Equality):
        # The rule was matched against (lhs - rhs); rebuild the equation.
        return sp.Eq(new_inner, 0)
    return new_inner


def _problem_inner(parsed: ParsedProblem) -> sp.Basic:
    """The expression we hand to a rule for matching. For SOLVE, that's
    ``lhs - rhs``; for everything else, the bare expression.

    Importantly we do **not** call ``sp.simplify`` here: the whole point
    of the rewriter is to expose patterns that SymPy hasn't already
    eaten. Pre-simplifying ``sin(x)**2 + cos(x)**2 - 1`` would collapse
    it to ``0`` before any rule has a chance to match the trig sum.
    """
    expr = parsed.expression
    if isinstance(expr, sp.Equality):
        return expr.lhs - expr.rhs
    return expr


def generate_rewrites(
    parsed: ParsedProblem,
    rules: Iterable[RewriteRule],
    *,
    max_rewrites: int = 2,
) -> list[Rewrite]:
    """Apply each rule (in order) to the problem and collect distinct
    rewrites. Caps at ``max_rewrites`` to bound the search.

    The rewritten expression is wrapped back into a fresh
    :class:`ParsedProblem` (same problem_type, target_symbol) so the
    reasoner can hand it to the registry exactly like a primary attempt.
    """
    inner = _problem_inner(parsed)
    seen: set[str] = {sp.srepr(inner)}
    out: list[Rewrite] = []
    for rule in rules:
        if len(out) >= max_rewrites:
            break
        candidate = rule.apply(inner)
        if candidate is None:
            continue
        key = sp.srepr(candidate)
        if key in seen:
            continue
        seen.add(key)
        new_top = _rewrite_target_expr(parsed, candidate)
        new_parsed = ParsedProblem(
            raw_input=f"# rewritten via rule #{rule.rule_id}: {parsed.raw_input}",
            source_format=parsed.source_format,
            problem_type=parsed.problem_type,
            expression=new_top,
            target_symbol=parsed.target_symbol,
            extra=(parsed.extra or {}) | {"rewritten_from_rule": rule.rule_id},
        )
        out.append(Rewrite(rule=rule, rewritten=candidate, parsed=new_parsed))
    return out
