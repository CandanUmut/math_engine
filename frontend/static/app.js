"use strict";

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const fmtNum = (n, d = 2) =>
  Number.isFinite(n) ? Number(n).toFixed(d) : "–";

const VERIFY_CLS = (s) =>
  s === "verified" ? "good" : s === "refuted" ? "bad" : s ? "warn" : "";
const CV_CLS = (s) => (s ? `cv-${s}` : "");

function tag(text, cls = "") {
  if (text === null || text === undefined || text === "") return "";
  const c = cls ? ` ${cls}` : "";
  return `<span class="tag${c}">${escapeHtml(text)}</span>`;
}

function pct(n) {
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : "–";
}

/* ── Tabs ─────────────────────────────────────────────────────────── */

const TABS = ["solve", "graph", "database", "insights"];
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
  const cls = status === "verified" ? "good" : status === "refuted" ? "bad" : "warn";
  return `<span class="tag ${cls}">verify: ${status}</span>`;
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
  $("answer-card").hidden = false;
  $("trace-card").hidden = false;
  $("fp-card").hidden = false;
  $("similar-card").hidden = false;

  $("answer").textContent = out.answer_pretty ?? out.error ?? "(no answer)";

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

function renderTrace(steps) {
  $("trace").innerHTML = steps.map((s) => {
    const kindClass = `k-${(s.kind || "").replace(/[^a-z_]/gi, "")}`;
    const detail = renderTraceDetail(s);
    return `<li>
      <span class="kind ${kindClass}">${escapeHtml(s.kind)}</span>
      <span class="summary">${escapeHtml(s.summary || "")}</span>
      ${detail}
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
  const text = $("input").value.trim();
  if (!text) return;
  const btn = $("solve-btn");
  btn.disabled = true;
  $("hint").textContent = "Solving…";
  try {
    const r = await fetch("/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const out = await r.json();
    renderOutcome(out);
    $("hint").textContent = out.ok ? "" : "Tool failed — see trace.";
  } catch (err) {
    $("hint").textContent = "Network error: " + err.message;
  } finally {
    btn.disabled = false;
    refreshStats();
    refreshRecent();
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

  refreshStats();
  refreshRecent();
  refreshToolsBar();
});
