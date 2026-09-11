// app.js — the agent-lab-ui client. Plain browser JS, no bundle step (locked
// decision 2026-09-02). The hand-rolled canvas gauge here is the ONE piece a future
// Next.js version would swap for a real chart lib; everything server-side stays.
//
// It opens an EventSource against /stream, routes each RunEvent to a panel, and
// redraws the gauge. The RunEvent shapes mirror events.ts (kept in sync by hand —
// the browser can't import the .ts directly without a build step, and v1 skips it).

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const rawEl = $("raw");
const verdictsEl = $("verdicts");
const summaryPanelEl = $("summary-panel");
const summaryStatsEl = $("summary-stats");
const summaryLessonEl = $("summary-lesson");
const cumulativeEl = $("cumulative");
const gaugeSub = $("gauge-sub");
const bannerEl = $("banner");
const railsEl = $("rails");
const gateEl = $("gate");
const gateButtonsEl = $("gate-buttons");
const canvas = $("gauge");
const ctx = canvas.getContext("2d");

// Pull the gauge colors from the CSS variables so the canvas stays in sync with the
// theme (one source of truth in style.css — no dark/light hexes duplicated here).
const css = getComputedStyle(document.documentElement);
const COLOR = {
  axis: css.getPropertyValue("--border").trim() || "#d0d7de",
  grid: css.getPropertyValue("--grid").trim() || "#eaeef2",
  label: css.getPropertyValue("--dim").trim() || "#656d76",
  sent: css.getPropertyValue("--sent").trim() || "#0969da",
  full: css.getPropertyValue("--full").trim() || "#bf3989",
};

// ---- run state (reset on each ▶ run) ----
let es = null;
let turns = []; // { turn, sent, full? }
let cumulative = 0;
let livePid = null; // pid of the current live child, for POST /gate

function reset() {
  turns = [];
  cumulative = 0;
  livePid = null;
  verdictsEl.innerHTML = "";
  summaryPanelEl.hidden = true;
  summaryStatsEl.innerHTML = "";
  summaryLessonEl.textContent = "";
  rawEl.textContent = "";
  cumulativeEl.textContent = "";
  gaugeSub.textContent = "";
  bannerEl.hidden = true;
  bannerEl.textContent = "";
  railsEl.innerHTML = "";
  clearGate();
  draw();
}

// ---- rails: one chip per named rail, latest status wins ----
const RAIL_ICON = { pass: "✅", fail: "❌", pending: "…", pause: "⏸" };
function setRail(name, status, detail) {
  let row = document.getElementById("rail-" + name);
  if (!row) {
    row = document.createElement("div");
    row.id = "rail-" + name;
    row.className = "rail";
    railsEl.appendChild(row);
  }
  row.className = "rail " + status;
  row.innerHTML = `<span class="rail-icon">${RAIL_ICON[status] ?? "?"}</span>` +
    `<span class="rail-name">${name}</span><span class="rail-detail"></span>`;
  row.querySelector(".rail-detail").textContent = detail || "";
}

// ---- gate ----
function clearGate() {
  gateEl.className = "gate idle";
  gateEl.textContent = "no pending approval";
  gateButtonsEl.hidden = true;
}
function showGatePause(animal, proposed) {
  gateEl.className = "gate paused";
  gateEl.innerHTML = `<b>⏸ ${animal}</b><br>proposes: ${proposed}`;
  gateButtonsEl.hidden = false;
  setRail("gate", "pause", `${animal}: ${proposed}`);
}
function showGateResume(animal, decision, reason) {
  gateEl.className = "gate " + (decision === "approve" ? "approved" : "rejected");
  gateEl.innerHTML = `<b>${decision === "approve" ? "✅ approved" : "⛔ rejected"} — ${animal}</b>` +
    (reason ? `<br>${reason}` : "");
  gateButtonsEl.hidden = true;
  setRail("gate", decision === "approve" ? "pass" : "fail", `${animal}: ${decision}`);
}
async function sendGate(decision) {
  if (livePid == null) return;
  gateButtonsEl.hidden = true;
  await fetch("/gate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pid: livePid, decision }),
  }).catch(() => {});
}

function showBanner(text) {
  bannerEl.textContent = text;
  bannerEl.hidden = false;
}

// ---- the gauge: two lines (sent bounded, full unbounded) over turn index ----
function draw() {
  const W = canvas.width;
  const H = canvas.height;
  const pad = { l: 56, r: 16, t: 16, b: 28 };
  ctx.clearRect(0, 0, W, H);

  // axes
  ctx.strokeStyle = COLOR.axis;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t);
  ctx.lineTo(pad.l, H - pad.b);
  ctx.lineTo(W - pad.r, H - pad.b);
  ctx.stroke();

  if (turns.length === 0) {
    ctx.fillStyle = COLOR.label;
    ctx.font = "12px monospace";
    ctx.fillText("waiting for turns…", pad.l + 12, pad.t + 24);
    return;
  }

  const maxTok = Math.max(1, ...turns.map((t) => Math.max(t.sent || 0, t.full || 0)));
  const n = turns.length;
  const xAt = (i) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
  const yAt = (v) => H - pad.b - (v / maxTok) * (H - pad.t - pad.b);

  // y-axis labels (0, mid, max)
  ctx.fillStyle = COLOR.label;
  ctx.font = "10px monospace";
  ctx.textAlign = "right";
  for (const frac of [0, 0.5, 1]) {
    const v = Math.round(maxTok * frac);
    const y = yAt(v);
    ctx.fillText(v.toLocaleString(), pad.l - 6, y + 3);
    ctx.strokeStyle = COLOR.grid;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(W - pad.r, y);
    ctx.stroke();
  }
  ctx.textAlign = "left";

  const line = (key, color) => {
    const pts = turns.map((t, i) => [xAt(i), t[key]]).filter((p) => p[1] != null);
    if (pts.length === 0) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach(([x, v], i) => (i ? ctx.lineTo(x, yAt(v)) : ctx.moveTo(x, yAt(v))));
    ctx.stroke();
  };

  line("full", COLOR.full); // unbounded (II.1 / truncate's shadow line)
  line("sent", COLOR.sent); // bounded / actual
}

// ---- verdict feed ----
function addVerdict(animal, verdict) {
  const row = document.createElement("div");
  row.className = "row";
  const head = (verdict || "").trim().slice(0, 6).toUpperCase();
  let cls = "";
  if (head.startsWith("OK")) cls = "ok";
  else if (head.startsWith("FLAG")) cls = "flag";
  else if (head.startsWith("ORPHAN")) cls = "orphan";
  const tag = cls || (verdict || "?").split(/[\s-]/)[0];
  const why = (verdict || "").replace(/^(OK|FLAG|ORPHAN)\s*-?\s*/i, "");
  row.innerHTML =
    `<span class="id">${animal}</span>` +
    `<span class="tag ${cls}">${cls ? cls.toUpperCase() : tag}</span>` +
    `<span class="why"></span>`;
  row.querySelector(".why").textContent = why;
  verdictsEl.appendChild(row);
  verdictsEl.scrollTop = verdictsEl.scrollHeight;
}

// ---- summary card: the end-of-run footer, promoted out of the raw console ----
function showSummary(evt) {
  // Prefer the explicit stats lines the rung emits; fall back to the legacy
  // scalar fields so pre-`stats` captures still render a card.
  let stats = Array.isArray(evt.stats) ? evt.stats.slice() : [];
  if (stats.length === 0) {
    if (evt.swept != null) stats.push({ label: "animals swept", value: String(evt.swept) });
    if (evt.roundTrips != null) stats.push({ label: "round-trips", value: String(evt.roundTrips) });
    if (evt.cumulative != null)
      stats.push({ label: "cumulative sent", value: `≈${evt.cumulative.toLocaleString()} tok` });
  }
  if (stats.length === 0 && !evt.lesson) return; // nothing to show
  summaryStatsEl.innerHTML = "";
  for (const s of stats) {
    const dt = document.createElement("dt");
    dt.textContent = s.label;
    const dd = document.createElement("dd");
    dd.textContent = s.value;
    summaryStatsEl.append(dt, dd);
  }
  summaryLessonEl.textContent = evt.lesson || "";
  summaryLessonEl.hidden = !evt.lesson;
  summaryPanelEl.hidden = false;
}

function logRaw(line) {
  rawEl.textContent += line + "\n";
  rawEl.scrollTop = rawEl.scrollHeight;
}

// ---- route one RunEvent ----
function handle(evt) {
  switch (evt.type) {
    case "run_start":
      gaugeSub.textContent = `· ${evt.rung} · ${evt.model} · N=${evt.n}`;
      break;
    case "turn":
      turns.push({ turn: evt.turn, sent: evt.sent, full: evt.full });
      if (evt.cumulative != null) {
        cumulative = evt.cumulative;
        cumulativeEl.textContent = `cumulative: ${cumulative.toLocaleString()} tok`;
      }
      draw();
      break;
    case "verdict":
      addVerdict(evt.animal, evt.verdict);
      break;
    case "run_end":
      setStatus("done", `done · ${evt.roundTrips ?? turns.length} round-trips`);
      showSummary(evt);
      // A run_end means the run is logically finished — re-enable the controls now,
      // without waiting for the socket to close. Some rungs (II.6's MCP client) can
      // keep the child alive briefly after their final output, which would otherwise
      // leave ▶ stuck disabled until the connection times out.
      stopStream();
      break;
    case "rail":
      setRail(evt.name, evt.status, evt.detail);
      break;
    case "gate_pause":
      showGatePause(evt.animal, evt.proposed);
      break;
    case "gate_resume":
      showGateResume(evt.animal, evt.decision, evt.reason);
      break;
    case "log":
      // The server announces a replay's event count as "[replay <file>: N lines, M @@ events]".
      // If M is 0 the capture predates emit() — explain the empty gauge instead of leaving it
      // looking broken, and pop the raw pane open so there's something to see.
      {
        const m = evt.line.match(/^\[replay .*?: \d+ lines, (\d+) @@ events\]$/);
        if (m && Number(m[1]) === 0) {
          showBanner(
            "This capture has no machine events (it predates emit()) — the gauge stays empty. " +
              "Re-capture it (see README) to populate the chart. Raw output is below."
          );
          document.querySelector("details.raw").open = true;
        }
        // the live handler announces the child's pid so the gate button can target it
        const p = evt.line.match(/^\[live pid (\d+)\]$/);
        if (p) livePid = Number(p[1]);
      }
      logRaw(evt.line);
      break;
    default:
      logRaw("· " + JSON.stringify(evt));
  }
}

function setStatus(cls, text) {
  statusEl.className = "status " + cls;
  statusEl.textContent = text;
}

// ---- controls ----
function openStream() {
  if (es) es.close();
  reset();
  const mode = $("mode").value;
  let url;
  if (mode === "live") {
    url =
      `/stream?mode=live&rung=${encodeURIComponent($("rung").value)}` +
      `&n=${encodeURIComponent($("n").value)}` +
      `&provider=${encodeURIComponent($("provider").value)}`;
  } else {
    url = `/stream?mode=replay&file=${encodeURIComponent($("file").value)}`;
  }
  es = new EventSource(url);
  setStatus("running", "running…");
  $("run").disabled = true;
  $("stop").disabled = false;
  es.onmessage = (e) => {
    try {
      handle(JSON.parse(e.data));
    } catch {
      /* ignore malformed frame */
    }
  };
  es.onerror = () => {
    // SSE closes with an error when the server ends the stream — treat as end-of-run
    if (statusEl.textContent === "running…") setStatus("done", "stream ended");
    stopStream();
  };
}

function stopStream() {
  if (es) es.close();
  es = null;
  $("run").disabled = false;
  $("stop").disabled = true;
}

function syncModeControls() {
  const replay = $("mode").value === "replay";
  $("n-wrap").hidden = replay;
  $("provider-wrap").hidden = replay; // model choice only applies to a live run
  $("file-wrap").hidden = !replay;
}

$("run").addEventListener("click", openStream);
$("stop").addEventListener("click", () => {
  stopStream();
  setStatus("done", "stopped");
});
$("mode").addEventListener("change", syncModeControls);
$("approve").addEventListener("click", () => sendGate("approve"));
$("reject").addEventListener("click", () => sendGate("reject"));

// ---- populate controls from /runs ----
(async () => {
  try {
    const { rungs, providers, captures } = await (await fetch("/runs")).json();
    const rungSel = $("rung");
    rungs.forEach((r) => {
      const o = document.createElement("option");
      o.value = r.id;
      o.textContent = `${r.id}  (${r.script})${r.gate ? "  ⏸" : ""}`;
      rungSel.appendChild(o);
    });
    const provSel = $("provider");
    (providers ?? []).forEach((p) => {
      const o = document.createElement("option");
      o.value = p;
      o.textContent = p;
      if (p === "qwen3.5") o.selected = true; // the default actor
      provSel.appendChild(o);
    });
    const fileSel = $("file");
    captures.forEach((f) => {
      const o = document.createElement("option");
      o.value = f;
      o.textContent = f;
      fileSel.appendChild(o);
    });
  } catch {
    setStatus("error", "could not load /runs");
  }
  syncModeControls();
  draw();
})();
