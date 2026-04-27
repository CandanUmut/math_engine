# SCHEMA — SQLite tables (Phase 1)

All durable state lives in one SQLite file, by default `data/pru_math.sqlite`
(overridable via `PRU_DB_PATH`). The schema is intentionally ORM-free so any
SQLite browser can open and edit it.

## `problems`

One row per solve request received, **regardless of whether the tool succeeded**.

| column             | type    | notes |
| ------------------ | ------- | ----- |
| `id`               | INTEGER | primary key, autoincrement |
| `raw_input`        | TEXT    | exactly what the user typed |
| `source_format`    | TEXT    | `"sympy"` \| `"latex"` \| `"natural_language"` |
| `problem_type`     | TEXT    | canonical tag from `problem_types.py` |
| `parsed_expr`      | TEXT    | `sp.srepr(expr)` — reloadable |
| `parsed_pretty`    | TEXT    | `sp.sstr(expr)` — human-readable |
| `fingerprint_json` | TEXT    | full JSON fingerprint |
| `signature`        | TEXT    | 16-char hash derived from canonicalised srepr + problem_type |
| `created_at`       | TEXT    | ISO timestamp, UTC |

Indexes: `idx_problems_signature`, `idx_problems_type`.

## `attempts`

One row per tool invocation against a problem. Phase 1 produces one attempt
per problem; Phase 3 produces up to `PRU_MAX_ATTEMPTS` (default 3) when
earlier ones don't verify; Phase 4 adds cross-verification fields populated
on the chosen verified attempt.

| column                  | type    | notes |
| ----------------------- | ------- | ----- |
| `id`                    | INTEGER | primary key |
| `problem_id`            | INTEGER | FK → `problems.id`, `ON DELETE CASCADE` |
| `tool`                  | TEXT    | `"sympy"`, `"numeric"`, `"z3"`, `"wolfram"` |
| `approach`              | TEXT    | e.g. `"sympy.solve"`, `"numeric.fsolve"`, `"z3.solve"` |
| `success`               | INTEGER | 0/1 — did the tool produce a candidate |
| `result_repr`           | TEXT    | `sp.srepr` of the candidate (or list of candidates) |
| `result_pretty`         | TEXT    | human-readable candidate |
| `verification_status`   | TEXT    | `"verified"` \| `"refuted"` \| `"inconclusive"` \| `NULL` |
| `verification_detail`   | TEXT    | one-line explanation of the verifier's decision |
| `cross_verify_tool`     | TEXT    | **Phase 4**: name of the second tool that re-checked |
| `cross_verify_status`   | TEXT    | `"agree"` \| `"disagree"` \| `"inconclusive"` \| `"unsupported"` \| `NULL` |
| `cross_verify_detail`   | TEXT    | one-line explanation from the cross-verifier |
| `cross_verify_time_ms`  | REAL    | wall-time of the cross-verification call |
| `time_ms`               | REAL    | tool wall-time |
| `error`                 | TEXT    | exception message if the tool raised |
| `steps_json`            | TEXT    | JSON array of step strings emitted by the tool |
| `created_at`            | TEXT    | ISO timestamp |

Indexes: `idx_attempts_problem`, `idx_attempts_tool`. The four
`cross_verify_*` columns are populated only when the chosen attempt
verified *and* `PRU_CROSS_VERIFY=true` *and* the registry found a
second available tool that can handle the problem.

## `tool_outcomes`

Aggregated counters keyed on `(signature, tool, approach)`. The Phase 3
learner reads from this table to rank candidate approaches via UCB1.
Phase 4 populates rows for every backend (`sympy`, `numeric`, `z3`,
`wolfram`).

| column                | type    | notes |
| --------------------- | ------- | ----- |
| `signature`           | TEXT    | fingerprint signature (see above) |
| `tool`                | TEXT    | same as `attempts.tool` |
| `approach`            | TEXT    | same as `attempts.approach` |
| `n_attempts`          | INTEGER | total invocations on this signature with this approach |
| `n_success`           | INTEGER | tool returned a candidate |
| `n_verified`          | INTEGER | verifier returned `"verified"` |
| `total_time_ms`       | REAL    | running sum, for averaging |
| `failure_modes_json`  | TEXT    | **Phase 3**: JSON array of up to 8 most-recent error tags (exception class for tool failures, `verify:refuted` / `verify:inconclusive` for verifier failures) |
| `updated_at`          | TEXT    | ISO timestamp of the last upsert |

Primary key: `(signature, tool, approach)`. Upserted with
`ON CONFLICT ... DO UPDATE` so the table is a true aggregate. The
`failure_modes_json` array is read-modify-written inside the same cursor
as the upsert, so it's atomic with the counter update.

Index: `idx_outcomes_tool_approach` for the dashboard's per-approach
rollups.

## Fingerprint JSON schema

Stored as the `fingerprint_json` column. The `signature` field is also
indexed separately for fast lookups.

```json
{
  "problem_type": "solve",
  "variables": ["x"],
  "variable_count": 1,
  "node_count": 13,
  "max_depth": 4,
  "operator_counts": { "Add": 2, "Mul": 2, "Pow": 1, "Eq": 1 },
  "function_flags": {
    "trig": false, "inv_trig": false, "hyp": false,
    "log": false,  "exp": false,      "abs": false,
    "piecewise": false, "factorial": false, "gamma": false
  },
  "polynomial_degree": 2,
  "target": "x",
  "signature": "a1b2c3d4e5f6a7b8"
}
```

All keys are stable across runs. The `signature` hash is computed from
`sp.srepr(expr)` after renaming free symbols to `x0, x1, …` so that
`x**2 + x` and `y**2 + y` share a signature.

## Similarity score (Phase 2+, already implemented)

`pru_math.fingerprint.similarity(a, b) → float in [0, 1]` combines:

| component                                         | weight |
| ------------------------------------------------- | ------ |
| problem-type match (0 or 1)                       | 0.30   |
| Jaccard over operator-class names                 | 0.25   |
| Jaccard over active function-class flags          | 0.20   |
| relative variable-count closeness                 | 0.10   |
| relative node-count closeness                     | 0.15   |

The weights are documented here so they're tunable transparently rather
than hidden in code. Phase 2 consumes this score to rank neighbours.

## Relational graph (Phase 2)

The graph is a NetworkX `MultiDiGraph` persisted to a single gpickle file
at `PRU_GRAPH_PATH` (default `data/pru_graph.gpickle`). Every solve
appends to the graph and atomically rewrites the file. If the file is
corrupt at startup, it is renamed to `*.gpickle.corrupt` and a fresh
graph is created — no silent data loss.

### Node id scheme

| prefix     | kind             | example                      |
| ---------- | ---------------- | ---------------------------- |
| `p:{id}`   | problem          | `p:42`                       |
| `t:{name}` | tool             | `t:sympy`                    |
| `pt:{name}`| problem type     | `pt:solve`                   |
| `sig:{h}`  | signature cluster| `sig:a1b2c3d4e5f6a7b8`       |
| `r:{name}` | rule / identity  | `r:pythagorean_identity` (Phase 4+) |

### Edge kinds

| kind            | from | to | attrs                                      |
| --------------- | ---- | -- | ------------------------------------------ |
| `solved_by`     | p    | t  | `approach`, `success`, `verified`, `time_ms` |
| `has_type`      | p    | pt | —                                          |
| `has_signature` | p    | sig| —                                          |
| `similar_to`    | p    | p  | `weight ∈ [0, 1]` (stored both directions)  |
| `uses_rule`     | p    | r  | reserved for later phases                  |

`similar_to` edges are only stored when the similarity score is at or
above `PRU_SIM_THRESHOLD` (default 0.55). Lowering the threshold makes
the graph denser and recall higher; raising it makes queries faster.

### Cytoscape JSON

`GET /graph` and `GET /graph/around/{id}` emit the standard Cytoscape
elements format (`{ "nodes": [...], "edges": [...] }`). Each node carries
its `kind` and a short `label` plus the original problem fields when
applicable, so the frontend can render and inspect without secondary
fetches. Undirected `similar_to` edges are deduplicated on serialisation.

### Sparse-matrix path

For graphs above ~200 problem nodes, `pru_math.retrieval.find_similar_problems_sparse`
projects fingerprints into a fixed feature vector (one-hot problem type +
operator classes + function flags + normalised counts/degree) and
computes cosine similarity in one BLAS-backed `scipy.sparse` multiplication.
Below 200 nodes the simple Python scan is faster, and the sparse path
falls back to it.

## `hypotheses` (Phase 5)

One row per hypothesis the engine has proposed. The same hypothesis is
not stored twice — the deterministic ``fingerprint`` column is UNIQUE,
so re-running ``hypothesizer.scan()`` merges any new evidence into the
existing row instead of duplicating it.

| column                | type    | notes |
| --------------------- | ------- | ----- |
| `id`                  | INTEGER | primary key |
| `kind`                | TEXT    | `"identity"` \| `"specialization"` \| `"recurring_approach"` |
| `claim`               | TEXT    | one-line human-readable claim, e.g. `"sin(x)**2 + cos(x)**2  ≡  1"` |
| `claim_repr`          | TEXT    | machine-checkable form (used by detectors and verifiers) |
| `fingerprint`         | TEXT    | SHA1(``kind|claim_repr``) truncated to 20 chars; UNIQUE |
| `evidence_json`       | TEXT    | structured evidence: supporting problem ids, leader stats, etc. |
| `status`              | TEXT    | `"proposed"` \| `"verified"` \| `"refuted"` \| `"inconclusive"` |
| `method`              | TEXT    | which verifier returned the status: `"sympy"` \| `"numeric"` \| `"z3"` \| `"stat"` \| NULL |
| `verification_detail` | TEXT    | one-line explanation of the verifier's decision |
| `rule_node`           | TEXT    | id of the corresponding `rule` node in the graph (if verified) |
| `created_at`          | TEXT    | ISO timestamp |
| `updated_at`          | TEXT    | ISO timestamp |

Indexes: `idx_hypotheses_status`, `idx_hypotheses_kind`. Verified
identities additionally appear in the graph as `rule` nodes named
`r:hyp_<id>` connected to every problem in their `support_problem_ids`
via `uses_rule` edges.

## Tool registry (Phase 4)

Every backend implements the `Tool` ABC in `pru_math/tools/base.py`:

```python
class Tool(ABC):
    name: str
    def is_available(self) -> bool: ...
    def candidate_approaches(self, problem_type: str) -> Sequence[str]: ...
    def can_handle(self, fingerprint: dict) -> float: ...    # in [0, 1]
    def solve_with(self, problem, approach) -> ToolResult: ...
    def can_cross_verify(self, problem) -> bool: ...
    def cross_verify(self, problem, candidate) -> CrossVerification: ...
```

The default registry contains `SymPyTool`, `NumericTool`, `Z3Tool`, and
`WolframTool`. `is_available()` is consulted once on startup and decides
whether the tool participates at all; `can_handle(fingerprint)` is
consulted on every solve and produces the confidence ordering used as
tiebreak in the learner's rank.

| tool      | available iff                                  | shines at                                |
| --------- | ---------------------------------------------- | ---------------------------------------- |
| `sympy`   | always                                         | symbolic algebra, calculus, identities   |
| `numeric` | always (scipy + numpy + mpmath are required)   | transcendental roots, definite integrals, evalf |
| `z3`      | `z3-solver` import succeeds                    | polynomial / integer / linear constraints; identity proofs (PROVE) |
| `wolfram` | `WOLFRAM_APP_ID` is set                        | breadth fallback; opaque text output |

Cross-verification picks the first available *other* tool whose
`can_cross_verify` returns True for the given problem. The Z3 tool
overrides `cross_verify` for SOLVE problems with a direct SMT proof
(substitute each candidate root, assert the residual ≠ 0, expect
unsat). The Wolfram tool's `can_cross_verify` returns False — its
opaque text output isn't suitable as a machine-checkable second opinion.

## UCB1 scoring (Phase 3)

For each candidate `(tool, approach)` the learner computes:

    value(c) = n_verified(c) / max(n_attempts(c), 1)
    bonus(c) = c_explore * sqrt(2 * ln(N + 1) / max(n_attempts(c), 1))
    score(c) = value(c) + bonus(c)

where `N = max(sum(sig_attempts), sum(type_attempts))` so exploration
still happens on a brand-new fingerprint when the problem type already
has history. The exploration constant `c_explore` is configurable via
`PRU_LEARNER_EXPLORATION` (default 1.0). Falls back to a 50% neutral
prior when neither sig- nor type-level stats exist for a candidate.

Determinism: ties are broken by lower average time, then by original
input order (so the registry's confidence sort is respected when scores
tie), then alphabetically by approach name. Same DB state + same
candidate set always yields the same rank.

## Inspecting and editing

```bash
sqlite3 data/pru_math.sqlite
# then:
.schema
SELECT id, problem_type, parsed_pretty FROM problems ORDER BY id DESC LIMIT 20;
SELECT tool, approach, verification_status, time_ms FROM attempts ORDER BY id DESC LIMIT 20;
```

The store does no ORM-level validation on raw UPDATEs. If you hand-edit
rows (e.g. to correct a mis-labelled problem), the next solve will pick
the corrected labels up without further action. The graph file is a plain
pickle — open it in a Python REPL with `pickle.load(open(path,"rb"))`
to inspect the `MultiDiGraph` directly.
