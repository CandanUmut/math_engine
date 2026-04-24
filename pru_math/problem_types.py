"""Canonical problem-type tags used across the engine.

Problem types are stored as plain strings in SQLite and as node attributes
in the relational graph. Keep this list small and stable — downstream
learners key on these values, so renaming is a breaking change.
"""
from __future__ import annotations

SOLVE = "solve"              # solve equation or system for variable(s)
SIMPLIFY = "simplify"        # reduce expression to a canonical/simpler form
INTEGRATE = "integrate"      # definite or indefinite integral
DIFFERENTIATE = "differentiate"
FACTOR = "factor"            # polynomial factorization
EVALUATE = "evaluate"        # numerical value of an expression
EXPAND = "expand"            # polynomial / product expansion
LIMIT = "limit"              # compute a limit
SERIES = "series"            # Taylor / Laurent expansion
PROVE = "prove"              # identity / inequality / theorem (Phase 4+)
UNKNOWN = "unknown"

ALL = (
    SOLVE, SIMPLIFY, INTEGRATE, DIFFERENTIATE, FACTOR,
    EVALUATE, EXPAND, LIMIT, SERIES, PROVE, UNKNOWN,
)
