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

The five planned phases plus operational hardening (Phase 6) are
feature-complete. **Phase 7** closed the loop between the hypothesizer
and the learner: verified rules now influence future ranking, identity
chaining proposes new identities from existing ones, and "hard"
signature classes get flagged with their working fallback.

| Phase | Scope                                                       | State  |
| ----- | ----------------------------------------------------------- | ------ |
| 1     | Skeleton · SymPy dispatch · verification · SQLite store     | **done** |
| 2     | Relational graph · similarity-based retrieval               | **done** |
| 3     | Learning · approach ranking · reasoning trace               | **done** |
| 4     | Multi-tool orchestration (numeric · Z3 · Wolfram)           | **done** |
| 5     | Hypothesis generation · identity discovery                  | **done** |
| 6     | Operational hardening (timeouts, settings, export, autoscan)| **done** |
| 7     | Reasoning quality (identity-aware ranking, transitivity)    | **done** |
| 8     | Notebook sessions · Ollama narrator                         | **done** |

The Phase 5 success criterion — "the system has proposed and verified at
least one identity or shortcut that was not explicitly given to it" —
is met: with no special priming, solving `sin(x)**2 + cos(x)**2` and
`1` produces the verified hypothesis `sin(x)**2 + cos(x)**2 ≡ 1`,
proven by SymPy, and a corresponding `rule` node appears in the graph
with `uses_rule` edges back to the supporting problems.

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

### What Phase 8 adds

Turns the engine from "a calculator that learns" into something a researcher can use as a working notebook.

- **Sessions** — a small `sessions` table (id / title / notes_markdown / timestamps) plus a nullable `session_id` column on `problems` (auto-migrated). Group related problems together with free-form markdown notes; problems linked to a deleted session keep their data with `session_id=NULL`. Existing solves stay valid.
- **Endpoints**: `GET /sessions`, `POST /sessions`, `GET /sessions/{id}` (returns the session plus its problem list in solve order), `PUT /sessions/{id}`, `DELETE /sessions/{id}`, `POST /problems/{id}/session` (attach / detach). `POST /solve` now accepts an optional `session_id`.
- **`POST /explain/{problem_id}`** — Ollama narrator. Reads the existing solved record (problem, attempts, verification, cross-verify), builds a strictly-constrained prompt that forbids the model from solving anything, and returns a 2–5 sentence plain-English narration. Falls back to a deterministic English summary built from the trace when `OLLAMA_ENABLED=false` or the local model is unreachable, so the endpoint always returns something useful.
- **Sessions panel** on the Solve tab: dropdown to pick a session, "new" / "rename" / "delete" controls, an editable markdown notes area that persists on save. Solves with an active session attach automatically.
- **"Explain in plain English"** button next to the answer; the narration appears below with a small `source: ollama|deterministic` tag so users always know whether they're reading machine-paraphrased English or a deterministic template.
- `/db/stats` now reports a `sessions: <count>` field.

The hard constraint stays in force: **the LLM never decides math.** Every fact in the narration comes from the engine's own records; the model only paraphrases.

### What Phase 7 adds

The hypothesizer's discoveries no longer sit passively in the graph —
they influence the next solve.

- **Identity-aware ranking** (`pru_math/rules.py`). The Learner now optionally takes a `RelationalGraph` reference. On every rank, it walks the graph from the current problem's signature node out to any verified `rule` nodes (`r:hyp_*`) and counts how many `(tool, approach)` witnesses each candidate has on supporting problems. Each witness adds a small bonus (`PRU_LEARNER_RULE_BONUS=0.05`, capped at `0.30`) on top of the UCB score, surfaced as `rule_bonus` and `rule_witnesses` in `CandidateStats` and the `decision` trace step's rationale. Verification rate stays the dominant term — rules just nudge the engine toward approaches that have been part of an identity chain on similar problems.
- **Transitive identity detector** (`Hypothesizer.detect_transitive_identities`). For every pair of verified `A ≡ B` and `B ≡ C` whose canonical bridge form lines up, the engine proposes `A ≡ C` and runs it through the same SymPy / numeric / Z3 verifier. Carries `derived_from: [parent_id_1, parent_id_2]` in its evidence so the chain is auditable.
- **Hard-signature detector** (`Hypothesizer.detect_hard_signatures`). For each signature class with at least 3 verified problems and an average `> 1.5` attempts-until-verified, propose a "fallback chain" hypothesis recording the most-frequently-winning `(tool, approach)`. Reuses the recurring-approach kind so the existing stat-verifier handles it. This makes "this signature is harder than usual; here's what works" a first-class output of the engine.
- **Auto-merge of derived hypotheses**: every detector goes through the same `upsert_hypothesis` path keyed on the deterministic fingerprint, so re-scans (including the auto-scan loop) merge new evidence into existing rows without duplicating.

### What Phase 6 adds

- **Tool-call timeout enforcement** — `CONFIG.tool_timeout_s` was read but never applied; now every `Tool.solve_with` is wrapped in a `concurrent.futures` budget. A timeout becomes an ordinary failed `ToolResult` with `error="ToolTimeoutError: ..."`, so the learner records it as a normal failure mode and moves on to the next candidate. Per-tool `timeout_s` overrides are supported.
- **Cross-verifier priority** — Z3 (proof) outranks numeric (empirical agreement) outranks SymPy (symbolic re-derivation). The `pick_cross_verifier` returns the highest-priority eligible tool deterministically, so `Z3` is preferred whenever it can handle the problem.
- **Runtime settings** (`pru_math/settings.py`). Layered on top of frozen `CONFIG` so a `PUT /config` flip takes effect immediately and persists to `data/settings.json` without restarting. The validator is the single source of truth for ranges and types. New endpoints: `GET /config` (now also lists `settable_keys`), `PUT /config`, `POST /config/reset`. The UI gains a cog icon that opens a settings modal.
- **Database export / import** (`pru_math/exporter.py`). `GET /db/export` returns a single JSON bundle of every persisted row plus a base64-encoded gpickle of the graph; `POST /db/import` replaces the live state atomically (single SQL transaction) and rejects malformed bundles. The Database tab gains export and import buttons. The user's accumulated knowledge is now portable.
- **Auto-scan**. With `auto_scan_every_n > 0`, the reasoner triggers `Hypothesizer.scan(verify=True)` in-process every N solves. The result lands in the trace as an `auto_scan` step listing what was discovered. Set `PRU_AUTO_SCAN_EVERY_N=10` (or change it live in the settings modal) and the system continuously hunts for new identities while you work.

### What Phase 5 adds

- **`pru_math/hypothesizer.py`** — three detectors that scan `tool_outcomes` and verified attempts for patterns and propose structured hypotheses with deterministic fingerprints (so re-scans merge evidence into existing rows rather than duplicating):
  - `detect_specializations` — when one tool dominates a `problem_type` (verify rate ≥ 70%, n ≥ 3), propose "for this type, prefer that tool"
  - `detect_recurring_approaches` — when one approach dominates a signature class (verify rate ≥ 80%, n ≥ 3), propose "for this signature, prefer this approach"
  - `detect_identities` — group verified `SIMPLIFY/EXPAND/FACTOR` results by canonical form; pairs of distinct inputs that canonicalise to the same thing become candidate identities `lhs ≡ rhs`
- **Verification pipeline** — for identities: SymPy `simplify(lhs - rhs) == 0` first, numeric sampling next, Z3 `unsat`-on-negation when the subset allows; for stat-style hypotheses: re-check the threshold against the live store. Each verifier records its method (`sympy` / `numeric` / `z3` / `stat`) and a one-line detail.
- **Graph integration** — verified identities materialise as `rule` nodes (`r:hyp_<id>`) with `uses_rule` edges back to every supporting problem. They show up alongside everything else on the Graph tab.
- **New endpoints**: `GET /hypotheses?status=&kind=`, `GET /hypotheses/{id}`, `POST /hypotheses/scan?verify=true`, `POST /hypotheses/{id}/verify`. `GET /db/stats` now includes a `hypotheses: {status: count}` map.
- **Hypotheses tab** in the UI — list cards filtered by status / kind, scan-now button, re-verify per row, expandable raw evidence. The tab gains a count badge in the topbar.
- **Schema**: a new `hypotheses` table with `status`, `kind`, `claim`, `claim_repr`, `fingerprint` (UNIQUE), `evidence_json`, `method`, `verification_detail`, `rule_node`, and timestamps. Indexes on `status` and `kind`. Auto-created on existing databases.

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
| `pru_math/hypothesizer.py`          | **Phase 5** — three detectors + verification pipeline for identities and routing rules |
| `pru_math/tools/timeout.py`         | **Phase 6** — `run_with_timeout` wrapper enforcing `PRU_TOOL_TIMEOUT_S` |
| `pru_math/settings.py`              | **Phase 6** — layered runtime config, persistent JSON overrides |
| `pru_math/exporter.py`              | **Phase 6** — single-bundle DB + graph export / atomic import |
| `pru_math/rules.py`                 | **Phase 7** — graph traversal from a fingerprint to verified-rule witnesses |
| `pru_math/narrator.py`              | **Phase 8** — Ollama-backed plain-English narration of a stored trace (with deterministic fallback) |
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

169 tests covering the parser (three formats), fingerprint determinism
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
when no second tool can handle the problem), the **hypothesizer**
(Pythagorean identity discovery from raw inputs alone, verifier proves
correct pairs and refutes wrong ones, specialisation /
recurring-approach detection on synthetic stats, scan idempotency,
verified identities materialise rule nodes in the graph), the
**hypotheses API** (scan, status filter, get one, re-verify, 404), and
the FastAPI layer via `TestClient` (`/solve`, `/problems/{id}/similar`,
`/graph`, `/graph/around/{id}`, `/graph/stats`, `/attempts`,
`/tool_outcomes`, `/db/stats`, `/hypotheses`).

The NL parser is exercised via a mock so the suite doesn't need a running
Ollama. Z3 tests skip cleanly when `z3-solver` is not installed.

## What this system is NOT

- Not a replacement for SymPy or Mathematica. It **uses** them.
- Not a math LLM. The LLM is used only for parsing natural-language input and (in later phases) generating explanations of reasoning traces. It never decides math.
- Not a black box. Every decision is auditable by design. If it isn't, that's a bug.
