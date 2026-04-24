const $ = (id) => document.getElementById(id);

async function refreshStats() {
  try {
    const r = await fetch("/db/stats");
    const s = await r.json();
    $("stat-problems").textContent = s.problems;
    $("stat-attempts").textContent = s.attempts;
    $("stat-verified").textContent = s.verified_attempts;
  } catch (_) {
    /* ignore */
  }
}

async function refreshRecent() {
  try {
    const r = await fetch("/problems?limit=12");
    const { items } = await r.json();
    const host = $("recent");
    if (!items.length) {
      host.innerHTML = '<div class="subtle">No problems yet. Solve one above.</div>';
      return;
    }
    host.innerHTML = items
      .map((p) => {
        const q = (p.raw_input || "").replaceAll("<", "&lt;");
        return `<div class="recent-item" data-id="${p.id}">
          <span class="id">#${p.id}</span>
          <span class="q" title="${q}">${q}</span>
          <span class="tag type">${p.problem_type}</span>
          <span class="tag">${p.source_format}</span>
          <span class="t">${p.created_at}</span>
        </div>`;
      })
      .join("");
  } catch (_) {
    /* ignore */
  }
}

function renderVerificationTag(status) {
  if (!status) return "";
  const cls = status === "verified" ? "good" : status === "refuted" ? "bad" : "warn";
  return `<span class="tag ${cls}">verify: ${status}</span>`;
}

function escapeHtml(s) {
  return String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function renderOutcome(out) {
  $("answer-card").hidden = false;
  $("trace-card").hidden = false;
  $("fp-card").hidden = false;

  const ans = out.answer_pretty ?? out.error ?? "(no answer)";
  $("answer").textContent = ans;

  const meta = [];
  if (out.problem_type) meta.push(`<span class="tag">${out.problem_type}</span>`);
  if (out.source_format) meta.push(`<span class="tag">${out.source_format}</span>`);
  if (out.approach) meta.push(`<span class="tag">${out.approach}</span>`);
  if (Number.isFinite(out.time_ms)) meta.push(`<span class="tag">${out.time_ms.toFixed(1)} ms</span>`);
  meta.push(renderVerificationTag(out.verification_status));
  if (out.problem_id != null) meta.push(`<span class="tag">#${out.problem_id}</span>`);
  $("answer-meta").innerHTML = meta.filter(Boolean).join(" ");

  $("trace").innerHTML = (out.trace || [])
    .map((s) => {
      const detail = s.detail ? JSON.stringify(s.detail, null, 2) : "";
      return `<li>
        <span class="kind">${s.kind}</span>
        <span class="summary">${escapeHtml(s.summary)}</span>
        ${detail ? `<pre class="detail">${escapeHtml(detail)}</pre>` : ""}
      </li>`;
    })
    .join("");

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

document.addEventListener("DOMContentLoaded", () => {
  $("solve-btn").addEventListener("click", submitSolve);
  $("input").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submitSolve();
  });
  refreshStats();
  refreshRecent();
});
