# Changelog

All notable changes to PRU Math Engine. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/) and the project follows
[semantic versioning](https://semver.org/) — major.minor.patch where
each major number corresponds to a deliberate phase of the build plan
described in the README.

## Unreleased

Nothing pending.

## [0.12.0] — Phase 12: multi-step rewrite chains

### Added
- `pru_math/rewriter.py::generate_rewrite_chains` — BFS over the rule
  graph up to `max_depth` rule applications. Verified-identity
  hypotheses can now be composed in sequence before solving (e.g.
  apply `(a+b)² ≡ a²+2ab+b²` then `(a-b)² ≡ a²-2ab+b²` to prove
  `(a+b)² - (a-b)² = 4ab` automatically).
- `RewriteChain` dataclass with `to_trace_dict()` for full
  per-step provenance (rule ids, intermediate expressions, final
  expression) — surfaced in the reasoning trace and the persisted
  `attempts.steps_json`.
- `max_rewrite_depth` runtime setting (default `2`, env
  `PRU_MAX_REWRITE_DEPTH`). Set to `1` to revert to the Phase 9
  single-step behaviour.
- 12 new tests covering chain composition, depth caps, BFS dedup
  (no infinite loops on bidirectional rules), reasoner integration,
  and the verify-against-original audit invariant.

### Changed
- `Reasoner._try_rewrites` now consumes chains. Depth-1 chains are
  mathematically identical to Phase 9 single-step rewrites, so all
  pre-existing rewrite tests pass with the new code path.
- `tests/test_phase9_rewrite.py` accepts both
  `"rewrite chain depth N via rules [..]"` and the legacy
  `"rewrite via rule N"` step phrasings.

### Notes
- The verification invariant from Phase 9 is unchanged: every chained
  rewrite is verified against the **original** parsed problem, never
  the intermediate or final rewritten form.
- 216 tests passing.

## [0.11.0] — Phase 11 + UI/UX overhaul

### Added (Phase 11 — notebook view)
- New `Notebook` tab in the UI: chronological problem cards per
  session with inline `explain` / `similar` / `move-to-session` /
  `delete` actions; markdown notes rendered with `marked.js`.
- `DELETE /problems/{id}` — cascades attempts via the FK and drops
  the corresponding `p:<id>` graph node.
- `GET /sessions/{id}/export` — session-scoped JSON bundle that
  round-trips through `POST /db/import`.
- Tab badge on Notebook for the active session's problem count.

### Added (UI/UX overhaul, merged from `main`)
- Examples drawer with 5 categories × 6 click-to-load problems each.
- Intent buttons (`auto` / `solve` / `integrate` / `differentiate` /
  `factor` / `expand` / `simplify` / `limit` / `evaluate`) routed via
  a new optional `problem_type` override on `POST /solve`.
- "Watch It Learn" demo rail with a 6-step guided tour.
- Live ranker preview card (shows UCB1 ranking before solving).
- Replay button to re-run the same input against the now-updated
  learner.
- Trace timeline redesign with iconified per-kind steps and decision
  bar charts.
- Copy-trace-as-markdown for paper-ready audit logs.
- Identity wall sub-tab in Hypotheses, KaTeX-rendered LHS ≡ RHS.
- Toast notifications replacing 11 `alert()` sites.
- Keyboard shortcuts: `Alt+1..6` tabs, `?` help, `D` demo, `/` focus,
  `Esc` close.
- Mobile breakpoint at 768px.
- KaTeX-rendered answers with show-raw toggle.
- `POST /db/reset` — wipes problems / attempts / tool_outcomes /
  hypotheses and clears the relational graph in one transaction;
  sessions and config preserved.

### Fixed
- **Verifier**: new `no_change` status when `SIMPLIFY` / `FACTOR` /
  `EXPAND` output is structurally identical to input. Catches
  `radsimp(x⁴-16) → x⁴-16` previously stamped as "verified".
- **Parser**: rejects natural-language artifacts where SymPy's
  `split_symbols` ate words like `solve` into single-letter symbol
  products. NL inputs now reach Ollama instead of getting trapped at
  the SymPy stage.
- **Parser**: NL Ollama timeout decoupled from `tool_timeout_s`
  (≥60s default) so reasoning models with `<think>` tokens have
  enough room.
- **Hypothesizer**: `detect_identities` now treats
  `verified ∪ no_change` as identity-trustworthy. `no_change` *is*
  identity attestation: the tool agrees with the input.

### Notes
- 204 tests passing after the merge.

## [0.10.0] — Phase 10: distribution

### Added
- `pyproject.toml` with full PEP 621 metadata, optional `[all]`
  (Z3) and `[test]` extras, two console-script entry points:
  `pru-math` (CLI) and `pru-math-server` (uvicorn wrapper).
- `pru_math/server.py` — argparse wrapper around `uvicorn.run`.
- `Dockerfile` based on `python:3.11-slim`; `docker-compose.yml`
  preconfigured to talk to a host-side Ollama via
  `host.docker.internal`.
- Read-only mode (`PRU_READ_ONLY=true`) — middleware that 403s
  any non-`GET` request with a clear message. `/config` reports
  the flag.
- `INSTALL.md` — every install path (pip-from-GitHub, source,
  Docker), Ollama setup, examples, troubleshooting, maintainer's
  PyPI publishing guide.
- 12 new tests for read-only mode, console-script entry points,
  and package metadata.

### Changed
- Frontend assets moved from repo-root `frontend/` to
  `pru_math/frontend/` so `pip install` ships the same UI the
  source checkout serves.

## [0.9.0] — Phase 9: rewrite-based search

### Added
- `pru_math/rewriter.py` — verified-identity hypotheses become
  rewriting rules via SymPy `Wild`-based pattern matching. Two-stage
  matching: direct `expr.replace`, then sub-Add subset matching for
  patterns that appear inside larger Adds.
- `_try_rewrites` post-failure phase in the reasoner. Fires only
  when no primary attempt verified, the rewrite phase is enabled,
  and at least one verified-identity hypothesis exists. Verification
  still runs against the original parsed problem.
- `enable_rewriting` and `max_rewrite_attempts` runtime settings.

## [0.8.0] — Phase 8: notebook sessions + Ollama narrator

### Added
- `sessions` SQLite table; nullable `session_id` on `problems`.
- Full session CRUD over the API; `POST /solve` accepts
  `session_id`.
- `POST /explain/{problem_id}` — Ollama-backed plain-English
  narration of a stored trace, with a deterministic fallback when
  `OLLAMA_ENABLED=false`.

## [0.7.0] — Phase 7: reasoning quality

### Added
- `pru_math/rules.py` — graph traversal from a fingerprint to
  verified-rule witnesses. Identity-aware ranking bonus on the
  Learner score (default cap 0.30).
- `Hypothesizer.detect_transitive_identities` — from verified
  `A ≡ B` and `B ≡ C`, propose `A ≡ C`.
- `Hypothesizer.detect_hard_signatures` — flag problem classes
  that reliably need >1.5 attempts before something verifies, plus
  the most-frequently-winning approach.

## [0.6.0] — Phase 6: operational hardening

### Added
- Tool-call timeout enforcement
  (`pru_math/tools/timeout.run_with_timeout`).
- Cross-verifier priority (Z3 > numeric > SymPy).
- `pru_math/settings.py` — runtime-settable layered config with
  persistent JSON overrides; `PUT /config` and `POST /config/reset`.
- `pru_math/exporter.py` — single-bundle DB + graph export
  (`GET /db/export`) and atomic import (`POST /db/import`).
- Auto-scan: `auto_scan_every_n` setting triggers
  `Hypothesizer.scan(verify=True)` every N solves.

## [0.5.0] — Phase 5: hypothesizer

### Added
- `pru_math/hypothesizer.py` with three detectors
  (`detect_specializations`, `detect_recurring_approaches`,
  `detect_identities`) and a verification pipeline (SymPy first,
  numeric sampling next, Z3 unsat-on-negation last).
- `hypotheses` SQLite table; verified identities materialise as
  `rule` nodes in the graph with `uses_rule` edges back to
  supporting problems.
- `/hypotheses` endpoints (`GET`, `POST scan`, per-id `GET`,
  `POST verify`).

## [0.4.0] — Phase 4: multi-tool registry

### Added
- `pru_math/tools/registry.py` — `Tool` ABC and `ToolRegistry` for
  discovery, capability checks, candidate ranking across tools.
- `pru_math/tools/numeric_tool.py` — scipy / mpmath fallback.
- `pru_math/tools/z3_tool.py` — Z3 SMT with SymPy → Z3 translator.
- `pru_math/tools/wolfram_tool.py` — optional HTTP fallback gated
  by `WOLFRAM_APP_ID`.
- Cross-verification — `cross_verify_*` columns on `attempts`.

## [0.3.0] — Phase 3: learning

### Added
- `pru_math/learner.py` — UCB1 ranker over per-(signature, tool,
  approach) statistics; type-level fallback for unseen signatures.
- Multi-attempt loop in the reasoner (≤ `PRU_MAX_ATTEMPTS`,
  default 3).
- `failure_modes_json` column on `tool_outcomes` capturing the last
  8 distinct error tags per (signature, approach) pair.
- `tools/sympy_tool.py` restructured into a registry of named
  approaches per problem type.

## [0.2.0] — Phase 2: relational graph + retrieval

### Added
- `pru_math/graph.py` — `RelationalGraph` (NetworkX `MultiDiGraph`)
  with atomic gpickle persistence and corruption recovery.
- `pru_math/retrieval.py` — `find_similar_problems` (Python scan +
  scipy.sparse path for large graphs).
- The reasoner queries the graph before calling SymPy and surfaces
  similar past problems in the response.
- Frontend: 4-tab layout (Solve / Graph / Database / Insights),
  Cytoscape graph viz.

## [0.1.0] — Phase 1: skeleton

### Added
- Initial project scaffold: parser (SymPy / LaTeX / Ollama),
  fingerprint, SymPy tool dispatcher, verifier, plain-`sqlite3`
  store, Phase 1 reasoner, FastAPI app, minimal dark-mode UI.
- 47 tests covering every module.
