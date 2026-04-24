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

One row per tool invocation against a problem. Phase 1 only produces one
attempt per problem (single tool, single approach); later phases may produce
several.

| column                | type    | notes |
| --------------------- | ------- | ----- |
| `id`                  | INTEGER | primary key |
| `problem_id`          | INTEGER | FK → `problems.id`, `ON DELETE CASCADE` |
| `tool`                | TEXT    | e.g. `"sympy"` |
| `approach`            | TEXT    | e.g. `"sympy.solve"`, `"sympy.integrate.doit"` |
| `success`             | INTEGER | 0/1 — did the tool produce a candidate |
| `result_repr`         | TEXT    | `sp.srepr` of the candidate (or list of candidates) |
| `result_pretty`       | TEXT    | human-readable candidate |
| `verification_status` | TEXT    | `"verified"` \| `"refuted"` \| `"inconclusive"` \| `NULL` |
| `verification_detail` | TEXT    | one-line explanation of the verifier's decision |
| `time_ms`             | REAL    | tool wall-time |
| `error`               | TEXT    | exception message if the tool raised |
| `steps_json`          | TEXT    | JSON array of step strings emitted by the tool |
| `created_at`          | TEXT    | ISO timestamp |

Indexes: `idx_attempts_problem`, `idx_attempts_tool`.

## `tool_outcomes`

Aggregated counters keyed on `(signature, tool, approach)`. The Phase 3
learner reads from this table; Phase 1 writes to it but doesn't use it for
decisions.

| column          | type    | notes |
| --------------- | ------- | ----- |
| `signature`     | TEXT    | fingerprint signature (see above) |
| `tool`          | TEXT    | same as `attempts.tool` |
| `approach`      | TEXT    | same as `attempts.approach` |
| `n_attempts`    | INTEGER | total invocations on this signature with this approach |
| `n_success`     | INTEGER | tool returned a candidate |
| `n_verified`    | INTEGER | verifier returned `"verified"` |
| `total_time_ms` | REAL    | running sum, for averaging |
| `updated_at`    | TEXT    | ISO timestamp of the last upsert |

Primary key: `(signature, tool, approach)`. Upserted with
`ON CONFLICT ... DO UPDATE` so the table is a true aggregate.

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

## Inspecting and editing the database

```bash
sqlite3 data/pru_math.sqlite
# then:
.schema
SELECT id, problem_type, parsed_pretty FROM problems ORDER BY id DESC LIMIT 20;
SELECT tool, approach, verification_status, time_ms FROM attempts ORDER BY id DESC LIMIT 20;
```

The store does no ORM-level validation on raw UPDATEs. If you hand-edit
rows (e.g. to correct a mis-labelled problem), the next solve will pick
the corrected labels up without further action.
