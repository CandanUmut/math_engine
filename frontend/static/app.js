"use strict";

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const fmtNum = (n, d = 2) =>
  Number.isFinite(n) ? Number(n).toFixed(d) : "–";

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
  if (out.problem_id != null) meta.push(`<span class="tag">#${out.problem_id}</span>`);
  $("answer-meta").innerHTML = meta.filter(Boolean).join(" ");

  const items = (out.similar || []);
  $("similar-hint").textContent = items.length
    ? `Surfaced ${items.length} structurally similar past problem(s) before solving — Phase 2 only displays them; Phase 3 will route on them.`
    : "No similar past problems yet — first of its kind in the graph.";
  renderSimilar(items);

  $("trace").innerHTML = (out.trace || []).map((s) => {
    const detail = s.detail ? JSON.stringify(s.detail, null, 2) : "";
    return `<li>
      <span class="kind">${escapeHtml(s.kind)}</span>
      <span class="summary">${escapeHtml(s.summary)}</span>
      ${detail ? `<pre class="detail">${escapeHtml(detail)}</pre>` : ""}
    </li>`;
  }).join("");

  $("fp").textContent = JSON.stringify(out.fingerprint, null, 2);
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
    const [stats, outcomes] = await Promise.all([
      fetch("/db/stats").then((r) => r.json()),
      fetch("/tool_outcomes?limit=50").then((r) => r.json()),
    ]);
    const types = stats.by_problem_type || {};
    const tmax = Math.max(1, ...Object.values(types));
    $("insights-types").innerHTML = Object.entries(types)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => bar(k, v, tmax)).join("") || '<span class="subtle">No data yet.</span>';

    const items = outcomes.items || [];
    if (!items.length) {
      $("insights-tools").innerHTML = '<span class="subtle">No data yet.</span>';
    } else {
      $("insights-tools").innerHTML = items.slice(0, 12).map((o) => {
        const label = `${o.approach} · sig ${o.signature.slice(0, 8)}`;
        const pct = Math.round((o.verify_rate || 0) * 100);
        return `<div class="bar-row">
          <span class="label" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
          <span class="bar"><span style="width:${pct}%"></span></span>
          <span class="v">${pct}% · ${o.n_attempts}×</span>
        </div>`;
      }).join("");
    }

    // We don't have a "by source format" endpoint yet; derive from /problems.
    const recent = await fetch("/problems?limit=200").then((r) => r.json());
    const bySource = {};
    (recent.items || []).forEach((p) => { bySource[p.source_format] = (bySource[p.source_format] || 0) + 1; });
    const smax = Math.max(1, ...Object.values(bySource));
    $("insights-sources").innerHTML = Object.entries(bySource)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => bar(k, v, smax)).join("") || '<span class="subtle">No data yet.</span>';
  } catch (err) {
    $("insights-tools").innerHTML = `<span class="subtle">${escapeHtml(err.message)}</span>`;
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

  refreshStats();
  refreshRecent();
});
