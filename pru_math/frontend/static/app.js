"use strict";

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const fmtNum = (n, d = 2) =>
  Number.isFinite(n) ? Number(n).toFixed(d) : "–";

const VERIFY_CLS = (s) =>
  s === "verified"  ? "good"
  : s === "refuted" ? "bad"
  : s === "no_change" ? "warn"
  : s ? "warn" : "";
const VERIFY_LABEL = (s) =>
  s === "no_change" ? "no change" : (s || "");
const CV_CLS = (s) => (s ? `cv-${s}` : "");

/* ── Intent (Phase A) ──────────────────────────────────────────────── */
let activeIntent = "auto";

/* ── Phase E: toast notifications ──────────────────────────────────── */
function toast(message, type = "info", duration = 3500) {
  const stack = $("toast-stack");
  if (!stack) { console.log("toast:", message); return; }
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.innerHTML = `<span class="toast-icon">${
    type === "success" ? "✓" : type === "error" ? "!" : type === "warn" ? "⚠" : "ℹ"
  }</span><span class="toast-msg">${escapeHtml(message)}</span>
  <button class="toast-close" aria-label="close">×</button>`;
  stack.appendChild(el);
  const close = () => { el.classList.add("toast-out"); setTimeout(() => el.remove(), 240); };
  el.querySelector(".toast-close").addEventListener("click", close);
  if (duration > 0) setTimeout(close, duration);
  return close;
}

/* ── Examples (Phase A) ────────────────────────────────────────────── */
const EXAMPLES = {
  algebra: [
    { input: "Eq(x**2 - 5*x + 6, 0)",        intent: "auto",   note: "quadratic — should give [2, 3]" },
    { input: "Eq(x**3 - 6*x**2 + 11*x - 6, 0)", intent: "auto", note: "cubic — three real roots" },
    { input: "x**4 - 16",                    intent: "factor", note: "factor difference of 4th powers" },
    { input: "(x + 1)**3",                   intent: "expand", note: "expand a binomial" },
    { input: "Eq(2*x + 3*y, 12)",            intent: "auto",   note: "linear in two variables" },
    { input: "Eq(2*x**2 - 5*x - 3, 0)",      intent: "auto",   note: "Z3 picks this up" },
  ],
  calculus: [
    { input: "Integral(x**2, (x, 0, 1))",      intent: "auto", note: "definite integral → 1/3" },
    { input: "Integral(sin(x), x)",            intent: "auto", note: "indefinite integral → -cos(x)" },
    { input: "Derivative(x**3, x)",            intent: "auto", note: "derivative → 3x²" },
    { input: "Limit(sin(x)/x, x, 0)",          intent: "auto", note: "classic limit → 1" },
    { input: "Limit((1 + 1/n)**n, n, oo)",     intent: "auto", note: "Euler's e" },
    { input: "Integral(exp(-x**2), (x, 0, oo))", intent: "auto", note: "Gaussian half — √π/2" },
  ],
  trig: [
    { input: "sin(x)**2 + cos(x)**2",          intent: "simplify", note: "Pythagorean → 1" },
    { input: "cosh(x)**2 - sinh(x)**2",        intent: "simplify", note: "hyperbolic → 1" },
    { input: "tan(x)",                         intent: "simplify", note: "for identity-pair with sin/cos" },
    { input: "sin(x)/cos(x)",                  intent: "simplify", note: "tan-identity partner" },
    { input: "sin(2*x)",                       intent: "simplify", note: "double-angle partner" },
    { input: "2*sin(x)*cos(x)",                intent: "simplify", note: "double-angle expanded" },
  ],
  hard: [
    { input: "Eq(cos(x) - x, 0)",              intent: "auto", note: "Dottie number ≈ 0.7390851 (numeric)" },
    { input: "Eq(x**5 - x - 1, 0)",            intent: "auto", note: "no closed form — numeric brentq" },
    { input: "Eq(sin(x) - x/2, 0)",            intent: "auto", note: "transcendental; numeric" },
    { input: "Integral(1/(1 + x**2), (x, -oo, oo))", intent: "auto", note: "improper → π" },
    { input: "Integral(log(x)/(1 + x**2), (x, 0, oo))", intent: "auto", note: "Catalan-class integral" },
    { input: "Eq(exp(x) - 2, 0)",              intent: "auto", note: "should give log(2)" },
  ],
  nl: [
    { input: "what is the integral of x squared from 0 to 1", intent: "auto", note: "via local Ollama" },
    { input: "differentiate sin(x) times x",                  intent: "auto", note: "" },
    { input: "factor x squared minus four",                   intent: "auto", note: "" },
    { input: "what is the limit of sin x over x as x approaches 0", intent: "auto", note: "" },
    { input: "what is two plus five",                         intent: "auto", note: "" },
    { input: "solve x squared minus nine equals zero",        intent: "auto", note: "" },
  ],
};
let activeExampleCat = "algebra";

function tag(text, cls = "") {
  if (text === null || text === undefined || text === "") return "";
  const c = cls ? ` ${cls}` : "";
  return `<span class="tag${c}">${escapeHtml(text)}</span>`;
}

function pct(n) {
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : "–";
}

/* ── Tabs ─────────────────────────────────────────────────────────── */

const TABS = ["solve", "graph", "database", "insights", "hypotheses", "notebook"];
function activateTab(name) {
  TABS.forEach((t) => {
    const btn = document.querySelector(`.tab[data-tab="${t}"]`);
    const panel = $(`tab-${t}`);
    if (btn) btn.classList.toggle("active", t === name);
    if (panel) panel.classList.toggle("active", t === name);
  });
  if (name === "graph") refreshGraph();
  if (name === "database") loadDbTable(currentDbView);
  if (name === "insights") refreshInsights();
  if (name === "hypotheses") refreshHypotheses();
  if (name === "notebook") refreshNotebook();
}

/* ── Stats / status ───────────────────────────────────────────────── */

async function refreshStats() {
  try {
    const r = await fetch("/db/stats");
    const s = await r.json();
    $("stat-problems").textContent = s.problems;
    $("stat-attempts").textContent = s.attempts;
    $("stat-verified").textContent = s.verified_attempts;
    if (s.graph) {
      $("stat-nodes").textContent = s.graph.nodes ?? 0;
      $("stat-edges").textContent = s.graph.edges ?? 0;
    }
    const hyp = s.hypotheses || {};
    const hypTotal = Object.values(hyp).reduce((a, b) => a + b, 0);
    const badge = $("tab-badge-hypotheses");
    if (badge) {
      badge.hidden = hypTotal === 0;
      badge.textContent = String(hypTotal);
      badge.title = JSON.stringify(hyp);
    }
  } catch (_) { /* ignore */ }
}

/* ── Solve tab ────────────────────────────────────────────────────── */

async function refreshRecent() {
  try {
    const r = await fetch("/problems?limit=12");
    const { items } = await r.json();
    const host = $("recent");
    if (!items.length) {
      host.innerHTML = '<div class="subtle">No problems yet. Solve one above.</div>';
      return;
    }
    host.innerHTML = items.map((p) => {
      const q = escapeHtml(p.raw_input || "");
      return `<div class="recent-item" data-id="${p.id}">
        <span class="id">#${p.id}</span>
        <span class="q" title="${q}">${q}</span>
        <span class="tag type">${escapeHtml(p.problem_type)}</span>
        <span class="tag">${escapeHtml(p.source_format)}</span>
        <span class="t">${escapeHtml(p.created_at)}</span>
        <button class="subtle-btn" data-action="similar" data-id="${p.id}">similar</button>
      </div>`;
    }).join("");
    host.querySelectorAll('[data-action="similar"]').forEach((b) => {
      b.addEventListener("click", () => loadSimilarFor(b.dataset.id));
    });
  } catch (_) { /* ignore */ }
}

async function loadSimilarFor(problemId) {
  const r = await fetch(`/problems/${problemId}/similar?k=5`);
  const data = await r.json();
  $("similar-card").hidden = false;
  $("similar-hint").textContent =
    `Top ${data.items.length} similar past problem(s) for #${problemId}.`;
  renderSimilar(data.items);
  $("similar-card").scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderVerificationTag(status) {
  if (!status) return "";
  const cls = VERIFY_CLS(status);
  const label = VERIFY_LABEL(status);
  return `<span class="tag ${cls}">verify: ${label}</span>`;
}

function renderSimilar(items) {
  const host = $("similar");
  if (!items || !items.length) {
    host.innerHTML = '<div class="subtle">No similar past problems yet — first of its kind.</div>';
    return;
  }
  host.innerHTML = items.map((s) => {
    const p = s.problem;
    const a = s.best_attempt;
    const status = a?.verification_status;
    const cls = status === "verified" ? "good" : status === "refuted" ? "bad" : "warn";
    return `<div class="similar-row">
      <span class="score">${fmtNum(s.score, 3)}</span>
      <span class="pretty" title="${escapeHtml(p.parsed_pretty)}">${escapeHtml(p.parsed_pretty)}</span>
      <span class="tag">${escapeHtml(p.problem_type)}</span>
      <span class="tag">${a ? escapeHtml(a.approach) : "—"}</span>
      ${status ? `<span class="tag ${cls}">${escapeHtml(status)}</span>` : '<span class="tag">no verify</span>'}
    </div>`;
  }).join("");
}

function renderOutcome(out) {
  lastSolveOutcome = out;
  $("answer-card").hidden = false;
  $("trace-card").hidden = false;
  $("fp-card").hidden = false;
  $("similar-card").hidden = false;

  const answerText = out.answer_pretty ?? out.error ?? "(no answer)";
  $("answer").textContent = answerText;
  // Phase F: KaTeX-rendered answer (if it's a math expression — refused otherwise)
  const rendered = $("answer-rendered");
  if (rendered) {
    if (out.answer_pretty && !out.error) {
      try {
        // Strip outer brackets for "list of roots" case so KaTeX renders cleanly.
        let src = String(out.answer_pretty);
        if (/^\[.+\]$/.test(src)) {
          // List of roots — render as a comma-separated set.
          src = "\\left\\{" + sympyToLatex(src.slice(1, -1)) + "\\right\\}";
        } else {
          src = sympyToLatex(src);
        }
        renderKaTeXInto(rendered, src, true);
        rendered.hidden = false;
        $("answer").hidden = true;
        $("answer-toggle-render").textContent = "show raw";
      } catch (_) {
        rendered.hidden = true;
        $("answer").hidden = false;
      }
    } else {
      rendered.hidden = true;
      $("answer").hidden = false;
    }
  }

  const meta = [];
  if (out.problem_type) meta.push(`<span class="tag">${escapeHtml(out.problem_type)}</span>`);
  if (out.source_format) meta.push(`<span class="tag">${escapeHtml(out.source_format)}</span>`);
  if (out.approach) meta.push(`<span class="tag">${escapeHtml(out.approach)}</span>`);
  if (Number.isFinite(out.time_ms)) meta.push(`<span class="tag">${out.time_ms.toFixed(1)} ms</span>`);
  meta.push(renderVerificationTag(out.verification_status));
  // Cross-verify badge from the chosen attempt (if any).
  const chosen = (out.attempts || []).find((a) => a.verification_status === "verified")
              || (out.attempts || [])[0];
  if (chosen && chosen.cross_verify_status) {
    meta.push(`<span class="tag ${CV_CLS(chosen.cross_verify_status)}">cross: ${escapeHtml(chosen.cross_verify_status)}${chosen.cross_verify_tool ? " · " + escapeHtml(chosen.cross_verify_tool) : ""}</span>`);
  }
  if (out.problem_id != null) meta.push(`<span class="tag">#${out.problem_id}</span>`);
  $("answer-meta").innerHTML = meta.filter(Boolean).join(" ");

  const items = (out.similar || []);
  $("similar-hint").textContent = items.length
    ? `Surfaced ${items.length} structurally similar past problem(s) before solving — the learner uses their attempts to rank approaches.`
    : "No similar past problems yet — first of its kind in the graph.";
  renderSimilar(items);

  renderAttempts(out);
  renderTrace(out.trace || []);

  $("fp").textContent = JSON.stringify(out.fingerprint, null, 2);
}

/* Phase C: trace timeline — icons, colored bars, decision-step
 * candidate chart, "why this won" pull-out. */
const TRACE_ICONS = {
  parse:        "📝",
  fingerprint:  "🧩",
  retrieval:    "🔗",
  decision:     "⚖️",
  tool_call:    "🛠",
  verify:       "✓",
  cross_verify: "⚖",
  persist:      "💾",
  learn:        "📈",
  graph_update: "🕸",
  rewrite:      "✂",
  auto_scan:    "🔬",
};
function traceIcon(kind) { return TRACE_ICONS[kind] || "•"; }
function traceKindLabel(kind) {
  return (kind || "").replace(/_/g, " ");
}

function traceToMarkdown(out) {
  const lines = [];
  lines.push(`# Solve: ${out.parsed_pretty || out.answer_pretty || "(unknown)"}\n`);
  if (out.problem_id != null) lines.push(`Problem #${out.problem_id}.`);
  if (out.problem_type) lines.push(`Type: \`${out.problem_type}\` · format: \`${out.source_format || "?"}\``);
  if (out.tool && out.approach) {
    lines.push(`Chosen: \`${out.tool}.${out.approach.replace(/^[^.]+\./, "")}\``);
  }
  if (out.verification_status) {
    lines.push(`Verification: **${out.verification_status}** ${out.verification_detail ? `— _${out.verification_detail}_` : ""}`);
  }
  if (out.answer_pretty) {
    lines.push("");
    lines.push("## Answer");
    lines.push("```");
    lines.push(String(out.answer_pretty));
    lines.push("```");
  }
  lines.push("");
  lines.push("## Trace");
  for (const s of (out.trace || [])) {
    lines.push(`- **${traceKindLabel(s.kind)}** — ${s.summary || ""}`);
  }
  return lines.join("\n");
}
let lastSolveOutcome = null;
async function copyTraceAsMarkdown() {
  if (!lastSolveOutcome) return;
  const md = traceToMarkdown(lastSolveOutcome);
  try {
    await navigator.clipboard.writeText(md);
    const b = $("trace-copy-md");
    if (b) {
      const orig = b.textContent;
      b.textContent = "copied ✓";
      setTimeout(() => { b.textContent = orig; }, 1500);
    }
  } catch (_) {
    // Fallback: open prompt with the text
    prompt("Copy this markdown:", md);
  }
}

function renderTrace(steps) {
  const host = $("trace");
  if (!host) return;
  host.innerHTML = steps.map((s, i) => {
    const k = s.kind || "step";
    const cls = `k-${k.replace(/[^a-z_]/gi, "")}`;
    const detail = renderTraceDetail(s);
    return `<li class="trace-step trace-step-${k}">
      <div class="trace-step-rail">
        <div class="trace-step-icon ${cls}">${traceIcon(k)}</div>
        ${i < steps.length - 1 ? '<div class="trace-step-line"></div>' : ""}
      </div>
      <div class="trace-step-body">
        <div class="trace-step-head">
          <span class="kind ${cls}">${escapeHtml(traceKindLabel(k))}</span>
          <span class="summary">${escapeHtml(s.summary || "")}</span>
        </div>
        ${detail}
      </div>
    </li>`;
  }).join("");
}

function renderTraceDetail(step) {
  const d = step.detail;
  if (!d || typeof d !== "object") return "";
  // Decision step: show the candidate table in styled form.
  if (step.kind === "decision" && Array.isArray(d.candidates)) {
    return `<details><summary>candidates · policy ${escapeHtml(d.policy || "?")} · max_attempts ${d.max_attempts ?? "?"}</summary>
      ${renderCandidateTable(d.candidates)}
      ${d.tools_available ? `<div class="subtle" style="margin-top:6px">tools: ${d.tools_available.map((t) => escapeHtml(t)).join(", ")}</div>` : ""}
    </details>`;
  }
  // Other kinds: pretty-print JSON inside a collapsible block.
  return `<details><summary>detail</summary>
    <pre class="detail">${escapeHtml(JSON.stringify(d, null, 2))}</pre>
  </details>`;
}

function renderCandidateTable(candidates) {
  if (!candidates || !candidates.length) return "";
  const max = Math.max(0.001, ...candidates.map((c) => c.score || 0));
  const rows = candidates.map((c, i) => {
    const w = Math.round(((c.score || 0) / max) * 100);
    const sigLabel = c.sig_attempts
      ? `${c.sig_verified}/${c.sig_attempts}`
      : c.type_attempts
        ? `<span class="subtle">type ${c.type_verified}/${c.type_attempts}</span>`
        : `<span class="subtle">unseen</span>`;
    const conf = Number.isFinite(c.confidence) ? c.confidence.toFixed(2) : "—";
    return `<tr class="${i === 0 ? "chosen" : ""}">
      <td>${i + 1}</td>
      <td><span class="approach">${escapeHtml(c.tool || "?")}.${escapeHtml((c.approach || "").replace(/^[^.]+\./, ""))}</span></td>
      <td class="num"><span class="bar"><span style="width:${w}%"></span></span> ${(c.score || 0).toFixed(3)}</td>
      <td class="num">${(c.value || 0).toFixed(2)}</td>
      <td class="num">${(c.bonus || 0).toFixed(2)}</td>
      <td class="num">${conf}</td>
      <td>${sigLabel}</td>
      <td title="${escapeHtml(c.rationale || "")}">${escapeHtml((c.rationale || "").slice(0, 60))}</td>
    </tr>`;
  }).join("");
  return `<div class="candidate-table"><table>
    <thead><tr>
      <th>#</th><th>tool.approach</th><th>score</th>
      <th>value</th><th>bonus</th><th>conf</th><th>sig n/N</th><th>rationale</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderAttempts(out) {
  const card = $("attempts-card");
  const host = $("attempts");
  const attempts = out.attempts || [];
  if (!attempts.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $("attempts-hint").textContent =
    `Multi-attempt loop tried ${attempts.length} approach(es); the verified one is highlighted. Total wall time ${(out.total_time_ms || 0).toFixed(1)} ms.`;
  // The chosen attempt is the verified one if any; otherwise the surfaced approach.
  let chosenIdx = attempts.findIndex((a) => a.verification_status === "verified");
  if (chosenIdx < 0) chosenIdx = attempts.findIndex((a) => a.approach === out.approach);
  host.innerHTML = attempts.map((a, i) => {
    const v = a.verification_status;
    const cv = a.cross_verify_status;
    const cls = i === chosenIdx ? " chosen" : "";
    const result = a.result_pretty ?? a.error ?? "—";
    const cvBadge = cv
      ? `<span class="tag ${CV_CLS(cv)}">cross: ${escapeHtml(cv)}${a.cross_verify_tool ? " · " + escapeHtml(a.cross_verify_tool) : ""}</span>`
      : "";
    return `<div class="attempt-row${cls}">
      <span class="idx">${i + 1}.</span>
      <span class="approach">${escapeHtml(a.tool || "?")}.${escapeHtml((a.approach || "").replace(/^[^.]+\./, ""))}</span>
      <span class="result vresult" title="${escapeHtml(result)}">${escapeHtml(String(result))}</span>
      <span>${tag(v, VERIFY_CLS(v))} ${cvBadge}</span>
      <span class="t">${a.time_ms ? a.time_ms.toFixed(1) + " ms" : ""}</span>
    </div>`;
  }).join("");
}

async function refreshToolsBar() {
  const host = $("tools-bar");
  if (!host) return;
  try {
    const data = await fetch("/tools").then((r) => r.json());
    const items = data.items || [];
    const badges = items.map((t) => {
      const cls = t.available ? "on" : "off";
      return `<span class="tool-badge ${cls}" title="${escapeHtml(t.class || "")}"><span class="dot"></span>${escapeHtml(t.name)}</span>`;
    }).join("");
    host.innerHTML = `<span class="label">tools</span>${badges}`;
  } catch (_) { /* ignore */ }
}

async function submitSolve() {
  let text = $("input").value.trim();
  if (!text) return;
  // Intent-driven client-side wrapping for solve/integrate/differentiate.
  // factor/expand/simplify/limit/evaluate go through the new
  // problem_type override field (handled below).
  let problemType = null;
  if (activeIntent === "solve") {
    text = wrapAsEquation(text);
  } else if (activeIntent === "integrate") {
    text = wrapAsIntegral(text);
  } else if (activeIntent === "differentiate") {
    text = wrapAsDerivative(text);
  } else if (["factor", "expand", "simplify", "limit", "evaluate"].includes(activeIntent)) {
    problemType = activeIntent;
  }
  const btn = $("solve-btn");
  btn.disabled = true;
  $("hint").textContent = "Solving…";
  try {
    const body = { text };
    if (activeSessionId !== null) body.session_id = activeSessionId;
    if (problemType) body.problem_type = problemType;
    const r = await fetch("/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const out = await r.json();
    lastSolvedProblemId = out.problem_id ?? null;
    // reset any stale explain panel
    const ex = $("explain-text");
    if (ex) { ex.hidden = true; ex.textContent = ""; }
    $("explain-status").textContent = "";
    renderOutcome(out);
    $("hint").textContent = out.ok ? "" : "Tool failed — see trace.";
  } catch (err) {
    $("hint").textContent = "Network error: " + err.message;
  } finally {
    btn.disabled = false;
    refreshStats();
    refreshRecent();
    if (activeSessionId !== null) refreshNotebook();
  }
}

/* Intent-driven wrappers: turn a bare expression into the SymPy class
 * the parser dispatches on. Pure client-side — no parser change. */
function wrapAsEquation(text) {
  const t = text.trim();
  if (/^Eq\s*\(/.test(t)) return t;            // already wrapped
  if (t.includes("=") && !t.includes("==")) {
    const idx = t.indexOf("=");
    const lhs = t.slice(0, idx).trim();
    const rhs = t.slice(idx + 1).trim() || "0";
    return `Eq(${lhs}, ${rhs})`;
  }
  return `Eq(${t}, 0)`;
}
function wrapAsIntegral(text) {
  const t = text.trim();
  if (/^Integral\s*\(/.test(t)) return t;
  // No bounds → indefinite over x. The user can write Integral(...) directly
  // to specify limits.
  return `Integral(${t}, x)`;
}
function wrapAsDerivative(text) {
  const t = text.trim();
  if (/^Derivative\s*\(/.test(t)) return t;
  return `Derivative(${t}, x)`;
}

/* ── Phase B: live ranker preview ─────────────────────────────────── */
let rankerPreviewTimer = null;
let lastRankerInput = "";
let lastRankerSignal = null;        // {problem_type, signature, candidates}

function inferProblemTypeForPreview(text, intent) {
  if (intent && intent !== "auto") return intent;
  const t = text.trim();
  if (/^Eq\s*\(/.test(t) || /=/.test(t)) return "solve";
  if (/^Integral\s*\(/.test(t)) return "integrate";
  if (/^Derivative\s*\(/.test(t)) return "differentiate";
  if (/^Limit\s*\(/.test(t)) return "limit";
  if (/^Sum\s*\(/.test(t)) return "evaluate";
  return null;        // unknown; ranker preview not useful
}

async function updateRankerPreview() {
  const text = $("input").value.trim();
  const card = $("ranker-preview-card");
  if (!card) return;
  if (!text) { card.hidden = true; return; }

  const ptype = inferProblemTypeForPreview(text, activeIntent);
  if (!ptype) { card.hidden = true; return; }

  const cacheKey = `${ptype}::${text}`;
  if (cacheKey === lastRankerInput && lastRankerSignal) {
    return;     // already showing
  }
  try {
    // Use type-level rank (no signature) — fast, doesn't require parsing
    // the input on the server. The decision step in the real solve will
    // narrow to signature-specific.
    const r = await fetch(`/learner/rank?problem_type=${encodeURIComponent(ptype)}`);
    if (!r.ok) { card.hidden = true; return; }
    const data = await r.json();
    lastRankerInput = cacheKey;
    lastRankerSignal = data;
    card.hidden = false;
    $("ranker-preview-hint").textContent =
      `Type-level UCB1 ranking for problem_type=${ptype}. The engine will narrow to your specific signature when you click Solve.`;
    $("ranker-preview-body").innerHTML = renderCandidateTable(data.candidates || []);
  } catch (_) {
    card.hidden = true;
  }
}

function scheduleRankerPreview() {
  clearTimeout(rankerPreviewTimer);
  rankerPreviewTimer = setTimeout(updateRankerPreview, 350);
}

/* ── Phase B: replay ──────────────────────────────────────────────── */
async function replayCurrentSolve() {
  // Re-run the same input + intent without persisting anything special.
  // Same code path as submitSolve so the user can compare ranker
  // rationale "before" and "after" learning.
  await submitSolve();
}

/* ── Phase B: demo guided tour ────────────────────────────────────── */
const DEMO_STEPS = [
  {
    title: "Reset learning state",
    body: "Wipe all problems, attempts, and hypotheses. The graph goes back to empty. Click <b>reset learning state</b> at the bottom of this rail. Stats in the topbar should drop to zero.",
    action: "Click <b>reset learning state</b> below.",
  },
  {
    title: "Solve a fresh quadratic",
    body: "Notice the trace's <b>decision</b> step says &quot;<i>unseen — neutral prior 50%</i>&quot;. The engine has no data yet.",
    input: "Eq(x**2 - 5*x + 6, 0)",
    intent: "auto",
  },
  {
    title: "Solve four more quadratics",
    body: "Each new quadratic feeds <code>tool_outcomes</code>. UCB1 explores; later attempts will exploit. The decision rationale shifts from <i>neutral prior</i> to <i>type N/M verified</i>.",
    inputs: [
      "Eq(x**2 - 4*x + 3, 0)",
      "Eq(x**2 - 7*x + 12, 0)",
      "Eq(x**2 - 9*x + 20, 0)",
      "Eq(2*x**2 - 5*x - 3, 0)",
    ],
    intent: "auto",
  },
  {
    title: "Seed an identity pair",
    body: "Solve <code>sin(x)**2 + cos(x)**2</code> with the <b>simplify</b> intent, then solve <code>1</code>. The engine sees two inputs that canonicalize to the same value.",
    inputs: [
      { text: "sin(x)**2 + cos(x)**2", intent: "simplify" },
      { text: "1", intent: "auto" },
    ],
  },
  {
    title: "Run the hypothesizer",
    body: "Switch to the <b>Hypotheses</b> tab and click <b>scan now</b>. The engine will independently propose and prove <code>sin²(x) + cos²(x) ≡ 1</code>. A new <code>rule</code> node appears in the relational graph with <code>uses_rule</code> edges back to the supporting problems.",
    action: "Open the Hypotheses tab and click <b>scan now</b>.",
  },
  {
    title: "Use the learned identity",
    body: "Solve <code>(sin(x)**2 + cos(x)**2)*y</code> with <b>simplify</b>. If the primary attempts can't verify, the rewriter fires (look for a <i>rewrite</i> step in the trace) and substitutes the just-proved identity. Same engine — strictly smarter than two minutes ago.",
    input: "(sin(x)**2 + cos(x)**2)*y",
    intent: "simplify",
  },
];

function renderDemoSteps() {
  const host = $("demo-steps");
  if (!host) return;
  host.innerHTML = DEMO_STEPS.map((s, i) => {
    const inputBtns = [];
    if (s.input) {
      inputBtns.push(`<button class="subtle-btn demo-run" data-step="${i}" data-idx="0">load &amp; solve</button>`);
    } else if (s.inputs) {
      inputBtns.push(`<button class="subtle-btn demo-run-all" data-step="${i}">load &amp; solve all (${s.inputs.length})</button>`);
    }
    return `<li class="demo-step" data-step="${i}">
      <div class="demo-step-num">${i + 1}</div>
      <div class="demo-step-body">
        <div class="demo-step-title">${escapeHtml(s.title)}</div>
        <div class="demo-step-text">${s.body}</div>
        ${s.action ? `<div class="demo-step-action">${s.action}</div>` : ""}
        <div class="demo-step-actions">${inputBtns.join(" ")}</div>
      </div>
    </li>`;
  }).join("");

  // Wire single-input runs
  host.querySelectorAll(".demo-run").forEach((b) => {
    b.addEventListener("click", async () => {
      const step = DEMO_STEPS[Number(b.dataset.step)];
      if (!step || !step.input) return;
      $("input").value = step.input;
      setIntent(step.intent || "auto");
      activateTab("solve");
      await submitSolve();
      markDemoStepDone(Number(b.dataset.step));
    });
  });
  // Wire multi-input runs (sequentially solve all)
  host.querySelectorAll(".demo-run-all").forEach((b) => {
    b.addEventListener("click", async () => {
      const step = DEMO_STEPS[Number(b.dataset.step)];
      if (!step || !step.inputs) return;
      activateTab("solve");
      b.disabled = true;
      const orig = b.textContent;
      for (let i = 0; i < step.inputs.length; i++) {
        const item = step.inputs[i];
        const text = typeof item === "string" ? item : item.text;
        const intent = (typeof item === "string" ? step.intent : item.intent) || step.intent || "auto";
        $("input").value = text;
        setIntent(intent);
        b.textContent = `solving ${i + 1} / ${step.inputs.length}…`;
        await submitSolve();
      }
      b.textContent = orig;
      b.disabled = false;
      markDemoStepDone(Number(b.dataset.step));
    });
  });
}
function markDemoStepDone(idx) {
  const li = document.querySelector(`.demo-step[data-step="${idx}"]`);
  if (li) li.classList.add("done");
}
function openDemoRail() {
  $("demo-rail").hidden = false;
  document.body.classList.add("demo-open");
  renderDemoSteps();
}
function closeDemoRail() {
  $("demo-rail").hidden = true;
  document.body.classList.remove("demo-open");
}
async function demoResetState() {
  if (!confirm("Wipe ALL problems, attempts, hypotheses, and the relational graph?\n\nSessions and config are kept. This cannot be undone.")) return;
  $("demo-status").textContent = "resetting…";
  try {
    const r = await fetch("/db/reset", { method: "POST" });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.detail || "reset failed");
    $("demo-status").textContent = `reset ✓ — ${JSON.stringify(data.counts)}`;
    document.querySelectorAll(".demo-step").forEach((li) => li.classList.remove("done"));
    refreshStats();
    refreshRecent();
    refreshToolsBar();
    if (typeof refreshHypotheses === "function") refreshHypotheses();
    // Hide answer/trace from previous session
    ["answer-card","similar-card","attempts-card","trace-card","fp-card","ranker-preview-card"]
      .forEach((id) => { const c = $(id); if (c) c.hidden = true; });
  } catch (err) {
    $("demo-status").textContent = "reset failed: " + err.message;
  }
}

/* Examples drawer (Phase A) */
function renderExamples() {
  const host = $("examples-list");
  if (!host) return;
  const items = EXAMPLES[activeExampleCat] || [];
  host.innerHTML = items.map((ex, i) => {
    return `<button class="example-item" data-idx="${i}" type="button">
      <code>${escapeHtml(ex.input)}</code>
      ${ex.note ? `<span class="ex-note">${escapeHtml(ex.note)}</span>` : ""}
      ${ex.intent && ex.intent !== "auto"
        ? `<span class="ex-intent">intent: ${escapeHtml(ex.intent)}</span>`
        : ""}
    </button>`;
  }).join("");
  host.querySelectorAll(".example-item").forEach((b) => {
    b.addEventListener("click", () => {
      const idx = Number(b.dataset.idx);
      const ex = items[idx];
      if (!ex) return;
      $("input").value = ex.input;
      setIntent(ex.intent || "auto");
      $("input").focus();
    });
  });
}
function setExampleCategory(cat) {
  activeExampleCat = cat;
  document.querySelectorAll(".ex-tab").forEach((b) => {
    b.classList.toggle("active", b.dataset.cat === cat);
  });
  renderExamples();
}
function setIntent(intent) {
  activeIntent = intent;
  document.querySelectorAll(".intent-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.intent === intent);
  });
}
function wireIntentAndExamples() {
  document.querySelectorAll(".ex-tab").forEach((b) => {
    b.addEventListener("click", () => setExampleCategory(b.dataset.cat));
  });
  document.querySelectorAll(".intent-btn").forEach((b) => {
    b.addEventListener("click", () => setIntent(b.dataset.intent));
  });
  const cs = $("cheatsheet-toggle");
  if (cs) {
    cs.addEventListener("click", () => {
      const sheet = $("cheatsheet");
      sheet.hidden = !sheet.hidden;
      cs.textContent = sheet.hidden ? "syntax help" : "hide help";
    });
  }
}

/* ── Graph tab ────────────────────────────────────────────────────── */

let cy = null;

const NODE_STYLE = [
  { selector: 'node[kind="problem"]',      style: { 'background-color': '#7c9cff', 'label': 'data(label)', 'color': '#e8ebf0', 'font-size': 9, 'text-wrap': 'wrap', 'text-max-width': 130, 'text-valign': 'bottom', 'text-margin-y': 4, 'width': 22, 'height': 22 } },
  { selector: 'node[kind="tool"]',         style: { 'background-color': '#7cf0c2', 'shape': 'round-rectangle', 'label': 'data(label)', 'color': '#0b0d12', 'font-weight': 600, 'font-size': 11, 'text-valign': 'center', 'text-halign': 'center', 'width': 'label', 'height': 28, 'padding': 8 } },
  { selector: 'node[kind="problem_type"]', style: { 'background-color': '#ffb078', 'shape': 'round-rectangle', 'label': 'data(label)', 'color': '#0b0d12', 'font-weight': 600, 'font-size': 11, 'text-valign': 'center', 'text-halign': 'center', 'width': 'label', 'height': 26, 'padding': 8 } },
  { selector: 'node[kind="signature"]',    style: { 'background-color': '#b78cff', 'shape': 'diamond', 'label': 'data(label)', 'color': '#e8ebf0', 'font-size': 9, 'text-valign': 'bottom', 'text-margin-y': 4, 'width': 16, 'height': 16 } },
  { selector: 'node[kind="rule"]',         style: { 'background-color': '#ff8591', 'shape': 'triangle', 'label': 'data(label)', 'font-size': 10, 'text-valign': 'bottom', 'text-margin-y': 4 } },
  { selector: 'node:selected',             style: { 'border-color': '#fff', 'border-width': 2 } },

  { selector: 'edge', style: {
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'line-color': 'rgba(255,255,255,0.18)',
      'target-arrow-color': 'rgba(255,255,255,0.18)',
      'width': 1,
  }},
  { selector: 'edge[kind="similar_to"]', style: {
      'line-color': 'rgba(124,156,255,0.5)',
      'target-arrow-shape': 'none',
      'curve-style': 'haystack',
      'width': 'mapData(weight, 0.5, 1, 1, 4)',
  }},
  { selector: 'edge[kind="solved_by"]', style: {
      'line-color': 'rgba(124,240,194,0.5)',
      'target-arrow-color': 'rgba(124,240,194,0.5)',
      'width': 1.5,
  }},
  { selector: 'edge[kind="has_type"]', style: {
      'line-color': 'rgba(255,176,120,0.4)',
      'target-arrow-color': 'rgba(255,176,120,0.4)',
  }},
  { selector: 'edge[kind="has_signature"]', style: {
      'line-color': 'rgba(183,140,255,0.35)',
      'target-arrow-color': 'rgba(183,140,255,0.35)',
      'line-style': 'dashed',
  }},
];

function ensureCy() {
  if (cy) return cy;
  cy = cytoscape({
    container: $("cy"),
    style: NODE_STYLE,
    layout: { name: 'cose', animate: false, idealEdgeLength: 90, nodeRepulsion: 6000 },
    wheelSensitivity: 0.2,
  });
  cy.on('tap', 'node', (evt) => renderGraphDetail(evt.target.data()));
  cy.on('tap', 'edge', (evt) => renderGraphDetail(evt.target.data()));
  return cy;
}

function renderGraphDetail(data) {
  const host = $("graph-detail");
  const lines = Object.entries(data || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => {
      const val = typeof v === "object" ? JSON.stringify(v) : String(v);
      return `<div><span class="label">${escapeHtml(k)}</span><span class="value">${escapeHtml(val)}</span></div>`;
    });
  host.innerHTML = lines.length ? lines.join("") : '<span class="subtle">empty</span>';
}

function applyGraphFilters() {
  if (!cy) return;
  const showSimilar = $("graph-show-similar").checked;
  const showTypes = $("graph-show-types").checked;
  const showTools = $("graph-show-tools").checked;
  const showSigs = $("graph-show-sigs").checked;

  cy.batch(() => {
    cy.elements().removeClass("hidden").style("display", "element");
    if (!showSimilar) cy.edges('[kind="similar_to"]').style("display", "none");
    if (!showTypes) {
      cy.nodes('[kind="problem_type"]').style("display", "none");
      cy.edges('[kind="has_type"]').style("display", "none");
    }
    if (!showTools) {
      cy.nodes('[kind="tool"]').style("display", "none");
      cy.edges('[kind="solved_by"]').style("display", "none");
    }
    if (!showSigs) {
      cy.nodes('[kind="signature"]').style("display", "none");
      cy.edges('[kind="has_signature"]').style("display", "none");
    }
  });
}

async function refreshGraph() {
  try {
    const r = await fetch("/graph?max_problems=200");
    const data = await r.json();
    const c = ensureCy();
    c.elements().remove();
    c.add(data.nodes.concat(data.edges));
    c.layout({ name: 'cose', animate: false, idealEdgeLength: 90, nodeRepulsion: 6000 }).run();
    applyGraphFilters();
  } catch (err) {
    $("graph-detail").textContent = "Failed to load graph: " + err.message;
  }
}

/* ── Database tab ─────────────────────────────────────────────────── */

let currentDbView = "problems";
let currentDbRows = [];
let currentSort = { key: null, dir: 1 };

const DB_COLUMNS = {
  problems: ["id", "raw_input", "source_format", "problem_type", "parsed_pretty", "signature", "created_at"],
  attempts: ["id", "problem_id", "tool", "approach", "success", "result_pretty", "verification_status", "time_ms", "error", "created_at"],
  tool_outcomes: ["signature", "tool", "approach", "n_attempts", "n_success", "n_verified", "success_rate", "verify_rate", "avg_time_ms", "updated_at"],
};

async function loadDbTable(view) {
  currentDbView = view;
  document.querySelectorAll(".db-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.db === view);
  });
  const url = view === "problems" ? "/problems?limit=500"
            : view === "attempts" ? "/attempts?limit=500"
            : "/tool_outcomes?limit=500";
  try {
    const r = await fetch(url);
    const data = await r.json();
    currentDbRows = data.items || [];
    renderDbTable();
  } catch (err) {
    $("db-table").innerHTML = `<div class="subtle" style="padding:12px">${escapeHtml(err.message)}</div>`;
  }
}

function renderDbTable() {
  const cols = DB_COLUMNS[currentDbView];
  const filter = ($("db-filter").value || "").toLowerCase();
  let rows = currentDbRows;
  if (filter) {
    rows = rows.filter((r) =>
      cols.some((c) => String(r[c] ?? "").toLowerCase().includes(filter))
    );
  }
  if (currentSort.key) {
    const k = currentSort.key, dir = currentSort.dir;
    rows = rows.slice().sort((a, b) => {
      const av = a[k], bv = b[k];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }
  const head = "<tr>" + cols.map((c) => {
    const sortedCls = currentSort.key === c ? "sorted" : "";
    return `<th class="${sortedCls}" data-col="${c}">${c}${currentSort.key === c ? (currentSort.dir > 0 ? " ↑" : " ↓") : ""}</th>`;
  }).join("") + "</tr>";
  const body = rows.map((r) => {
    return "<tr>" + cols.map((c) => {
      let v = r[c];
      if (typeof v === "number" && !Number.isInteger(v)) v = v.toFixed(3);
      return `<td title="${escapeHtml(v ?? "")}">${escapeHtml(v ?? "")}</td>`;
    }).join("") + "</tr>";
  }).join("");
  $("db-table").innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
  $("db-table").querySelectorAll("th").forEach((th) => {
    th.addEventListener("click", () => {
      const col = th.dataset.col;
      if (currentSort.key === col) currentSort.dir *= -1;
      else { currentSort.key = col; currentSort.dir = 1; }
      renderDbTable();
    });
  });
}

/* ── Insights tab ─────────────────────────────────────────────────── */

const CHARTS = {};

const CHART_COLORS = {
  accent:  "#7c9cff",
  accent2: "#7cf0c2",
  warn:    "#ffb078",
  bad:     "#ff8591",
  muted:   "rgba(232,235,240,0.18)",
  text:    "#9aa3b2",
};

function chartDefaults() {
  return {
    plugins: { legend: { labels: { color: CHART_COLORS.text, font: { size: 10 } } } },
    scales: {
      x: { ticks: { color: CHART_COLORS.text, font: { size: 9 } },
           grid: { color: CHART_COLORS.muted } },
      y: { ticks: { color: CHART_COLORS.text, font: { size: 9 } },
           grid: { color: CHART_COLORS.muted } },
    },
    responsive: true,
    maintainAspectRatio: false,
  };
}

function upsertChart(canvasId, type, data, options) {
  const ctx = $(canvasId);
  if (!ctx) return;
  if (CHARTS[canvasId]) {
    CHARTS[canvasId].data = data;
    CHARTS[canvasId].options = options || CHARTS[canvasId].options;
    CHARTS[canvasId].update();
    return;
  }
  // eslint-disable-next-line no-undef
  CHARTS[canvasId] = new Chart(ctx, { type, data, options });
}

function rollingVerifyRate(timeline, window = 20) {
  // timeline is newest-first; reverse so we plot left-to-right in time.
  const rows = timeline.slice().reverse();
  const out = { labels: [], rates: [] };
  let q = [];
  rows.forEach((r, i) => {
    q.push(r.verification_status === "verified" ? 1 : 0);
    if (q.length > window) q.shift();
    out.labels.push(`#${r.id}`);
    out.rates.push(q.reduce((a, b) => a + b, 0) / q.length);
  });
  return out;
}

function attemptsPerProblem(timeline) {
  // group recent attempts by problem_id, plot a moving avg of attempt-counts.
  const rows = timeline.slice().reverse();
  const counts = {};
  const order = [];
  rows.forEach((r) => {
    if (counts[r.problem_id] === undefined) order.push(r.problem_id);
    counts[r.problem_id] = (counts[r.problem_id] || 0) + 1;
  });
  const labels = order.map((pid) => `#${pid}`);
  const values = order.map((pid) => counts[pid]);
  // 5-problem moving average for a cleaner trend line.
  const window = 5;
  const avg = values.map((_, i) => {
    const slice = values.slice(Math.max(0, i - window + 1), i + 1);
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  });
  return { labels, values, avg };
}

function timeByType(timeline) {
  const sums = {};
  const counts = {};
  timeline.forEach((r) => {
    const k = r.problem_type || "unknown";
    sums[k] = (sums[k] || 0) + (r.time_ms || 0);
    counts[k] = (counts[k] || 0) + 1;
  });
  const types = Object.keys(sums).sort();
  return {
    labels: types,
    values: types.map((k) => counts[k] ? sums[k] / counts[k] : 0),
  };
}

function bar(label, value, max, suffix = "") {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return `<div class="bar-row">
    <span class="label" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
    <span class="bar"><span style="width:${pct}%"></span></span>
    <span class="v">${escapeHtml(String(value))}${suffix}</span>
  </div>`;
}

async function refreshInsights() {
  try {
    const [stats, outcomes, timeline, recent] = await Promise.all([
      fetch("/db/stats").then((r) => r.json()),
      fetch("/tool_outcomes?limit=80").then((r) => r.json()),
      fetch("/attempts/timeline?limit=500").then((r) => r.json()),
      fetch("/problems?limit=200").then((r) => r.json()),
    ]);

    // ── Chart 1: rolling verify rate over recent attempts ────────────
    const tl = (timeline.items || []);
    if (tl.length) {
      const rv = rollingVerifyRate(tl, 20);
      upsertChart("chart-verify-time", "line", {
        labels: rv.labels,
        datasets: [{
          label: "rolling verify rate (window 20)",
          data: rv.rates,
          borderColor: CHART_COLORS.accent2,
          backgroundColor: "rgba(124,240,194,0.10)",
          borderWidth: 2,
          tension: 0.3,
          pointRadius: 0,
          fill: true,
        }],
      }, {
        ...chartDefaults(),
        scales: {
          ...chartDefaults().scales,
          y: { ...chartDefaults().scales.y, min: 0, max: 1,
               ticks: { ...chartDefaults().scales.y.ticks,
                        callback: (v) => Math.round(v * 100) + "%" } },
        },
      });
    }

    // ── Chart 2: attempts-per-problem trend ──────────────────────────
    if (tl.length) {
      const ap = attemptsPerProblem(tl);
      upsertChart("chart-attempts-per-problem", "bar", {
        labels: ap.labels,
        datasets: [
          {
            label: "attempts per problem",
            data: ap.values,
            backgroundColor: "rgba(124,156,255,0.45)",
            borderColor: CHART_COLORS.accent,
            borderWidth: 1,
          },
          {
            type: "line",
            label: "5-problem moving avg",
            data: ap.avg,
            borderColor: CHART_COLORS.warn,
            backgroundColor: "transparent",
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 0,
          },
        ],
      }, {
        ...chartDefaults(),
        scales: {
          ...chartDefaults().scales,
          y: { ...chartDefaults().scales.y, min: 0, suggestedMax: 3 },
        },
      });
    }

    // ── Chart 3: average solve time by problem type ──────────────────
    if (tl.length) {
      const tt = timeByType(tl);
      upsertChart("chart-time-by-type", "bar", {
        labels: tt.labels,
        datasets: [{
          label: "avg time (ms)",
          data: tt.values,
          backgroundColor: "rgba(183,140,255,0.45)",
          borderColor: "#b78cff",
          borderWidth: 1,
        }],
      }, chartDefaults());
    }

    // ── Bars: per-(sig, approach) verify rates ───────────────────────
    const items = outcomes.items || [];
    if (!items.length) {
      $("insights-tools").innerHTML = '<span class="subtle">No data yet.</span>';
    } else {
      $("insights-tools").innerHTML = items.slice(0, 16).map((o) => {
        const label = `${o.tool}.${(o.approach || "").replace(/^[^.]+\./, "")} · sig ${(o.signature || "").slice(0, 8)}`;
        const p = Math.round((o.verify_rate || 0) * 100);
        const fails = (o.failure_modes || []).slice(-3).join(", ");
        return `<div class="bar-row">
          <span class="label" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
          <span class="bar"><span style="width:${p}%"></span></span>
          <span class="v">${p}% · ${o.n_attempts}×${fails ? ` · ${escapeHtml(fails)}` : ""}</span>
        </div>`;
      }).join("");
    }

    // ── Bars: by problem type ────────────────────────────────────────
    const types = stats.by_problem_type || {};
    const tmax = Math.max(1, ...Object.values(types));
    $("insights-types").innerHTML = Object.entries(types)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => bar(k, v, tmax)).join("") || '<span class="subtle">No data yet.</span>';

    // ── Bars: by source format (derived from /problems) ─────────────
    const bySource = {};
    (recent.items || []).forEach((p) => {
      bySource[p.source_format] = (bySource[p.source_format] || 0) + 1;
    });
    const smax = Math.max(1, ...Object.values(bySource));
    $("insights-sources").innerHTML = Object.entries(bySource)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => bar(k, v, smax)).join("") || '<span class="subtle">No data yet.</span>';
  } catch (err) {
    $("insights-tools").innerHTML = `<span class="subtle">Failed to load insights: ${escapeHtml(err.message)}</span>`;
  }
}

/* ── Sessions + Explain (Phase 8) ─────────────────────────────────── */

let activeSessionId = null;
let sessionsCache = [];

function getActiveSession() {
  return sessionsCache.find((s) => s.id === activeSessionId) || null;
}

async function refreshSessions() {
  try {
    const r = await fetch("/sessions");
    sessionsCache = (await r.json()).items || [];
  } catch (_) {
    sessionsCache = [];
  }
  const sel = $("session-select");
  if (!sel) return;
  sel.innerHTML = '<option value="">(none — global)</option>' +
    sessionsCache.map((s) =>
      `<option value="${s.id}">#${s.id} ${escapeHtml(s.title)}</option>`).join("");
  if (activeSessionId !== null) sel.value = String(activeSessionId);
  renderActiveSessionPanel();
}

function renderActiveSessionPanel() {
  const active = getActiveSession();
  $("session-rename").hidden = !active;
  $("session-delete").hidden = !active;
  const notes = $("session-notes");
  const notesRow = $("session-notes-row");
  if (!active) {
    notes.hidden = true; notesRow.hidden = true;
    $("session-title").value = "";
    return;
  }
  notes.hidden = false; notesRow.hidden = false;
  notes.value = active.notes_markdown || "";
  $("session-title").value = active.title || "";
  $("session-status").textContent = "";
}

async function onSessionSelect() {
  const v = $("session-select").value;
  activeSessionId = v ? parseInt(v, 10) : null;
  renderActiveSessionPanel();
  // Phase 11: keep the notebook in lockstep with the dropdown.
  refreshNotebook();
}

async function createSession() {
  const titleInput = $("session-title");
  const title = (titleInput.value || "").trim() || "Untitled session";
  try {
    const r = await fetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!r.ok) throw new Error(await r.text());
    const created = await r.json();
    activeSessionId = created.id;
    titleInput.value = "";
    await refreshSessions();
    refreshNotebook();
  } catch (err) {
    $("session-status").textContent = "create failed: " + err.message;
  }
}

async function renameSession() {
  const active = getActiveSession();
  if (!active) return;
  const title = ($("session-title").value || "").trim();
  if (!title) return;
  try {
    const r = await fetch(`/sessions/${active.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!r.ok) throw new Error(await r.text());
    await refreshSessions();
    refreshNotebook();
    $("session-status").textContent = "renamed.";
  } catch (err) {
    $("session-status").textContent = "rename failed: " + err.message;
  }
}

async function deleteSession() {
  const active = getActiveSession();
  if (!active) return;
  if (!confirm(`Delete session #${active.id} "${active.title}"? Linked problems stay but are unlinked.`)) return;
  try {
    const r = await fetch(`/sessions/${active.id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(await r.text());
    activeSessionId = null;
    await refreshSessions();
    refreshRecent();
    refreshNotebook();
  } catch (err) {
    $("session-status").textContent = "delete failed: " + err.message;
  }
}

async function saveSessionNotes() {
  const active = getActiveSession();
  if (!active) return;
  const notes = $("session-notes").value;
  $("session-status").textContent = "saving…";
  try {
    const r = await fetch(`/sessions/${active.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes_markdown: notes }),
    });
    if (!r.ok) throw new Error(await r.text());
    await refreshSessions();
    refreshNotebook();
    $("session-status").textContent = "saved.";
  } catch (err) {
    $("session-status").textContent = "save failed: " + err.message;
  }
}

async function explainCurrentAnswer() {
  if (!lastSolvedProblemId) return;
  const btn = $("explain-btn");
  const status = $("explain-status");
  const out = $("explain-text");
  btn.disabled = true;
  status.textContent = "asking…";
  try {
    const r = await fetch(`/explain/${lastSolvedProblemId}`, { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    out.hidden = false;
    out.innerHTML = escapeHtml(data.text || "(no narration)") +
      `<span class="src">source: ${escapeHtml(data.source || "?")}` +
      (data.model ? ` · ${escapeHtml(data.model)}` : "") +
      (data.reason ? ` · ${escapeHtml(data.reason)}` : "") + "</span>";
    status.textContent = "";
  } catch (err) {
    status.textContent = "explain failed: " + err.message;
  } finally {
    btn.disabled = false;
  }
}

let lastSolvedProblemId = null;

/* ── Notebook tab (Phase 11) ──────────────────────────────────────── */

let notebookEditingNotes = false;
let notebookOriginalNotes = "";

function renderMarkdown(text) {
  if (!text || !text.trim()) {
    return '<div class="empty-note">No notes yet — click "edit" to add some.</div>';
  }
  if (typeof marked !== "undefined") {
    try {
      return marked.parse(text, { breaks: true });
    } catch (_) { /* fall through */ }
  }
  // Fallback: just show the raw text safely-escaped.
  return `<pre>${escapeHtml(text)}</pre>`;
}

async function refreshNotebook() {
  const empty = $("notebook-empty");
  const body = $("notebook-body");
  const badge = $("tab-badge-notebook");

  if (activeSessionId == null) {
    if (empty) empty.hidden = false;
    if (body) body.hidden = true;
    if (badge) badge.hidden = true;
    return;
  }

  try {
    const data = await fetch(`/sessions/${activeSessionId}`).then((r) => r.json());
    if (!data || !data.session) {
      if (empty) empty.hidden = false;
      if (body) body.hidden = true;
      return;
    }
    if (empty) empty.hidden = true;
    if (body) body.hidden = false;

    const s = data.session;
    $("notebook-title").textContent = s.title || `Session #${s.id}`;
    $("notebook-meta").textContent =
      `#${s.id} · updated ${s.updated_at} · ${(data.problems || []).length} problem(s)`;

    if (badge) {
      const n = (data.problems || []).length;
      badge.hidden = n === 0;
      badge.textContent = String(n);
    }

    notebookOriginalNotes = s.notes_markdown || "";
    if (!notebookEditingNotes) {
      $("notebook-notes-rendered").innerHTML = renderMarkdown(notebookOriginalNotes);
      $("notebook-notes-editor").value = notebookOriginalNotes;
    }

    await renderNotebookProblems(data.problems || []);
  } catch (err) {
    if (empty) empty.hidden = false;
    if (body) body.hidden = true;
  }
}

async function renderNotebookProblems(problems) {
  const host = $("notebook-problems");
  if (!host) return;
  if (!problems.length) {
    host.innerHTML =
      '<div class="subtle" style="padding:12px">No problems attached to this session yet. Solve one in the Solve tab while this session is selected, or click "+ new problem".</div>';
    return;
  }

  // Pull every problem's attempts in parallel.
  const detailed = await Promise.all(problems.map(async (p) => {
    try {
      const r = await fetch(`/problems/${p.id}`).then((r) => r.json());
      return { problem: p, attempts: (r.attempts || []) };
    } catch (_) {
      return { problem: p, attempts: [] };
    }
  }));

  // Build the session-options list once for the move-to-session dropdowns.
  const sessionOpts = ['<option value="">(detach)</option>']
    .concat(sessionsCache.map((s) =>
      `<option value="${s.id}"${s.id === activeSessionId ? " selected" : ""}>#${s.id} ${escapeHtml(s.title)}</option>`));

  host.innerHTML = detailed.map(({ problem, attempts }) =>
    notebookProblemCard(problem, attempts, sessionOpts.join(""))
  ).join("");

  // Wire up actions (event delegation would also work; per-card handlers
  // are easier to follow here).
  host.querySelectorAll('[data-action]').forEach((el) => {
    el.addEventListener("click", onNotebookAction);
  });
  host.querySelectorAll('[data-action-change]').forEach((el) => {
    el.addEventListener("change", onNotebookAction);
  });
}

function notebookProblemCard(problem, attempts, sessionOptions) {
  const chosen = attempts.find((a) => a.verification_status === "verified")
              || attempts[attempts.length - 1] || {};
  const v = chosen.verification_status;
  const cv = chosen.cross_verify_status;
  const cvBadge = cv
    ? `<span class="tag ${CV_CLS(cv)}">cross: ${escapeHtml(cv)}${chosen.cross_verify_tool ? " · " + escapeHtml(chosen.cross_verify_tool) : ""}</span>`
    : "";
  const ans = chosen.result_pretty ?? chosen.error ?? "(no answer)";
  return `<article class="nb-problem" data-pid="${problem.id}">
    <div class="nb-problem-header">
      <span class="id">#${problem.id}</span>
      <span class="raw" title="${escapeHtml(problem.raw_input || "")}">${escapeHtml(problem.raw_input || "")}</span>
      <span class="when">${escapeHtml(problem.created_at || "")}</span>
    </div>
    <div class="nb-problem-answer">${escapeHtml(String(ans))}</div>
    <div class="nb-problem-meta">
      ${tag(problem.problem_type)}
      ${tag(problem.source_format)}
      ${tag(chosen.tool ? `${chosen.tool}.${(chosen.approach || "").replace(/^[^.]+\./, "")}` : "")}
      ${v ? tag(v, VERIFY_CLS(v)) : ""}
      ${cvBadge}
      ${tag(`${attempts.length} attempt${attempts.length === 1 ? "" : "s"}`)}
    </div>
    <div class="nb-problem-actions">
      <button class="subtle-btn" data-action="explain" data-pid="${problem.id}">explain</button>
      <button class="subtle-btn" data-action="similar" data-pid="${problem.id}">similar</button>
      <select data-action-change="move-session" data-pid="${problem.id}">${sessionOptions}</select>
      <button class="subtle-btn" data-action="delete" data-pid="${problem.id}"
              style="margin-left:auto;color:var(--bad)">delete</button>
    </div>
    <div class="nb-explain" id="nb-explain-${problem.id}" hidden></div>
  </article>`;
}

async function onNotebookAction(e) {
  const el = e.currentTarget;
  const action = el.dataset.action || el.dataset.actionChange;
  const pid = el.dataset.pid;
  if (!action || !pid) return;

  if (action === "explain") {
    const out = $(`nb-explain-${pid}`);
    out.hidden = false;
    out.textContent = "asking…";
    try {
      const r = await fetch(`/explain/${pid}`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      out.innerHTML = escapeHtml(data.text || "(no narration)") +
        `<span class="src">source: ${escapeHtml(data.source || "?")}` +
        (data.model ? ` · ${escapeHtml(data.model)}` : "") +
        (data.reason ? ` · ${escapeHtml(data.reason)}` : "") + "</span>";
    } catch (err) {
      out.textContent = "explain failed: " + err.message;
    }
    return;
  }

  if (action === "similar") {
    activateTab("solve");
    loadSimilarFor(pid);
    return;
  }

  if (action === "delete") {
    if (!confirm(`Delete problem #${pid}? Its attempts go too. The hypothesis row stays but loses one supporter.`)) return;
    try {
      const r = await fetch(`/problems/${pid}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      refreshStats();
      refreshRecent();
      refreshNotebook();
    } catch (err) {
      toast("Delete failed: " + err.message, "error");
    }
    return;
  }

  if (action === "move-session") {
    const target = el.value;
    const newSid = target === "" ? null : parseInt(target, 10);
    try {
      const r = await fetch(`/problems/${pid}/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: newSid }),
      });
      if (!r.ok) throw new Error(await r.text());
      refreshNotebook();
    } catch (err) {
      toast("Move failed: " + err.message, "error");
    }
    return;
  }
}

function startNotesEdit() {
  notebookEditingNotes = true;
  $("notebook-notes-rendered").hidden = true;
  $("notebook-notes-editor").hidden = false;
  $("notebook-notes-edit").hidden = true;
  $("notebook-notes-save").hidden = false;
  $("notebook-notes-cancel").hidden = false;
  $("notebook-notes-editor").focus();
}

function cancelNotesEdit() {
  notebookEditingNotes = false;
  $("notebook-notes-editor").value = notebookOriginalNotes;
  $("notebook-notes-editor").hidden = true;
  $("notebook-notes-rendered").hidden = false;
  $("notebook-notes-edit").hidden = false;
  $("notebook-notes-save").hidden = true;
  $("notebook-notes-cancel").hidden = true;
}

async function saveNotesEdit() {
  if (activeSessionId == null) return;
  const notes = $("notebook-notes-editor").value;
  try {
    const r = await fetch(`/sessions/${activeSessionId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes_markdown: notes }),
    });
    if (!r.ok) throw new Error(await r.text());
    notebookOriginalNotes = notes;
    notebookEditingNotes = false;
    $("notebook-notes-rendered").innerHTML = renderMarkdown(notes);
    $("notebook-notes-editor").hidden = true;
    $("notebook-notes-rendered").hidden = false;
    $("notebook-notes-edit").hidden = false;
    $("notebook-notes-save").hidden = true;
    $("notebook-notes-cancel").hidden = true;
    // Sync the small Solve-tab notes textarea + sessionsCache.
    await refreshSessions();
  } catch (err) {
    toast("Save failed: " + err.message, "error");
  }
}

async function attachLastSolveToSession() {
  if (activeSessionId == null || lastSolvedProblemId == null) {
    toast("Solve a problem first, then come back to this session.", "warn");
    return;
  }
  try {
    const r = await fetch(`/problems/${lastSolvedProblemId}/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: activeSessionId }),
    });
    if (!r.ok) throw new Error(await r.text());
    refreshNotebook();
  } catch (err) {
    toast("Attach failed: " + err.message, "error");
  }
}

async function exportSessionBundle() {
  if (activeSessionId == null) return;
  try {
    const r = await fetch(`/sessions/${activeSessionId}/export`);
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    const blob = new Blob([JSON.stringify(data, null, 2)],
                          { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const slug = (data.session?.title || "session").replace(/\s+/g, "-").toLowerCase();
    a.href = url;
    a.download = `pru-session-${slug}-${stamp}.json`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    toast("Export failed: " + err.message, "error");
  }
}

function jumpToSolveForNewProblem() {
  activateTab("solve");
  $("input")?.focus();
}

/* ── Settings modal (Phase 6) ─────────────────────────────────────── */

const SETTING_TYPES = {
  max_attempts:         "int",
  similar_top_k:        "int",
  auto_scan_every_n:    "int",
  learner_exploration:  "float",
  similarity_threshold: "float",
  tool_timeout_s:       "float",
  cross_verify:         "bool",
  ollama_enabled:       "bool",
  ollama_model:         "str",
};

async function openSettings() {
  const data = await fetch("/config").then((r) => r.json());
  const form = $("settings-form");
  const keys = data.settable_keys || Object.keys(SETTING_TYPES);
  form.innerHTML = keys.map((k) => {
    const t = SETTING_TYPES[k] || "str";
    const v = data[k];
    if (t === "bool") {
      const checked = v ? "checked" : "";
      return `<label>${escapeHtml(k)}
        <input type="checkbox" data-key="${k}" data-type="bool" ${checked}/>
      </label>`;
    }
    return `<label>${escapeHtml(k)}
      <input data-key="${k}" data-type="${t}" value="${escapeHtml(String(v ?? ""))}"/>
    </label>`;
  }).join("");
  $("settings-status").textContent = "";
  $("settings-modal").hidden = false;
}

function closeSettings() {
  $("settings-modal").hidden = true;
}

async function saveSettings() {
  const fields = $("settings-form").querySelectorAll("[data-key]");
  const updates = {};
  fields.forEach((el) => {
    const k = el.dataset.key;
    const t = el.dataset.type;
    if (t === "bool") updates[k] = el.checked;
    else if (t === "int") updates[k] = parseInt(el.value, 10);
    else if (t === "float") updates[k] = parseFloat(el.value);
    else updates[k] = el.value;
  });
  $("settings-status").textContent = "saving…";
  try {
    const r = await fetch("/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!r.ok) {
      const msg = await r.text();
      throw new Error(`HTTP ${r.status}: ${msg}`);
    }
    $("settings-status").textContent = "saved.";
    setTimeout(closeSettings, 600);
    refreshStats();
  } catch (err) {
    $("settings-status").textContent = "save failed: " + err.message;
  }
}

async function resetSettings() {
  if (!confirm("Revert every setting to the env-loaded defaults?")) return;
  await fetch("/config/reset", { method: "POST" });
  await openSettings();
}

/* ── Database export / import (Phase 6) ──────────────────────────── */

async function exportDb() {
  try {
    const r = await fetch("/db/export");
    const data = await r.json();
    const blob = new Blob([JSON.stringify(data, null, 2)],
                          { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    a.href = url;
    a.download = `pru-math-export-${stamp}.json`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    toast("Export failed: " + err.message, "error");
  }
}

function importDbFile(file) {
  if (!file) return;
  if (!confirm(`Replace the entire database with ${file.name}? This cannot be undone.`)) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const body = JSON.parse(reader.result);
      const r = await fetch("/db/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const msg = await r.text();
        throw new Error(`HTTP ${r.status}: ${msg}`);
      }
      const out = await r.json();
      toast("Imported " + JSON.stringify(out.counts), "success");
      refreshStats();
      refreshRecent();
      if (currentDbView) loadDbTable(currentDbView);
    } catch (err) {
      toast("Import failed: " + err.message, "error");
    }
  };
  reader.readAsText(file);
}

/* ── Hypotheses tab (Phase 5) ─────────────────────────────────────── */

/* ── Phase D: SymPy text → LaTeX (lightweight) ────────────────────── */
function sympyToLatex(s) {
  if (s == null) return "";
  let t = String(s);
  // Constants
  t = t.replace(/\boo\b/g, "\\infty");
  t = t.replace(/\bpi\b/g, "\\pi");
  t = t.replace(/\bE\b(?![a-z_(])/g, "e");
  t = t.replace(/\bI\b(?![a-z_(])/g, "i");
  // sqrt(x) → \sqrt{x}  (single nesting depth)
  t = t.replace(/\bsqrt\s*\(([^()]*)\)/g, "\\sqrt{$1}");
  // Function names: sin cos tan asin acos atan sinh cosh tanh exp log ln
  t = t.replace(/\b(sin|cos|tan|asin|acos|atan|sinh|cosh|tanh|exp|log|ln|sec|csc|cot)\b/g, "\\$1");
  // a**b — wrap exponent in braces. Multi-char exponents need braces.
  t = t.replace(/\*\*\s*\(([^()]+)\)/g, "^{$1}");
  t = t.replace(/\*\*\s*([A-Za-z]\w*|\d+)/g, "^{$1}");
  // Multiplication: turn `*` into a thin space (KaTeX juxtaposition is implicit)
  t = t.replace(/\s*\*\s*/g, " \\, ");
  return t;
}

function renderKaTeXInto(el, src, displayMode = false) {
  if (!el) return;
  if (typeof katex === "undefined") {
    el.textContent = src;
    return;
  }
  try {
    katex.render(src, el, { throwOnError: false, displayMode, output: "html" });
  } catch (_) {
    el.textContent = src;
  }
}

/* Identity wall (Phase D): verified identities, big and pretty.
 * One card per hypothesis, LHS ≡ RHS rendered with KaTeX. */
function renderIdentityWall(hypotheses) {
  const host = $("identity-wall");
  if (!host) return;
  const identities = (hypotheses || []).filter(
    (h) => h.kind === "identity" && h.status === "verified"
  );
  const others = (hypotheses || []).filter(
    (h) => h.kind !== "identity" && h.status === "verified"
  );
  if (!identities.length && !others.length) {
    host.innerHTML = `<div class="subtle" style="padding:24px;text-align:center">
      <div style="font-size: 38px; opacity: 0.5">🔬</div>
      <p>No verified hypotheses yet.</p>
      <p>Solve pairs of equivalent expressions (e.g. <code>sin(x)**2 + cos(x)**2</code> and <code>1</code>) and click <b>scan now</b>.</p>
    </div>`;
    return;
  }
  const idHtml = identities.map((h) => {
    const ev = h.evidence || {};
    const lhs = ev.lhs_pretty || "?";
    const rhs = ev.rhs_pretty || "?";
    const supportIds = (ev.support_problem_ids || []).slice(0, 8);
    const supportChips = supportIds.map((id) =>
      `<button class="problem-chip" data-action="open-problem" data-id="${id}">#${id}</button>`
    ).join("");
    const latex = `${sympyToLatex(lhs)} \\;\\equiv\\; ${sympyToLatex(rhs)}`;
    return `<div class="identity-card" data-id="${h.id}">
      <div class="identity-card-head">
        <span class="tag">#${h.id}</span>
        <span class="tag good">${escapeHtml(h.method || "verified")}</span>
        <button class="subtle-btn" data-action="try-identity"
                data-text="${escapeHtml(lhs)}">try this in Solve</button>
        <button class="subtle-btn" data-action="reverify" data-id="${h.id}">re-verify</button>
      </div>
      <div class="identity-math" data-latex="${escapeHtml(latex)}"></div>
      <div class="identity-meta">
        <span class="subtle">supports:</span>
        ${supportChips || '<span class="subtle">(none)</span>'}
        ${h.verification_detail ? `<span class="subtle" title="${escapeHtml(h.verification_detail)}">· ${escapeHtml(h.verification_detail.slice(0, 70))}</span>` : ""}
      </div>
    </div>`;
  }).join("");
  const otherHtml = others.length
    ? `<div class="identity-other">
        <h3>Other verified rules</h3>
        ${others.map((h) => `<div class="identity-other-row">
          <span class="tag kind-${escapeHtml(h.kind)}">${escapeHtml(h.kind)}</span>
          <span>${escapeHtml(h.claim || "")}</span>
        </div>`).join("")}
      </div>`
    : "";
  host.innerHTML = idHtml + otherHtml;
  // Render KaTeX
  host.querySelectorAll(".identity-math").forEach((el) => {
    const latex = el.dataset.latex || "";
    renderKaTeXInto(el, latex, true);
  });
  // Wire actions
  host.querySelectorAll('[data-action="try-identity"]').forEach((b) => {
    b.addEventListener("click", () => {
      $("input").value = b.dataset.text || "";
      setIntent("simplify");
      activateTab("solve");
      $("input").focus();
    });
  });
  host.querySelectorAll('[data-action="reverify"]').forEach((b) => {
    b.addEventListener("click", () => reverifyHypothesis(b.dataset.id));
  });
  host.querySelectorAll('[data-action="open-problem"]').forEach((b) => {
    b.addEventListener("click", () => {
      activateTab("solve");
      loadSimilarFor(b.dataset.id);
    });
  });
}

let activeHypView = "wall";
function setHypView(view) {
  activeHypView = view;
  document.querySelectorAll(".hyp-subtab").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
  });
  $("identity-wall").hidden = view !== "wall";
  $("hyp-list").hidden = view !== "raw";
  refreshHypotheses();
}

async function refreshHypotheses() {
  const status = $("hyp-filter-status").value;
  const kind = $("hyp-filter-kind").value;
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (kind) params.set("kind", kind);
  params.set("limit", "200");
  const host = $("hyp-list");
  if (activeHypView === "raw") {
    host.innerHTML = '<div class="subtle" style="padding:12px">loading…</div>';
  } else {
    $("identity-wall").innerHTML = '<div class="subtle" style="padding:12px">loading…</div>';
  }
  try {
    const data = await fetch(`/hypotheses?${params}`).then((r) => r.json());
    const items = data.items || [];
    // Always feed the identity wall (filtered to verified identities).
    // For raw list, respect the selected status/kind filters.
    if (activeHypView === "wall") {
      // For the wall we want all kinds at all statuses, then filter.
      const allData = await fetch("/hypotheses?limit=200").then((r) => r.json());
      renderIdentityWall(allData.items || []);
    } else {
      if (!items.length) {
        host.innerHTML = `<div class="subtle" style="padding:12px">
          No hypotheses yet. Solve a few problems and click "scan now".
        </div>`;
        return;
      }
      host.innerHTML = items.map(renderHypothesisCard).join("");
    }
  } catch (err) {
    const target = activeHypView === "raw" ? host : $("identity-wall");
    target.innerHTML = `<div class="subtle" style="padding:12px">${escapeHtml(err.message)}</div>`;
  }
}

function renderHypothesisCard(h) {
  const status = h.status || "proposed";
  const kind = h.kind || "?";
  const ev = h.evidence || {};
  const evidenceLine = renderEvidenceSummary(h);
  const detailJson = JSON.stringify(h, null, 2);
  return `<div class="hyp-card">
    <div class="row1">
      <span class="id">#${h.id}</span>
      <span class="tag kind-${escapeHtml(kind)}">${escapeHtml(kind)}</span>
      <span class="tag status-${escapeHtml(status)}">${escapeHtml(status)}</span>
      ${h.method ? tag(h.method) : ""}
      <span class="claim">${escapeHtml(h.claim || "")}</span>
    </div>
    <div class="row2">
      ${evidenceLine}
      <span class="subtle">updated ${escapeHtml(h.updated_at || "")}</span>
      ${h.rule_node ? `<span class="tag">rule node ${escapeHtml(h.rule_node)}</span>` : ""}
      <button class="subtle-btn" data-action="reverify" data-id="${h.id}">re-verify</button>
    </div>
    ${h.verification_detail ? `<div class="subtle" style="margin-top:6px">${escapeHtml(h.verification_detail)}</div>` : ""}
    <details><summary>raw evidence</summary><pre>${escapeHtml(detailJson)}</pre></details>
  </div>`;
}

function renderEvidenceSummary(h) {
  const ev = h.evidence || {};
  if (h.kind === "identity") {
    const ids = (ev.support_problem_ids || []).slice(0, 6).join(", ");
    return `<span>${ids ? "from #" + ids : "(no support ids)"}</span>`;
  }
  if (h.kind === "specialization") {
    const leader = ev.leader || {};
    return `<span>${escapeHtml(ev.problem_type || "?")} · leader ${escapeHtml(leader.tool || "?")} (${leader.verified ?? "?"}/${leader.attempts ?? "?"})</span>`;
  }
  if (h.kind === "recurring_approach") {
    const leader = ev.leader || {};
    return `<span>sig ${escapeHtml((ev.signature || "").slice(0, 8))} · ${escapeHtml(leader.approach || "?")} (${leader.verified ?? "?"}/${leader.attempts ?? "?"})</span>`;
  }
  return "";
}

async function triggerHypothesisScan() {
  const btn = $("hyp-scan");
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = "scanning…";
  try {
    const r = await fetch("/hypotheses/scan", { method: "POST" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    await r.json();
    await refreshHypotheses();
    refreshStats();
  } catch (err) {
    toast("Scan failed: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function reverifyHypothesis(id) {
  try {
    const r = await fetch(`/hypotheses/${id}/verify`, { method: "POST" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    await refreshHypotheses();
    refreshStats();
  } catch (err) {
    toast("Re-verify failed: " + err.message, "error");
  }
}

/* ── Bootstrap ────────────────────────────────────────────────────── */

document.addEventListener("DOMContentLoaded", () => {
  $("solve-btn").addEventListener("click", submitSolve);
  $("input").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submitSolve();
  });

  document.querySelectorAll(".tab").forEach((b) => {
    b.addEventListener("click", () => activateTab(b.dataset.tab));
  });
  document.querySelectorAll(".db-tab").forEach((b) => {
    b.addEventListener("click", () => loadDbTable(b.dataset.db));
  });

  ["graph-show-similar", "graph-show-types", "graph-show-tools", "graph-show-sigs"]
    .forEach((id) => $(id).addEventListener("change", applyGraphFilters));
  $("graph-fit").addEventListener("click", () => cy && cy.fit(undefined, 30));
  $("graph-refresh").addEventListener("click", refreshGraph);
  $("db-filter").addEventListener("input", renderDbTable);

  // Trace expand / collapse controls (Phase 3+)
  const traceList = $("trace");
  $("trace-expand")?.addEventListener("click", () => {
    if (traceList) traceList.querySelectorAll("details").forEach((d) => (d.open = true));
  });
  $("trace-collapse")?.addEventListener("click", () => {
    if (traceList) traceList.querySelectorAll("details").forEach((d) => (d.open = false));
  });
  $("trace-copy-md")?.addEventListener("click", copyTraceAsMarkdown);

  // Phase 8: sessions + explain
  $("session-select")?.addEventListener("change", onSessionSelect);
  $("session-new")?.addEventListener("click", createSession);
  $("session-rename")?.addEventListener("click", renameSession);
  $("session-delete")?.addEventListener("click", deleteSession);
  $("session-save-notes")?.addEventListener("click", saveSessionNotes);
  $("explain-btn")?.addEventListener("click", explainCurrentAnswer);

  // Phase 11: notebook tab
  $("notebook-notes-edit")?.addEventListener("click", startNotesEdit);
  $("notebook-notes-cancel")?.addEventListener("click", cancelNotesEdit);
  $("notebook-notes-save")?.addEventListener("click", saveNotesEdit);
  $("notebook-attach-current")?.addEventListener("click", attachLastSolveToSession);
  $("notebook-export")?.addEventListener("click", exportSessionBundle);
  $("notebook-new-problem")?.addEventListener("click", jumpToSolveForNewProblem);

  // Phase 6: settings modal + DB export/import
  $("open-settings")?.addEventListener("click", openSettings);
  $("close-settings")?.addEventListener("click", closeSettings);
  $("save-settings")?.addEventListener("click", saveSettings);
  $("reset-settings")?.addEventListener("click", resetSettings);
  $("settings-modal")?.addEventListener("click", (e) => {
    if (e.target.id === "settings-modal") closeSettings();
  });
  $("db-export")?.addEventListener("click", exportDb);
  $("db-import")?.addEventListener("click", () => $("db-import-file").click());
  $("db-import-file")?.addEventListener("change", (e) => {
    importDbFile(e.target.files && e.target.files[0]);
    e.target.value = "";
  });

  // Phase 5: hypotheses tab controls
  $("hyp-scan")?.addEventListener("click", triggerHypothesisScan);
  $("hyp-refresh")?.addEventListener("click", refreshHypotheses);
  $("hyp-filter-status")?.addEventListener("change", refreshHypotheses);
  $("hyp-filter-kind")?.addEventListener("change", refreshHypotheses);
  $("hyp-list")?.addEventListener("click", (e) => {
    const btn = e.target.closest('[data-action="reverify"]');
    if (btn) reverifyHypothesis(btn.dataset.id);
  });
  // Phase D: hypotheses subtabs (Identity wall / All hypotheses)
  document.querySelectorAll(".hyp-subtab").forEach((b) => {
    b.addEventListener("click", () => setHypView(b.dataset.view));
  });

  // Phase A: examples drawer + intent buttons + cheatsheet
  wireIntentAndExamples();
  renderExamples();

  // Phase B: live ranker preview as the user types
  $("input")?.addEventListener("input", scheduleRankerPreview);
  document.querySelectorAll(".intent-btn").forEach((b) =>
    b.addEventListener("click", () => scheduleRankerPreview())
  );
  $("ranker-preview-close")?.addEventListener("click", () => {
    $("ranker-preview-card").hidden = true;
    lastRankerInput = "";
  });
  $("replay-btn")?.addEventListener("click", replayCurrentSolve);
  $("answer-toggle-render")?.addEventListener("click", () => {
    const rendered = $("answer-rendered");
    const raw = $("answer");
    const btn = $("answer-toggle-render");
    if (rendered.hidden) {
      rendered.hidden = false; raw.hidden = true;
      btn.textContent = "show raw";
    } else {
      rendered.hidden = true; raw.hidden = false;
      btn.textContent = "show rendered";
    }
  });

  // Phase B: demo rail
  $("open-demo")?.addEventListener("click", openDemoRail);
  $("close-demo")?.addEventListener("click", closeDemoRail);
  $("demo-reset")?.addEventListener("click", demoResetState);

  // Phase E: keyboard shortcuts (global) + shortcut help modal
  $("close-shortcuts")?.addEventListener("click", () => { $("shortcuts-modal").hidden = true; });
  $("shortcuts-modal")?.addEventListener("click", (e) => {
    if (e.target.id === "shortcuts-modal") $("shortcuts-modal").hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    const inField = e.target.matches("input, textarea, select");
    if (e.key === "Escape") {
      if (!$("shortcuts-modal").hidden) $("shortcuts-modal").hidden = true;
      else if (!$("settings-modal").hidden) closeSettings();
      else if (!$("demo-rail").hidden) closeDemoRail();
      return;
    }
    if (inField) return;
    if (e.altKey && /^[1-6]$/.test(e.key)) {
      activateTab(TABS[Number(e.key) - 1]);
      e.preventDefault();
    } else if (e.key === "?" || (e.shiftKey && e.key === "/")) {
      $("shortcuts-modal").hidden = false;
      e.preventDefault();
    } else if (e.key === "/" && !e.shiftKey) {
      $("input")?.focus();
      e.preventDefault();
    } else if (e.key === "d" || e.key === "D") {
      const open = !$("demo-rail").hidden;
      open ? closeDemoRail() : openDemoRail();
      e.preventDefault();
    }
  });

  refreshStats();
  refreshRecent();
  refreshToolsBar();
  refreshSessions();
});
