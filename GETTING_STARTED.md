# Getting started

A 10-minute tour. By the end you'll have the engine running, you'll
have solved a few problems, and you'll have watched it discover an
identity it wasn't told about.

> **Audience.** This guide assumes you know enough Python to run
> `pip install` and understand basic `solve(x² - 4 = 0) → [-2, 2]`
> math. No SymPy / NetworkX / FastAPI knowledge required.

---

## What is this?

The PRU Math Engine is a **transparent math reasoner**. It's the layer
*above* SymPy, numerical solvers, and Z3 that:

1. **Decides** which tool to call on a given problem
2. **Verifies** every answer with a different algorithm than the one
   that produced it
3. **Learns** from every attempt — the next solve picks better
   approaches first
4. **Discovers** identities and shortcuts on its own, by analysing
   the patterns in the problems you've fed it
5. **Explains** every decision as an inspectable graph and a
   step-by-step reasoning trace

Crucially, it is **not a math LLM**. The local language model (Ollama,
optional) is used *only* to translate natural-language input into
SymPy syntax. The math itself is always done by SymPy / numeric / Z3
and the answer is always verified before it's shown.

---

## Why use it?

If any of these resonate, this is the tool:

- **You're a student** and you want to see *why* an answer is correct,
  not just be told it is. The trace shows every approach the engine
  tried, what verified, what was refuted, and which tools cross-checked
  the final answer.
- **You're a researcher** and you want a working notebook that
  remembers everything: every solved problem, every verified identity,
  every approach that worked or didn't on a problem class. Sessions
  group related work; the database is one JSON export away from being
  shareable.
- **You're a teacher** and you want a tool that demonstrates
  multi-tool verification (SymPy says X, Z3 confirms; numeric agrees)
  and the limits of automated reasoning. The hypothesis discovery
  shows students that mathematical patterns can be *empirically*
  found and then *symbolically* proven.
- **You want to verify AI-generated math.** Paste a math claim from
  ChatGPT or Claude, parse it, and let SymPy/Z3 actually check it.
  The LLM never gets a vote.
- **You want to play with mathematical exploration.** Solve a few
  trig problems and watch the engine discover the Pythagorean
  identity from the data alone. Solve a bunch of quadratics and watch
  it learn that `sympy.roots` is faster than `sympy.solve` for that
  shape.

---

## Install (one line)

```bash
pip install git+https://github.com/CandanUmut/math_engine.git
```

That's it. `pip` builds the package locally from the repo's
`pyproject.toml`. You get two console scripts: `pru-math` (CLI) and
`pru-math-server` (HTTP + UI).

If you also want Z3 (recommended — gives stronger cross-verification
on polynomial / integer problems):

```bash
pip install "pru-math-engine[all] @ git+https://github.com/CandanUmut/math_engine.git"
```

If `pru-math-engine` ever lights up on PyPI (see `RELEASING.md`), the
install commands become `pip install pru-math-engine` and
`pip install "pru-math-engine[all]"`.

### No Python? Use Docker

```bash
git clone https://github.com/CandanUmut/math_engine.git
cd math_engine
docker compose up
# open http://localhost:8000
```

Persistent state (every solved problem, every learned approach, every
verified identity) lives in `./data/` thanks to the volume mount.

---

## Solve your first problem (30 seconds)

```bash
pru-math "Eq(x**2 - 5*x + 6, 0)"
```

Output:

```
input      : Eq(x**2 - 5*x + 6, 0)
parsed as  : sympy / solve
expression : Eq(x**2 - 5*x + 6, 0)
answer     : [3, 2]
tool       : sympy.roots
time       : 80.3 ms
verify     : verified — all 2 solution(s) substitute to zero
problem_id : 1
```

What just happened:

1. The parser recognised SymPy syntax and identified this as a
   `solve` problem.
2. The fingerprinter computed a structural signature (a hash of the
   shape of the expression).
3. The retrieval layer looked for similar past problems (none yet —
   it's the first solve).
4. The learner ranked candidate approaches across SymPy / numeric /
   Z3. With no history, ranking falls back to the registry's
   confidence, so SymPy went first.
5. SymPy returned `[3, 2]`.
6. The verifier substituted each root back into the original
   equation; both gave 0.
7. The result was persisted: one row in `problems`, one row in
   `attempts`, one row in `tool_outcomes`, plus nodes and edges in
   the relational graph.

Try a few more:

```bash
pru-math "Integral(x**2, (x, 0, 1))"      # → 1/3, cross-verified by quadrature
pru-math "Derivative(sin(x)*cos(x), x)"    # product rule
pru-math "sin(x)**2 + cos(x)**2"          # → 1
pru-math "Limit(sin(x)/x, x, 0)"          # → 1
pru-math "factor(x**3 - 6*x**2 + 11*x - 6)"  # (x-1)(x-2)(x-3)
```

LaTeX works too:

```bash
pru-math '\int_{0}^{\pi} \sin(x) dx'
pru-math '\sum_{n=0}^{\infty} 1/n!'
```

---

## The Web UI (5 minutes)

```bash
pru-math-server
# open http://localhost:8000
```

Five tabs in the topbar:

| Tab | What it shows |
| --- | ------------- |
| **Solve** | Input + answer + multi-attempt list + reasoning trace + similar past problems + the structural fingerprint. Plus an "Explain in plain English" button that calls Ollama (or a deterministic fallback). |
| **Graph** | Interactive Cytoscape view of the relational graph: every problem you've solved, the tools and approaches that worked, the verified identities. Click any node for details. |
| **Database** | Sortable / filterable raw inspectors for the SQLite tables. Plus export / import buttons that produce a single JSON bundle of your accumulated knowledge. |
| **Insights** | Verify-rate-over-time, attempts-per-problem trend, average solve time by problem type. The line trending upward is the learner working. |
| **Hypotheses** | Identities and routing rules the engine has proposed and verified from your solve history. Click "scan now" to run the detectors on demand. |
| **Notebook** | A research-log view of the active session. Renders markdown notes, lists problems chronologically with inline explain / move / delete actions, exports the session as a self-contained JSON bundle. |

---

## Watch the engine learn (the killer demo)

```bash
# 1. Open the UI. Click the cog icon → set auto_scan_every_n=5 → save.

# 2. Solve these in any order:
pru-math "sin(x)**2 + cos(x)**2"
pru-math "1"
pru-math "cos(x)**2 + sin(x)**2"

# 3. Open the Hypotheses tab. After ~5 solves you'll see:
#       [verified] identity   sin(x)**2 + cos(x)**2 ≡ 1   method=sympy
#    On the Graph tab, a new red triangle (rule node) materialises.

# 4. Now solve a fresh trig identity with a different variable:
pru-math "sin(y)**2 + cos(y)**2"
# Open the Reasoning trace. The "decision" step shows the candidate
# table with rule_bonus +0.15 (×3 witnesses) on sympy.cancel — the
# system used its own discovery to rank approaches.

# 5. The killer demo (Phase 9 + 12): rewrite chains.
pru-math "Eq(sin(z)**2 + cos(z)**2 - 1, 0)"
# When the primary attempts return inconclusive, the engine matches
# the trig sum inside the Add via its own verified identity and
# rewrites the residual to 0 — surfaced as a `rewrite chain` trace step.
```

This is the **closed loop**: solve → learn → discover → reuse. No
training, no neural networks, no black boxes.

---

## Connecting Ollama (optional, for natural-language input)

The LLM is used **only** to translate questions like *"what is the
limit of sin x over x as x approaches zero"* into SymPy syntax. SymPy
then does the math and the verifier checks it.

```bash
# 1. Install Ollama from https://ollama.com (one-line install)

# 2. Pull a math-friendly model
ollama pull qwen2.5-math      # ~4 GB, recommended

# 3. Tell PRU about it
echo "OLLAMA_ENABLED=true"           >> .env
echo "OLLAMA_MODEL=qwen2.5-math"     >> .env

# 4. Restart the server, then try natural language:
pru-math "what is the integral of x squared from 0 to 1"
```

The Ollama path is gracefully optional. Without it, SymPy syntax and
LaTeX still work — you just can't paste English questions.

---

## What to read next

- **[APPLICATIONS.md](APPLICATIONS.md)** — concrete use cases with worked examples (students, researchers, teachers, AI verification, identity discovery, curriculum design).
- **[INSTALL.md](INSTALL.md)** — every install path (Docker, source clone, dev mode), Ollama setup, troubleshooting.
- **[RELEASING.md](RELEASING.md)** — how to publish a version to PyPI (for maintainers).
- **[PHILOSOPHY.md](PHILOSOPHY.md)** — the "why precomputed-relational reasoning?" essay.
- **[SCHEMA.md](SCHEMA.md)** — the SQLite schema and graph data model, in case you want to inspect the database directly with `sqlite3 data/pru_math.sqlite`.

---

## Need help?

- **The CLI prints `command not found`.** Your `pip install` worked
  but the console scripts aren't on `PATH`. Either activate the venv
  you installed into, or fall back to `python -m pru_math "..."` and
  `python -m pru_math.server`.
- **Natural language input doesn't work.** Either set
  `OLLAMA_ENABLED=true` after running Ollama locally, or rephrase in
  SymPy / LaTeX syntax.
- **Z3 errors on every problem.** Z3 only handles polynomial /
  rational / integer / inequality expressions. The engine catches
  `Z3UnsupportedError` and moves on automatically; if you see Z3
  errors *blocking* solves, check that `z3-solver` actually imports
  with `python -c "import z3"`. If not: `pip install z3-solver`.
- **Tests fail.** Run them with `OLLAMA_ENABLED=false pytest -q`.

For everything else, file an issue at
<https://github.com/CandanUmut/math_engine/issues>.
