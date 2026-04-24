# Philosophy — Precomputed Relational Reasoning for Math

The PRU Math Engine is a testbed for a specific claim about AI systems:

> A transparent, learning, relational reasoner can be a viable architecture
> for AI — one where every decision is grounded, every step is explainable,
> and the system gets demonstrably smarter over time **without retraining a
> single neural network.**

Math is the testbed because math is verifiable. If the architecture works
here — if a graph-of-relations-and-tools can solve, learn, and introspect
on its own reasoning — then the architecture generalises to any domain
that admits machine-checkable outcomes.

## Why "precomputed relational"

The bet is that reasoning is, in general, closer to **retrieval and
composition** than to **from-scratch inference**. A human mathematician
rarely derives integration-by-parts from first principles in the middle of
a problem — they recognise a shape, recall a tool, apply it, and verify.

Applied to software, that means:

- **Every solved problem becomes a node.**
  Its structural fingerprint, the tool that solved it, the steps taken, and
  the verification result are all first-class data.
- **Every attempt — successful or failed — becomes an edge.**
  Failed approaches are not erased; the graph remembers what was tried so
  the system doesn't blunder into the same dead end twice.
- **New problems are solved by traversal.**
  Retrieve similar fingerprints, rank candidate approaches by their past
  success on that neighbourhood, try the best-ranked approach, verify,
  update the graph.

This is not a new idea in its parts — case-based reasoning, retrieval-augmented
generation, bandit-style tool selection all live here — but the framing of
**"the graph is the memory, the traversal is the reasoning, and the LLM only
translates"** is the distinctive bet.

## The role of the LLM

In this project the LLM is used for exactly two things:

1. Parsing natural-language input into a SymPy expression.
2. (Later phases) Producing human-readable narratives of reasoning traces.

The LLM never:

- picks the tool to call
- decides the solution strategy
- produces the final mathematical answer
- is treated as a source of truth about math

This is the single most important invariant. An LLM that decides math
hallucinates; an LLM that translates language into a formal expression
can be wrong, but the expression is then handed to a tool whose output
is verified.

## Auditability as the product

The transparency is the product. The reason to use this system rather
than "just call SymPy" or "just ask a chatbot" is that every decision —
the parse, the fingerprint, the tool choice, the verification, the
graph update — is inspectable:

- The problem is stored with its raw input, its parsed form, and its
  fingerprint.
- Every tool invocation stores its approach, its timing, its steps,
  its result, and its error (if any).
- In later phases, every decision the reasoner made (why this tool
  first? which past problems informed it? what did the learner's
  weights say?) is surfaced in the trace.
- The entire SQLite database is a plain file with no ORM; users can
  open it in any SQLite browser, edit rows, delete experiments, export
  JSON, re-import.

A feature that obscures any of this is a bug, not a feature.

## Why build it in phases

- Phase 1 proves the substrate works: parse, solve, verify, persist. No
  graph, no learning, no alternate tools. If this isn't solid, nothing
  downstream will be.
- Phase 2 adds the relational graph and similarity retrieval but still
  calls SymPy every time. This is where the "precomputed relational"
  story starts to show: the system can say "this problem looks like
  these 5 past ones" before it answers.
- Phase 3 is where it starts to learn — ranked approach selection with a
  documented exploration policy, and the reasoning trace becomes
  decision-justifying rather than just descriptive.
- Phase 4 makes the system plural: numeric solvers, Z3, optionally
  Wolfram. The tool registry is designed so adding a tool is a one-file
  change, and cross-tool verification becomes possible.
- Phase 5 is the genuinely novel layer: the system scans its own graph
  for empirical equivalences, recurring sub-paths, and routing patterns,
  and proposes shortcuts / identities / specialisations as structured
  hypotheses, which are then verified by the tools. Refuted hypotheses
  are stored too — the graph remembers what didn't work.

At no point does any phase require throwing away the previous phase's
work. The `Reasoner`, `Store`, and `ToolResult` shapes already have the
fields the later phases need.

## What this system is not

- It is not a replacement for SymPy or Mathematica. It uses them.
- It is not a math LLM.
- It is not pretending to discover mathematics in any deep sense. Phase 5
  proposes useful shortcuts within the graph's domain — not Riemann.
- It is not a black box. If it becomes one, it has failed.
