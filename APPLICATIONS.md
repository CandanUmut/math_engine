# What can you actually do with this?

Six concrete applications, each with a worked example you can run in
two minutes.

> **Setup for every example below.** Install the engine
> (`pip install git+https://github.com/CandanUmut/math_engine.git`),
> then either run `pru-math "..."` for one-off problems or
> `pru-math-server` for the web UI. Optional but recommended:
> `ollama pull qwen2.5-math` so natural-language input works.

---

## 1. Verify your homework, with a paper trail

You're stuck on a quadratic. Most tools either give you the answer
(no learning) or make you type it in their syntax (no help if you
don't know the syntax). The PRU Math Engine takes English, shows you
*every approach it tried*, and tells you which one was confirmed by a
second tool.

**Worked example.** Solve `x² - 5x + 6 = 0`, see the multi-attempt
loop, see Z3 cross-verify SymPy's answer.

```bash
PRU_CROSS_VERIFY=true pru-math "Eq(x**2 - 5*x + 6, 0)"
```

The CLI shows the chosen approach, the verification status, and the
wall time. Open the UI to see the full trace including:

- Which approaches were ranked highest and why (UCB1 score breakdown)
- Each attempt's result and verification status
- The Z3 cross-verifier saying *"agree — substituted 2 root(s) → 0"*

**Why it's useful.** Unlike a black-box answer, the trace lets you
follow the reasoning. Unlike a textbook solution, the verification is
done by an algorithm independent of the solving algorithm — a real
cross-check, not just a re-derivation.

---

## 2. A working notebook for research math

You're working through a problem set or exploring a hunch. You want
related problems grouped together with notes, and you want the
accumulated knowledge to be sharable.

**Worked example.** Spin up a session, solve a few related problems,
attach markdown notes, export the session as a single file.

1. Open the UI. In the **Solve** tab, type `Calculus warmups` in the
   small panel above the input → click "new". The dropdown switches
   to that session.
2. Solve a few problems while the session is active:
   ```
   Eq(x**2 - 5*x + 6, 0)
   Integral(x**2, (x, 0, 1))
   Integral(exp(-x**2), (x, 0, 1))
   sin(x)**2 + cos(x)**2
   ```
3. Click the **Notebook** tab. You'll see all four problems as
   chronological cards with results and verification badges.
4. Click "edit" on the notes card and write some markdown. Save.
5. Click "export session" — you get a single JSON file with
   everything: problems, attempts, learned tool stats, the relational
   graph subgraph, plus the markdown notes.

Send the file to a collaborator. They `POST /db/import` it and
inherit the same notebook, including the engine's accumulated
preferences for *this* set of problems.

**Why it's useful.** The database is the value. Sharing accumulated
mathematical knowledge as a single JSON bundle means the engine's
learning travels with the problems.

---

## 3. Teaching: show students how the engine reasons

The PRU Math Engine is, deliberately, a transparent reasoner. Every
decision is exposed. That makes it a teaching tool.

**Worked example.** Start with an empty database. In the Solve tab,
solve `Eq(x**2 - 5*x + 6, 0)`. Open the **Reasoning trace** card.

- The first step is `parse` — show students how the parser identified
  this as a `solve` problem.
- The `decision` step shows the candidate table — explain UCB1: with
  no history, every approach gets the same score plus a small
  exploration bonus, so the registry's confidence ordering wins.
- The `tool_call` step shows SymPy returning `[3, 2]`.
- The `verify` step shows the verifier substituting each root back
  into the equation and getting 0 both times.
- The `learn` step shows the rank deltas: now that `sympy.roots`
  succeeded once on this signature class, its score is slightly
  higher next time.

Solve a few more quadratics. Then open the **Insights** tab — the
verify-rate-over-time chart trends upward. The system is *learning*,
visibly, without retraining a single weight.

**Why it's useful.** Most "AI" math tools are black boxes. This one
shows that you can build a learning system entirely out of inspectable
decisions — a great anchor for a class on classical AI / heuristic
search / interpretable ML.

---

## 4. Verify AI-generated math

LLMs hallucinate math. The PRU Math Engine uses its LLM (Ollama)
*only* to translate natural language into SymPy syntax — never to
decide. Every answer is verified by an algorithm independent of the
LLM.

**Worked example.** ChatGPT claims that
`integrate(x*exp(-x**2)) = -1/2 * exp(-x**2)`. Verify it.

```bash
# Set up Ollama (one-time)
ollama pull qwen2.5-math
echo "OLLAMA_ENABLED=true" >> .env

# Run it
pru-math "differentiate -1/2 * exp(-x**2) with respect to x"
```

Output:
```
parsed as  : natural_language / differentiate
expression : Derivative(-exp(-x**2)/2, x)
answer     : x*exp(-x**2)
tool       : sympy.diff
time       : 14 ms
verify     : verified
```

The differentiation gives back the original integrand, confirming the
claim. The verification was done by SymPy comparing
`d/dx(answer)` against `x*exp(-x**2)` numerically at six sample
points. The LLM's role was strictly translation.

**Why it's useful.** This is the right architecture for "AI-assisted
math": the LLM provides the natural-language interface, but a
deterministic, auditable system does the actual reasoning and
verification. No hallucinations get through.

---

## 5. Mathematical exploration: discover identities you didn't know

The hypothesizer scans your solve history and proposes identities,
specialisations, and routing rules. They get verified by a second
algorithm before being added to the engine's knowledge.

**Worked example.** Have the engine discover the Pythagorean identity
from scratch.

```bash
# Empty database
rm -f data/pru_math.sqlite data/pru_graph.gpickle

# Solve a few related forms — the engine doesn't know they're related
pru-math "sin(x)**2 + cos(x)**2"
pru-math "1"
pru-math "cos(x)**2 + sin(x)**2"

# Trigger a hypothesis scan
curl -X POST http://localhost:8000/hypotheses/scan
```

The output:
```json
{
  "scanned": 1,
  "items": [{
    "kind": "identity",
    "claim": "sin(x)**2 + cos(x)**2  ≡  1",
    "status": "verified",
    "method": "sympy"
  }]
}
```

The detector grouped the verified-or-no-change SIMPLIFY results by
canonical form, noticed three different inputs all canonicalise to
`1`, and proposed pairwise identities. The verifier then ran
`sp.simplify(lhs - rhs) == 0` on the proposed identity, got `True`,
and stamped it as verified.

**Now the closed loop fires.** Solve a problem the primary tools
can't handle:

```bash
PRU_MAX_REWRITE_DEPTH=3 pru-math "Eq(sin(z)**2 + cos(z)**2 - 1, 0)"
```

The trace shows the engine matching `sin(z)**2 + cos(z)**2` inside
the larger Add via its own verified identity and rewriting the
residual to 0.

**Why it's useful.** This is the "system gets smarter without
retraining" claim, made tangible. You can run this on your own
problem domain and watch the engine extract structure you didn't
explicitly tell it about.

---

## 6. Curriculum design: find which problems are hard

The engine has a "hard signature" detector that flags problem
classes where the multi-attempt loop reliably needs more than one
approach before something verifies. Useful for teachers building a
problem set: which families of problems are conceptually hardest?

**Worked example.** Solve a mix of quadratics and trig identities.
Some will be "easy" (verified on the first attempt); others will be
"hard" (multiple approaches needed).

```bash
for q in \
    "Eq(x**2 - 5*x + 6, 0)" \
    "Eq(x**2 - 7*x + 12, 0)" \
    "Eq(x**2 - 9*x + 20, 0)" \
    "sin(x)**2 + cos(x)**2" \
    "sin(2*x)*cos(x) - 2*sin(x)*cos(x)**2" \
    "Integral(exp(-x**2)*sin(x), (x, 0, 1))"
do
    pru-math "$q"
done

# Then trigger a scan
curl -X POST http://localhost:8000/hypotheses/scan
```

In the **Hypotheses** tab you'll see entries like:

> `[verified] recurring_approach   Signature 4f8a2b is hard (avg 2.3 attempts); prefer sympy.trigsimp as a fallback`

This tells you (a) that signature class needed multiple approaches
on average, and (b) which approach reliably worked when others
didn't. For a teacher, this directly answers *"which problem types
should I budget more class time for?"*.

**Why it's useful.** Hardness is data the system already has but
nobody's looked at. The detector turns it into an actionable signal.

---

## What we're NOT

To set expectations honestly:

- **Not a replacement for SymPy or Mathematica.** The engine *uses*
  those tools. If you want raw symbolic computation power, use them
  directly.
- **Not a math LLM.** The local LLM only translates language. The
  reasoning, the answer, the verification — all done by classical
  algorithms.
- **Not a research-grade theorem prover.** Z3 handles the polynomial /
  integer / inequality subset. For real proofs in higher mathematics
  you want Lean, Coq, Isabelle.
- **Not pretending to discover deep mathematics.** The hypothesizer
  proposes useful shortcuts and identities within the graph's
  domain — useful, but not Riemann.

What it *is*: an interpretable, learning, relational reasoner that
gets demonstrably smarter with use, and shows you exactly how it
got there. The transparency is the product.
