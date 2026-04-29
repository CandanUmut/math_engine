# Install & run

## TL;DR

```bash
pip install git+https://github.com/CandanUmut/math_engine.git
pru-math-server
# open http://localhost:8000
```

That's it. No PyPI account, no clone, no Node toolchain. The four-tab UI
loads, all 9 phases of features are live, and the database lives at
`./data/`.

If you want Z3 too:

```bash
pip install "pru-math-engine[all] @ git+https://github.com/CandanUmut/math_engine.git"
```

If `pip install` from PyPI ever lights up (see "Publishing to PyPI"
below), the same `git+...` commands become the much shorter
`pip install pru-math-engine` and `pip install "pru-math-engine[all]"`.

---

## Install paths

### 1. From GitHub (works today)

```bash
pip install git+https://github.com/CandanUmut/math_engine.git
```

This builds the package locally from the repo's `pyproject.toml`. You get
the `pru-math` and `pru-math-server` console scripts.

To pin to a specific branch or tag:

```bash
pip install git+https://github.com/CandanUmut/math_engine.git@main
pip install git+https://github.com/CandanUmut/math_engine.git@v0.10.0
```

### 2. From PyPI (when published)

Once the maintainer runs the upload steps under *Publishing to PyPI*
below:

```bash
pip install pru-math-engine
pip install "pru-math-engine[all]"   # includes z3-solver
```

### 3. From a local clone (for development)

```bash
git clone https://github.com/CandanUmut/math_engine.git
cd math_engine
pip install -e ".[all,test]"
pytest                               # 183 tests, ~5 s
pru-math-server --reload             # hot-reload while editing
```

`-e` is editable mode — your code changes show up immediately, no
reinstall needed.

### 4. Docker (no Python on the host)

```bash
docker build -t pru-math-engine .
docker run --rm -p 8000:8000 -v $(pwd)/data:/data pru-math-engine
```

Or with compose:

```bash
docker compose up
```

The mounted `./data` volume keeps your accumulated knowledge across
container restarts. To talk to a host-side Ollama from inside the
container, set:

```bash
docker run --rm -p 8000:8000 \
    -e OLLAMA_ENABLED=true \
    -e OLLAMA_HOST=http://host.docker.internal:11434 \
    -v $(pwd)/data:/data pru-math-engine
```

The shipped `docker-compose.yml` already sets `host.docker.internal` as
an `extra_host`.

---

## Running

```bash
# CLI smoke test (no server)
pru-math "Eq(x**2 - 5*x + 6, 0)"
pru-math "Integral(x**2, (x, 0, 1))"
pru-math "sin(x)^2 + cos(x)^2"

# HTTP server
pru-math-server                      # 127.0.0.1:8000
pru-math-server --host 0.0.0.0       # bind public
pru-math-server --port 9000
pru-math-server --reload             # dev hot-reload
```

The four UI tabs:
- **Solve** — input, answer, attempts, reasoning trace, fingerprint, similar past problems
- **Graph** — interactive Cytoscape view of the relational graph
- **Database** — sortable / filterable raw inspectors for `problems`, `attempts`, `tool_outcomes`; export / import buttons
- **Insights** — verify-rate-over-time and other Chart.js dashboards
- **Hypotheses** — proposed / verified / refuted identities, scan button, re-verify

The cog icon in the topbar opens a settings modal with every runtime-
tunable knob (max attempts, exploration constant, cross-verify,
auto-scan, rewriting, ...).

---

## Connecting Ollama (for natural-language input)

```bash
# 1. Install Ollama from https://ollama.com (one-line install on macOS / Linux)

# 2. Pull a math-friendly model
ollama pull qwen2.5-math      # ~4 GB — best for our parsing prompt
# alternatives:
#   ollama pull llama3.1
#   ollama pull mistral

# 3. Ollama serves automatically on http://localhost:11434

# 4. Tell PRU about it (in .env or shell)
echo "OLLAMA_ENABLED=true"           >> .env
echo "OLLAMA_MODEL=qwen2.5-math"     >> .env
echo "OLLAMA_HOST=http://localhost:11434" >> .env

pru-math-server
```

Verify:

```bash
curl -s http://localhost:8000/config | python -m json.tool | grep ollama
# → "ollama_enabled": true, "ollama_model": "qwen2.5-math"
```

The LLM is used **only** to translate natural-language questions into
SymPy expressions. It never decides math — SymPy / numeric / Z3 always
do the actual work, and every answer is verified.

---

## Examples

### SymPy syntax (works without Ollama)

```bash
pru-math "Eq(x**2 - 5*x + 6, 0)"          # roots [2, 3]
pru-math "Integral(x**2, (x, 0, 1))"      # 1/3
pru-math "Integral(exp(-x**2), (x, 0, 1))" # erf-based answer
pru-math "Derivative(sin(x)*cos(x), x)"
pru-math "sin(x)**2 + cos(x)**2"          # → 1 (after a few of these
                                          #   the hypothesizer proposes
                                          #   the identity)
pru-math "factor(x**3 - 6*x**2 + 11*x - 6)"
pru-math "Limit(sin(x)/x, x, 0)"
```

### LaTeX (paste from a paper)

```bash
pru-math '\int_{0}^{\pi} \sin(x) dx'
pru-math '\sum_{n=0}^{\infty} 1/n!'
pru-math '\frac{d}{dx}(x^3 + 2x)'
```

### Natural language (with Ollama running)

```bash
pru-math "solve x squared minus five x plus six equals zero"
pru-math "integrate x squared from zero to one"
pru-math "what is the limit of sin x over x as x approaches zero"
pru-math "factor x cubed minus 6 x squared plus 11 x minus 6"
```

### "The system found something" demo

```bash
# 1. Open the UI, click the cog, set auto_scan_every_n=5, save.
# 2. Solve these in any order:
pru-math "sin(x)**2 + cos(x)**2"
pru-math "1"
pru-math "cos(x)**2 + sin(x)**2"
# 3. Watch the Hypotheses tab — after ~5 solves you'll see:
#       [verified] identity   sin(x)**2 + cos(x)**2 ≡ 1   method=sympy
#    The Graph tab shows a new red triangle (rule node).

# 4. Now solve a fresh trig identity with a different variable:
pru-math "sin(y)**2 + cos(y)**2"
# Open the Reasoning trace. The "decision" step shows the candidate
# table with rule_bonus +0.15 (×3 witnesses) on sympy.cancel — the
# system used its own discovery to rank approaches.

# 5. And the killer demo (Phase 9): rewrite-based search.
pru-math "Eq(sin(z)**2 + cos(z)**2 - 1, 0)"
# When the primary attempts return inconclusive, the engine matches
# the trig sum inside the Add via its own verified identity and
# rewrites the residual to 0 — surfaced as a `rewrite` trace step.
```

---

## Read-only mode (for shared / public servers)

```bash
PRU_READ_ONLY=true pru-math-server --host 0.0.0.0
```

Or from Docker:

```bash
docker run -p 8000:8000 -e PRU_READ_ONLY=true \
    -v $(pwd)/data:/data pru-math-engine
```

In read-only mode every `POST` / `PUT` / `DELETE` returns `403 Forbidden`
with a clear message. Reads (`GET /problems`, `GET /graph`, etc.) work
unchanged. Combined with `docker run --read-only`, this lets you publish
a snapshot of accumulated knowledge that visitors can browse but can't
poison.

---

## Sharing your accumulated knowledge

The `data/` directory is the value of this engine — it's where every
solved problem, every learned approach, every verified identity lives.

Two share paths:

1. **Tarball**: zip up `data/` and send it. The receiver puts it under
   their own checkout's `data/` and starts the server.
2. **JSON bundle**: in the UI, **Database tab → export**. This produces
   one self-contained JSON file (problems + attempts + tool_outcomes +
   hypotheses + the relational graph as base64-encoded pickle). The
   receiver imports via the same tab. No filesystem footprint to
   coordinate.

---

## Publishing to PyPI (maintainer's guide)

Once you have a PyPI account and an API token, publishing is a one-time
setup and a single command per release.

```bash
# 1. One-time setup:
pip install --upgrade build twine
# Save your token at ~/.pypirc:
#   [pypi]
#     username = __token__
#     password = pypi-...

# 2. For each release:
git tag v0.10.0           # match `version` in pyproject.toml
git push origin v0.10.0

# Clean any old build artefacts.
rm -rf dist build *.egg-info

# Build sdist + wheel.
python -m build

# Inspect the produced artefacts (recommended).
ls dist/
twine check dist/*

# Upload.
twine upload dist/*

# 3. Verify (in a fresh venv):
pip install pru-math-engine
pru-math "Eq(x**2 - 4, 0)"
```

After the first successful upload, the public install becomes:

```bash
pip install pru-math-engine
pip install "pru-math-engine[all]"     # with z3-solver
```

If you want to test the upload first, use TestPyPI:

```bash
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ pru-math-engine
```

---

## Troubleshooting

**"command not found: pru-math-server"**
The console scripts are installed into Python's bin/scripts directory.
If `pip install` worked but the command isn't on `PATH`, you're probably
in a virtual environment that isn't activated, or your `~/.local/bin`
isn't on `PATH`. Run `python -m pru_math.server` as a fallback.

**"could not parse input"**
You hit the third parsing path (natural language) without Ollama. Either
type the problem in SymPy syntax, or set `OLLAMA_ENABLED=true` after
running Ollama locally.

**"Z3UnsupportedError: ..."**
Z3 only handles polynomial / rational / inequality expressions. The
multi-attempt loop catches this and moves on to the next candidate. If
every Z3 attempt fails on every problem, set `PRU_AUTO_SCAN_EVERY_N=0`
and check that `z3-solver` actually imports (`python -c "import z3"`).

**"the engine is in read-only mode"**
You set `PRU_READ_ONLY=true`. Restart without it (or set to `false`).

**Tests fail with "Ollama unreachable"**
Run them with `OLLAMA_ENABLED=false pytest`.
