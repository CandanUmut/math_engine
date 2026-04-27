# PRU Math Engine

A learning, auditable, relational math reasoner. Not a math solver — a layer
**above** SymPy / numerical tools / Z3 that decides which tool to call, which
approach to try, and which sequence of steps to use; learns from every
problem it solves; and exposes every decision as an inspectable graph.

The thesis is the Precomputed Relational Universe (PRU) model: knowledge is
most efficiently represented as a graph of precomputed relations between
entities, so solving a new problem is a traversal over that graph rather than
a from-scratch computation. Applied to math, every problem, every tool call,
every success and failure becomes a node or edge — and new problems are
solved by retrieving similar past problems, replaying what worked, adapting,
and updating the graph.

See `PHILOSOPHY.md` for the longer version, and `SCHEMA.md` for the data model.

## Status

This repo is **Phase 4** of a 5-phase plan. The later phases are not yet
implemented; the architecture was designed so adding them is additive.

| Phase | Scope                                                   | State  |
| ----- | ------------------------------------------------------- | ------ |
| 1     | Skeleton · SymPy dispatch · verification · SQLite store | **done** |
| 2     | Relational graph · similarity-based retrieval           | **done** |
| 3     | Learning · approach ranking · reasoning trace           | **done (backend)** |
| 4     | Multi-tool orchestration (numeric · Z3 · Wolfram)       | **done (backend)** |
| 5     | Hypothesis generation · identity discovery              | planned |

> Phases 3 and 4 are backend-complete and verified end-to-end; the
> matching frontend JS for the new trace renderer / Insights charts /
> tool badges is still on Phase 2's layout. See the open `frontend/`
> TODOs in the most recent commits for the punch list.

### What Phase 1 does

- Parse a math problem in one of three formats:
  - **SymPy syntax** — `x**2 + 3*x - 4`, `Eq(x**2 - 5*x + 6, 0)`, `Integral(x**2, (x, 0, 1))`
  - **LaTeX** — `\int_{0}^{1} x^2 dx`, `\sin(x)^2 + \cos(x)^2`
  - **Natural language** — via a local Ollama model (optional; disabled if Ollama isn't running)
- Compute a deterministic structural **fingerprint** for the problem.
- Dispatch to the right SymPy routine based on problem type
  (`solve`, `integrate`, `differentiate`, `simplify`, `factor`, `expand`, `evaluate`, `limit`, `series`).
- **Verify** the answer numerically (sampling) or symbolically (back-substitution, derivative check, quadrature).
- **Persist** the problem, the attempt, the fingerprint, and the verification status to SQLite.
- Expose a FastAPI backend and a minimal dark-mode web UI that shows the answer, the reasoning trace, and the fingerprint.

### What Phase 2 adds

- A persistent **NetworkX MultiDiGraph** of typed nodes (problems, tools, problem-types, signature clusters, rules) and typed edges (`solved_by`, `has_type`, `has_signature`, `similar_to`, `uses_rule`). Atomic gpickle save on every solve; corrupt files are quarantined and a fresh graph is created.
- A **retrieval** layer (`pru_math.retrieval`) that joins the graph (fingerprints) with the SQLite store (solutions and verification status) and returns the top-K most structurally similar past problems. A scipy-sparse path is included for graphs past ~200 nodes; below that, the simple Python scan is faster.
- The reasoner now queries the graph **before** calling SymPy and surfaces the neighbours in the response and the trace. Per the Phase 2 spec, **decisions are not yet routed on the graph** — Phase 3 introduces that. Phase 2 just exposes the relations.
- A four-tab UI:
  - **Solve** — input, answer, similar-past-problems panel, full reasoning trace, fingerprint.
  - **Graph** — interactive Cytoscape view of the relational graph; toggle similar / type / tool / signature edges; click any node or edge for details.
  - **Database** — sortable, filterable raw inspectors for `problems`, `attempts`, and `tool_outcomes`. The "see the database" requirement.
  - **Insights** — per-problem-type counts, per-tool/approach verify rates, per-source-format mix.
- New endpoints: `GET /graph`, `GET /graph/around/{id}`, `GET /graph/stats`, `GET /problems/{id}/similar`, `GET /attempts`, `GET /tool_outcomes`, `GET /config`.

### What Phase 3 adds (backend complete)

- **`pru_math/learner.py`** — UCB1 ranker over per-`(signature, tool, approach)` statistics from `tool_outcomes`. Falls back to type-level aggregates joined via `problems.signature` when a fingerprint is brand new, plus a neutral prior + max bonus when neither level has data. Fully read-only; no separate model state.
- **`pru_math/tools/sympy_tool.py`** — restructured into a registry of named approaches per problem type (`sympy.solve`/`sympy.solveset`/`sympy.roots` for SOLVE, `sympy.integrate`/`sympy.integrate.meijerg`/`sympy.integrate.risch` for INTEGRATE, four flavours of simplify, etc.). Approach names are stable strings written to `attempts.approach` and `tool_outcomes.approach` so the learner can key statistics on them.
- **Reasoner multi-attempt loop**: parse → fingerprint → retrieval → rank candidates → try (≤`PRU_MAX_ATTEMPTS`, default 3) → verify → persist all attempts → graph_update → emit trace. Stops early on the first verified result.
- **Failure-mode tracking** — `tool_outcomes.failure_modes_json` records up to the last 8 distinct error tags per `(signature, tool, approach)`; populated automatically on every refuted/inconclusive/error attempt.
- **New endpoints**: `GET /learner/rank?problem_type=...&signature=...` previews the live ranker; `GET /attempts/timeline` feeds the dashboard.
- **Demonstrable learning**: solving five quadratics from a fresh DB, the engine picks `sympy.roots` for the first three (greedy), then UCB1's exploration bonus pulls `sympy.solve` ahead and tries it. Both verified.

### What Phase 4 adds (backend complete)

- **Tool registry** (`pru_math/tools/registry.py`). Every backend implements the new `Tool` ABC: `is_available()`, `candidate_approaches(problem_type)`, `can_handle(fingerprint) -> confidence`, `solve_with(problem, approach) -> ToolResult`, plus optional `cross_verify(problem, candidate)`. The reasoner asks the registry for *all* candidate `(tool, approach)` pairs; the learner ranks across the union, not just within SymPy.
- **`pru_math/tools/numeric_tool.py`** — scipy/mpmath-backed approaches: `numeric.fsolve` and `numeric.brentq` for transcendental / high-degree roots, `numeric.quad` for definite integrals, `numeric.evalf` for closed-form numerical values.
- **`pru_math/tools/z3_tool.py`** — Z3 SMT for SOLVE (real and integer domains) and PROVE (assert ¬claim, expect unsat). Translates the polynomial / rational / inequality subset of SymPy expressions; raises `Z3UnsupportedError` on transcendentals so the reasoner skips it cleanly. Implements a domain-specific `cross_verify` for SOLVE problems that substitutes each candidate root and asks Z3 to prove the residual is zero.
- **`pru_math/tools/wolfram_tool.py`** — optional HTTP backend. `is_available()` returns `False` when `WOLFRAM_APP_ID` is unset and the registry filters the tool out — no network calls are ever made without a key.
- **Cross-verification** — when `PRU_CROSS_VERIFY=true`, a verified primary result is re-checked by a *different* tool (numeric vs. SymPy, Z3 vs. SymPy, ...). The outcome (`agree` / `disagree` / `inconclusive` / `unsupported`) is persisted as new `cross_verify_*` columns on the `attempts` row and surfaced in the trace and the `attempts` API. Schema is auto-migrated on existing databases.
- **Self-confidence as tiebreak**: each tool's `can_handle(fingerprint)` returns a confidence in `[0, 1]`. The registry sorts candidates by confidence; the learner uses original input order as a deterministic tiebreaker so a cold-start problem prefers the high-confidence tool's approach.
- **`GET /tools`** lists every registered tool with its availability and class name.
- **End-to-end**: with the default registry (SymPy + numeric + Z3, Wolfram unavailable), `Integral(x**2, (x, 0, 1))` is solved by SymPy and cross-verified `agree` by numeric quadrature; quadratics are solved by `sympy.roots` and cross-verified by either numeric or Z3 depending on which is picked first.

### What's still missing

- **Frontend JS** — Phases 3 and 4 changed the response shape (`attempts[]`, `candidates[]`, `cross_verify_*` fields, `decision` / `learn` / `cross_verify` trace kinds, `/tools` endpoint), but `frontend/static/app.js` still renders the Phase 2 layout. The HTML and CSS scaffolding for the new panels and Insights charts already exist; the JS just needs to be updated.
- **Phase 5 — hypothesis generation** — proposing identities and shortcuts from graph-structure analysis; not started.

## Running it

```bash
pip install -r requirements.txt

# CLI smoke test (no server needed)
python -m pru_math "Eq(x**2 - 5*x + 6, 0)"
python -m pru_math "Integral(x**2, (x, 0, 1))"
python -m pru_math "sin(x)^2 + cos(x)^2"

# Web UI
uvicorn pru_math.api:app --reload
# open http://localhost:8000
```

Optional: copy `.env.example` to `.env` to point at a different SQLite file,
a different Ollama host, or disable Ollama.

### Ollama (natural-language input)

The natural-language path shells out to a local Ollama server. If Ollama is
not running or `OLLAMA_ENABLED=false`, the parser silently skips that path —
SymPy syntax and LaTeX still work. The LLM never decides math; it only
translates language into a SymPy-parseable expression. See
`pru_math/parser.py` for the exact prompt.

## Architecture

```
┌─────────────────────────── GUI (4 tabs) ──────────────────────────────┐
│  Solve · Graph (cytoscape) · Database · Insights (Chart.js)           │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼────────────────────────────────────┐
│                          pru_math.api (FastAPI)                       │
│   /solve   /problems   /problems/{id}/similar   /attempts             │
│   /attempts/timeline   /tool_outcomes   /learner/rank   /tools        │
│   /graph   /graph/around/{id}   /graph/stats   /db/stats   /config    │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼────────────────────────────────────┐
│                          pru_math.reasoner                            │
│  parse → fingerprint → retrieval → rank candidates →                  │
│  multi-attempt loop (≤budget) → verify → persist →                    │
│  cross_verify? → graph_update → emit trace                            │
└──┬──────────┬─────────────┬────────────┬────────────┬─────────────────┘
   │          │             │            │            │
 parser  fingerprint    retrieval     learner     verifier
                                    (UCB1, RO)
                              │           │
                              │           ▼
                              │   ┌──────────────────────┐
                              │   │   ToolRegistry       │
                              │   │  ┌────────┬────────┐ │
                              │   │  │ sympy  │numeric │ │
                              │   │  ├────────┼────────┤ │
                              │   │  │  z3    │wolfram │ │
                              │   │  └────────┴────────┘ │
                              │   └──────────┬───────────┘
                              ▼              │
                  ┌────────────────────────┐ │
                  │  RelationalGraph       │ │
                  │  (NetworkX MultiDiGraph│ │
                  │   + atomic gpickle)    │ │
                  └───────────┬────────────┘ │
                              │              │
                  ┌───────────▼──────────────▼────┐
                  │           SQLite              │
                  │ problems · attempts · graph   │
                  │ tool_outcomes (failure_modes) │
                  └───────────────────────────────┘
```

## Module map

| File                                | Purpose |
| ----------------------------------- | ------- |
| `pru_math/config.py`                | env → `Config` dataclass (db / graph / ollama / similarity threshold / top-K) |
| `pru_math/problem_types.py`         | canonical `SOLVE` / `INTEGRATE` / … tags |
| `pru_math/parser.py`                | SymPy / LaTeX / NL → `ParsedProblem` |
| `pru_math/fingerprint.py`           | structural fingerprint + similarity score (documented weights) |
| `pru_math/tools/sympy_tool.py`      | dispatches parsed problem to the right `sympy.*` call |
| `pru_math/verifier.py`              | per-type numerical / symbolic verification |
| `pru_math/store.py`                 | plain `sqlite3` wrapper (no ORM — inspect with any SQLite browser) |
| `pru_math/graph.py`                 | **Phase 2** — `RelationalGraph` (NetworkX) with persistence and cytoscape serialisation |
| `pru_math/retrieval.py`             | **Phase 2** — `find_similar_problems`, plus a sparse-matrix path for large graphs |
| `pru_math/learner.py`               | **Phase 3** — UCB1 ranker over `(signature, tool, approach)` statistics |
| `pru_math/tools/registry.py`        | **Phase 4** — `Tool` ABC + `ToolRegistry` for multi-tool orchestration |
| `pru_math/tools/numeric_tool.py`    | **Phase 4** — scipy / mpmath fallback (`numeric.fsolve`, `numeric.brentq`, `numeric.quad`, `numeric.evalf`) |
| `pru_math/tools/z3_tool.py`         | **Phase 4** — Z3 SMT backend with SymPy→Z3 translator (graceful when missing) |
| `pru_math/tools/wolfram_tool.py`    | **Phase 4** — optional HTTP backend, gated by `WOLFRAM_APP_ID` |
| `pru_math/reasoner.py`              | orchestrator; emits a structured `SolveOutcome` + trace |
| `pru_math/api.py`                   | FastAPI app; mounts the frontend |
| `pru_math/__main__.py`              | `python -m pru_math "..."` CLI |
| `frontend/`                         | static UI (no bundler — Cytoscape & Chart.js via CDN) |

## Tests

```bash
OLLAMA_ENABLED=false pytest -q
```

109 tests covering the parser (three formats), fingerprint determinism
and similarity, SymPy tool dispatch for every supported problem type,
the verifier against correct and wrong candidates, the SQLite store,
the graph (node/edge add, similarity edges, persistence round-trip,
corruption recovery, cytoscape serialisation), retrieval (basic, exclude
self, prefer-verified best-attempt, sparse-path fallback), the reasoner
end-to-end, the **tool registry** (availability filtering, confidence
ordering, cross-verifier picking), the **numeric tool** (fsolve/brentq
roots, scipy.quad, evalf), the **Z3 tool** (translator subset,
real/integer SOLVE, cross-verify agree/disagree on substituted roots,
graceful fail on transcendental input), the **Wolfram tool** (gated by
`WOLFRAM_APP_ID`, mock-based smoke), **cross-verification persistence**
(decision step lists multiple tools, cross-verify trace step exists when
enabled, `cross_verify_status` written to the attempt row, skipped cleanly
when no second tool can handle the problem), and the FastAPI layer via
`TestClient` (`/solve`, `/problems/{id}/similar`, `/graph`,
`/graph/around/{id}`, `/graph/stats`, `/attempts`, `/tool_outcomes`,
`/db/stats`).

The NL parser is exercised via a mock so the suite doesn't need a running
Ollama. Z3 tests skip cleanly when `z3-solver` is not installed.

## What this system is NOT

- Not a replacement for SymPy or Mathematica. It **uses** them.
- Not a math LLM. The LLM is used only for parsing natural-language input and (in later phases) generating explanations of reasoning traces. It never decides math.
- Not a black box. Every decision is auditable by design. If it isn't, that's a bug.
