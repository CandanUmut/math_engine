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

This repo is **Phase 2** of a 5-phase plan. The later phases are not yet
implemented; the architecture was designed so adding them is additive.

| Phase | Scope                                                   | State  |
| ----- | ------------------------------------------------------- | ------ |
| 1     | Skeleton · SymPy dispatch · verification · SQLite store | **done** |
| 2     | Relational graph · similarity-based retrieval           | **done** |
| 3     | Learning · approach ranking · reasoning trace           | planned |
| 4     | Multi-tool orchestration (numeric · Z3 · Wolfram)       | planned |
| 5     | Hypothesis generation · identity discovery              | planned |

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

### What Phase 2 does **not** do yet

- No learning — approach selection is still hardcoded by problem type.
  Tool outcomes are recorded but not yet consumed by a ranker.
- No alternate tools — SymPy only.
- No hypothesis generation.

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
│  Solve · Graph (cytoscape) · Database · Insights                      │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼────────────────────────────────────┐
│                          pru_math.api (FastAPI)                       │
│   /solve   /problems   /problems/{id}/similar   /attempts             │
│   /tool_outcomes   /graph   /graph/around/{id}   /graph/stats         │
│   /db/stats   /config                                                 │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼────────────────────────────────────┐
│                          pru_math.reasoner                            │
│  parse → fingerprint → retrieval → sympy_tool → verify → persist →    │
│  graph_update → trace                                                 │
└──┬─────────┬──────────────┬──────────────┬──────────────┬─────────────┘
   │         │              │              │              │
 parser  fingerprint     retrieval      sympy_tool      verifier
                              │
                              ▼
                  ┌────────────────────────┐
                  │  RelationalGraph       │
                  │  (NetworkX MultiDiGraph│
                  │   + atomic gpickle     │
                  │   + scipy.sparse path) │
                  └───────────┬────────────┘
                              │
                  ┌───────────▼───────────┐
                  │       SQLite          │
                  │  problems · attempts  │
                  │   · tool_outcomes     │
                  └───────────────────────┘
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
| `pru_math/reasoner.py`              | orchestrator; emits a structured `SolveOutcome` + trace |
| `pru_math/api.py`                   | FastAPI app; mounts the frontend |
| `pru_math/__main__.py`              | `python -m pru_math "..."` CLI |
| `frontend/`                         | static UI (no bundler — Cytoscape via CDN) |

## Tests

```bash
OLLAMA_ENABLED=false pytest -q
```

71 tests covering the parser (three formats), fingerprint determinism and
similarity, SymPy tool dispatch for every supported problem type, the
verifier against correct and wrong candidates, the SQLite store, the
graph (node/edge add, similarity edges, persistence round-trip,
corruption recovery, cytoscape serialisation), retrieval (basic, exclude
self, prefer-verified best-attempt, sparse-path fallback), the reasoner
end-to-end (including "second similar problem finds the first"), and
the FastAPI layer via `TestClient` (`/solve`, `/problems/{id}/similar`,
`/graph`, `/graph/around/{id}`, `/graph/stats`, `/attempts`,
`/tool_outcomes`, `/db/stats`).

The NL parser is exercised via a mock so the suite doesn't need a running
Ollama.

## What this system is NOT

- Not a replacement for SymPy or Mathematica. It **uses** them.
- Not a math LLM. The LLM is used only for parsing natural-language input and (in later phases) generating explanations of reasoning traces. It never decides math.
- Not a black box. Every decision is auditable by design. If it isn't, that's a bug.
