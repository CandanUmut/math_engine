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

This repo is **Phase 1** of a 5-phase plan. The later phases are not yet
implemented; the architecture was designed so adding them is additive.

| Phase | Scope                                                   | State  |
| ----- | ------------------------------------------------------- | ------ |
| 1     | Skeleton · SymPy dispatch · verification · SQLite store | **done** |
| 2     | Relational graph · similarity-based retrieval           | planned |
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

### What Phase 1 does **not** do yet

- No graph retrieval — every problem is solved fresh.
- No learning — approach selection is hardcoded by problem type.
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
┌──────────────────────────────── GUI ────────────────────────────────┐
│  /  (index.html)  — input, answer, trace, fingerprint, recent list  │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│                    pru_math.api (FastAPI)                           │
│   POST /solve    GET /problems    GET /problems/{id}    /db/stats   │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│                      pru_math.reasoner (Phase 1)                    │
│    parse → fingerprint → sympy_tool → verify → persist → trace      │
└───────┬───────────────┬──────────────┬──────────────┬───────────────┘
        │               │              │              │
 ┌──────▼──────┐ ┌──────▼──────┐ ┌─────▼─────┐ ┌──────▼──────┐
 │   parser    │ │ fingerprint │ │  tools/   │ │  verifier   │
 │sympy / latex│ │             │ │ sympy_tool│ │ numeric +   │
 │ / NL (ollama│ │             │ │           │ │ symbolic    │
 └─────────────┘ └─────────────┘ └───────────┘ └─────────────┘

                              ┌──────────────┐
                              │   SQLite     │
                              │   problems   │
                              │   attempts   │
                              │ tool_outcomes│
                              └──────────────┘
```

## Module map

| File                                | Purpose |
| ----------------------------------- | ------- |
| `pru_math/config.py`                | env → `Config` dataclass |
| `pru_math/problem_types.py`         | canonical `SOLVE` / `INTEGRATE` / … tags |
| `pru_math/parser.py`                | SymPy / LaTeX / NL → `ParsedProblem` |
| `pru_math/fingerprint.py`           | structural fingerprint + similarity score (documented weights) |
| `pru_math/tools/sympy_tool.py`      | dispatches parsed problem to the right `sympy.*` call |
| `pru_math/verifier.py`              | per-type numerical / symbolic verification |
| `pru_math/store.py`                 | plain `sqlite3` wrapper (no ORM — inspect with any SQLite browser) |
| `pru_math/reasoner.py`              | Phase 1 orchestrator; emits a structured `SolveOutcome` + trace |
| `pru_math/api.py`                   | FastAPI app; mounts the frontend |
| `pru_math/__main__.py`              | `python -m pru_math "..."` CLI |
| `frontend/`                         | minimal static UI (no bundler in Phase 1) |

## Tests

```bash
OLLAMA_ENABLED=false pytest -q
```

47 tests covering the parser (three formats), fingerprint determinism and
similarity, SymPy tool dispatch for every supported problem type, the
verifier against correct and wrong candidates, the SQLite store, the
reasoner end-to-end, and the FastAPI layer via `TestClient`.

The NL parser is exercised via a mock so the suite doesn't need a running
Ollama.

## What this system is NOT

- Not a replacement for SymPy or Mathematica. It **uses** them.
- Not a math LLM. The LLM is used only for parsing natural-language input and (in later phases) generating explanations of reasoning traces. It never decides math.
- Not a black box. Every decision is auditable by design. If it isn't, that's a bug.
