// Jarvis Control Center — client logic.
//
// Data sources:
//   1. GET /api/dashboard/snapshot — one-shot full-state read on load.
//   2. WebSocket /ws (token via POST /api/ws-token) — the SAME broadcast
//      bus every other Jarvis client (the PWA, the Android companion)
//      already listens on. This dashboard never sends voice_session_*/
//      user_message/device_status — it is a read-only listener that
//      sends exactly two dashboard-only wire messages, neither ever sent
//      by the phone or the PWA: "register_observer" once on connect
//      (opts this connection into the small set of new event types this
//      feature adds — see ConnectionManager.broadcast_observers — so an
//      ordinary protocol connection never sees them and can't have its
//      own request/response frame order disturbed), and periodic "ping"
//      frames used purely to measure this connection's own round-trip
//      latency honestly.
//   3. A 5s poll of /api/dashboard/snapshot for the handful of numeric
//      fields (uptime, connection/reconnect counts, OpenCode liveness)
//      that have no natural "it changed" event to broadcast — chosen
//      over adding a new server-wide periodic broadcast so a demo phone
//      is never woken every 5s for a dashboard it isn't using.
//
// No chain-of-thought / raw LLM reasoning is ever rendered here — every
// panel is fed by tool names, arguments, truncated results, and state
// transitions the backend already logs as *executed*, never by anything
// resembling the model's own explanation of why.

const $ = (id) => document.getElementById(id);

// Control Center hardening, Phase 2: JARVIS_API_TOKEN gates every real
// endpoint in main.py (see _require_api_token) the same way regardless
// of caller — the dashboard is no exception. There is no dashboard
// settings UI (out of scope for this feature), so the token is read
// from localStorage the same way a developer/operator would set one:
// `localStorage.setItem("jarvis_api_token", "...")` once in devtools.
// Absent (the default, JARVIS_API_TOKEN unset server-side) this adds no
// header at all, unchanged from before.
function authHeaders() {
  let token = null;
  try { token = window.localStorage.getItem("jarvis_api_token"); } catch (e) { /* private browsing etc. */ }
  return token ? { "Authorization": `Bearer ${token}` } : {};
}

const state = {
  connectedAt: null,
  serverStartedAt: null,
  wsConnectedSince: null,
  wsReconnects: 0,
  lastPingSentAt: null,
  latencyMs: null,
  opencodeAlive: null,
  connectivity: {},
  tasksById: new Map(),         // task_id -> task row (merged view)
  taskProgress: new Map(),      // task_id -> latest progress line
  attentionById: new Map(),     // attention_request_id -> row
  currentTurn: null,            // in-flight/most recent conversation turn
  timelineLit: {},              // stage -> {state, at}
  lastHeartbeatAt: null,
  deviceStatus: null,
};

const MAX_FEED_ROWS = 250;
const MAX_DECISION_ROWS = 80;
const MAX_ALERTS = 30;

// ── Health / Timeline definitions ──────────────────────────────────

const HEALTH_ITEMS = [
  "android", "backend", "websocket", "supervisor", "llm", "opencode", "database", "notifications",
];
const HEALTH_LABELS = {
  android: "Android Companion", backend: "Backend", websocket: "WebSocket",
  supervisor: "Supervisor", llm: "LLM", opencode: "OpenCode",
  database: "Database", notifications: "Notifications",
};

const TIMELINE_STAGES = [
  { key: "wake_word", label: "Wake Word", instrumented: false },
  { key: "voice_session", label: "Voice Session", instrumented: true },
  { key: "supervisor", label: "Supervisor", instrumented: true },
  { key: "llm", label: "LLM", instrumented: true },
  { key: "tool", label: "Tool", instrumented: true },
  { key: "opencode", label: "OpenCode", instrumented: true },
  { key: "task_complete", label: "Task Complete", instrumented: true },
  { key: "phone_response", label: "Phone Response", instrumented: true },
  { key: "tts", label: "TTS", instrumented: false },
];

// ── DOM bootstrapping for static structures ────────────────────────

function buildHealthRow() {
  const row = $("health-row");
  row.innerHTML = HEALTH_ITEMS.map((k) => `
    <div class="cc-health-item" data-health="${k}">
      <span class="cc-dot unknown" data-dot="${k}"></span>
      <span class="cc-health-name">${HEALTH_LABELS[k]}</span>
      <span class="cc-health-detail" data-detail="${k}"></span>
    </div>`).join("");
}

function buildTimeline() {
  const el = $("event-timeline");
  el.innerHTML = TIMELINE_STAGES.map((s) => `
    <div class="cc-tl-node ${s.instrumented ? "" : "dim"}" data-stage="${s.key}">
      <div class="cc-tl-line"></div>
      <div class="cc-tl-dot"></div>
      <div class="cc-tl-label">${s.label}${s.instrumented ? "" : "<br><span style='opacity:.6'>(not wired)</span>"}</div>
      <div class="cc-tl-time" data-stage-time="${s.key}"></div>
    </div>`).join("");
}

function setHealth(key, level, detail) {
  const dot = document.querySelector(`[data-dot="${key}"]`);
  const item = document.querySelector(`.cc-health-item[data-health="${key}"]`);
  const detailEl = document.querySelector(`[data-detail="${key}"]`);
  if (!dot) return;
  dot.className = "cc-dot " + level;
  if (item) item.classList.toggle("bad", level === "bad");
  if (detailEl && detail) detailEl.textContent = detail;
}

function litStage(key, kind, timeLabel) {
  const node = document.querySelector(`.cc-tl-node[data-stage="${key}"]`);
  if (!node) return;
  node.classList.remove("lit", "ok", "bad");
  node.classList.add(kind); // "lit" | "ok" | "bad"
  const t = document.querySelector(`[data-stage-time="${key}"]`);
  if (t) t.textContent = timeLabel || nowShort();
  state.timelineLit[key] = { kind, at: Date.now() };
  // auto-settle a "lit" (in-progress) node to "ok" after a few seconds if
  // nothing else touches it, so the pipeline doesn't stay permanently
  // highlighted from the last turn forever.
  if (kind === "lit") {
    setTimeout(() => {
      if (state.timelineLit[key] && Date.now() - state.timelineLit[key].at > 2900) {
        node.classList.remove("lit");
        node.classList.add("ok");
      }
    }, 3000);
  }
}

// ── Time helpers ────────────────────────────────────────────────────

function nowShort() {
  return new Date().toLocaleTimeString([], { hour12: false });
}
function tsShort(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleTimeString([], { hour12: false }); } catch { return ""; }
}
function fmtElapsed(seconds) {
  if (seconds == null) return "—";
  seconds = Math.max(0, Math.round(seconds));
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
function fmtAgo(iso) {
  if (!iso) return "never";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 0) return "just now";
  if (d < 60) return `${Math.round(d)}s ago`;
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  return `${Math.round(d / 3600)}h ago`;
}
function esc(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function truncate(s, n) {
  if (s == null) return "";
  s = String(s);
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// ── Activity Feed / Decision Stream / Alerts (shared row renderer) ─

function pushRow(containerId, html, opts = {}) {
  const el = $(containerId);
  const empty = el.querySelector(".cc-empty");
  if (empty) empty.remove();
  const row = document.createElement("div");
  row.className = "cc-row" + (opts.sev ? ` sev-${opts.sev}` : "");
  row.innerHTML = html;
  // Plain column flow (see the CSS comment on .cc-feed for why this is
  // NOT column-reverse): prepending puts the newest row at DOM index 0,
  // which is the visual top in normal flow — no scroll-position math
  // needed. Processing events oldest-to-newest and prepending each one
  // (both here and in the snapshot backfill loop) naturally leaves the
  // single newest event at the top once all of them have been applied.
  el.prepend(row);
  const max = opts.max || MAX_FEED_ROWS;
  while (el.children.length > max) el.removeChild(el.lastChild);
}

function feedRow(icon, timeIso, html, sev) {
  pushRow("activity-feed", `<span class="icon">${icon}</span><span class="t">${tsShort(timeIso)}</span><span class="body">${html}</span>`, { sev, max: MAX_FEED_ROWS });
}
function decisionRow(icon, timeIso, html) {
  pushRow("decision-stream", `<span class="icon">${icon}</span><span class="t">${tsShort(timeIso)}</span><span class="body">${html}</span>`, { max: MAX_DECISION_ROWS });
}
function pushAlert(text, severity, timeIso) {
  const el = $("alerts-list");
  const empty = el.querySelector(".cc-empty");
  if (empty) empty.remove();
  const chip = document.createElement("div");
  chip.className = "cc-alert-chip " + (severity || "info");
  chip.innerHTML = `<span class="t">${tsShort(timeIso || new Date().toISOString())}</span><span>${esc(text)}</span>`;
  el.prepend(chip);
  while (el.children.length > MAX_ALERTS) el.removeChild(el.lastChild);
}

// ── Event routing: one dict per `type`, either from history/snapshot
//    (type is stored as the events-table `type`, content is a JSON
//    string) or from a live broadcast (same shape, `content` still a
//    JSON string for every event this dashboard cares about). ────────

function safeParse(s) {
  if (s == null) return {};
  if (typeof s === "object") return s;
  try { return JSON.parse(s); } catch { return { raw: s }; }
}

function handleNamedEvent(type, content, timestamp) {
  const c = safeParse(content);
  switch (type) {
    case "task_started":
      feedRow("▶", timestamp, `Task started: <span class="k">${esc(c.name)}</span> <span style="color:var(--muted-2)">${esc((c.task_id||"").slice(0,8))}</span>`);
      touchTask(c.task_id, { name: c.name, status: "running" });
      break;
    case "task_stdout":
      touchTaskProgress(c.task_id, c.line);
      break;
    case "task_completed":
      feedRow("✓", timestamp, `Task completed: <span class="k">${esc(c.name)}</span>`);
      touchTask(c.task_id, { status: "completed" });
      litStage("task_complete", "ok", tsShort(timestamp));
      break;
    case "task_failed":
      feedRow("✗", timestamp, `Task failed: <span class="k">${esc(c.name)}</span>`, "fail");
      touchTask(c.task_id, { status: "failed" });
      litStage("task_complete", "bad", tsShort(timestamp));
      pushAlert(`Task failed: ${c.name || c.task_id}`, "critical", timestamp);
      break;
    case "task_cancelled":
      feedRow("⊘", timestamp, `Task cancelled: <span class="k">${esc(c.name)}</span>`);
      touchTask(c.task_id, { status: "cancelled" });
      break;
    case "question_asked":
      feedRow("❓", timestamp, `Question asked: <span class="k">${esc(truncate(c.question, 80))}</span>`, "warn");
      break;
    case "question_answered":
      feedRow("💬", timestamp, `Question answered: <span class="k">${esc(truncate(c.answer, 60))}</span>`);
      break;
    case "question_cancelled":
      feedRow("⊘", timestamp, `Question cancelled`);
      break;
    case "opencode_task_created":
      feedRow("🛠", timestamp, `OpenCode task created: <span class="k">${esc(truncate(c.instruction, 70))}</span>`);
      touchTask(c.task_id, { name: `OpenCode: ${truncate(c.instruction, 50)}`, status: "running" });
      litStage("opencode", "lit", tsShort(timestamp));
      litStage("tool", "ok", tsShort(timestamp));
      break;
    case "opencode_task_cancelled":
      feedRow("⊘", timestamp, `OpenCode task cancelled: ${esc((c.task_id||"").slice(0,10))}`);
      touchTask(c.task_id, { status: "cancelled" });
      break;
    case "opencode_task_completed":
      if (c.status === "failed") {
        feedRow("✗", timestamp, `OpenCode task failed: ${esc((c.task_id||"").slice(0,10))}`, "fail");
        touchTask(c.task_id, { status: "failed" });
        litStage("task_complete", "bad", tsShort(timestamp));
        pushAlert(`OpenCode task failed: ${c.task_id}`, "critical", timestamp);
      } else {
        feedRow("✓", timestamp, `OpenCode task completed: ${esc((c.task_id||"").slice(0,10))}`);
        touchTask(c.task_id, { status: "completed" });
        litStage("task_complete", "ok", tsShort(timestamp));
      }
      break;
    case "opencode_error":
      feedRow("⚠", timestamp, `OpenCode error on ${esc((c.task_id||"").slice(0,10))}`, "fail");
      pushAlert("OpenCode session error", "critical", timestamp);
      break;
    case "task_question":
      feedRow("❓", timestamp, `OpenCode question: <span class="k">${esc(truncate(c.question, 70))}</span>`, "warn");
      break;
    case "task_permission":
      feedRow("🔐", timestamp, `Permission requested: <span class="k">${esc(c.action)} ${esc(c.path||"")}</span>`, "warn");
      break;
    case "opencode_question_answered":
    case "opencode_question_rejected":
    case "opencode_permission_handled":
      feedRow("💬", timestamp, `Resolved: ${esc(type.replace(/_/g, " "))}`);
      break;
    case "opencode_message":
      touchTaskProgress(c.task_id, c.content);
      litStage("opencode", "lit", tsShort(timestamp));
      break;

    case "attention_created":
      upsertAttention(c);
      pushAlert(`Needs attention: ${truncate(c.summary, 60)}`, "warning", timestamp);
      break;
    case "attention_contacting":
    case "attention_pending":
    case "attention_deferred":
      upsertAttention(c);
      break;
    case "attention_resolving":
    case "attention_resolved":
    case "attention_cancelled":
    case "attention_expired":
      removeAttention(c.attention_request_id);
      break;

    case "device_status_update":
      state.deviceStatus = c;
      feedRow("📱", timestamp, `Phone status: <span class="k">${esc(c.device_id || "unknown")}</span> paired=${esc(c.pairing_state)}`);
      break;

    case "voice_session_lifecycle":
      handleVoiceLifecycle(c, timestamp);
      break;

    case "supervisor_turn_started":
      startTurn(c, timestamp);
      feedRow("🧠", timestamp, `Supervisor processing: <span class="k">${esc(truncate(c.user_message, 60))}</span>`);
      litStage("supervisor", "lit", tsShort(timestamp));
      break;
    case "supervisor_tool_call":
      addToolCall(c, timestamp);
      decisionRow("⚙", timestamp, `Selected tool: <span class="k">${esc(c.tool)}</span>`);
      decisionRow("→", timestamp, `Arguments: <span style="color:var(--muted)">${esc(truncate(JSON.stringify(c.args || {}), 140))}</span>`);
      decisionRow("=", timestamp, `Result: ${esc(truncate(c.result_summary, 140))}`);
      litStage("llm", "ok", tsShort(timestamp));
      litStage("tool", "lit", tsShort(timestamp));
      break;
    case "supervisor_turn":
      completeTurn(c, timestamp);
      decisionRow("✓", timestamp, `Turn completed (${c.tool_call_count || 0} tool call${c.tool_call_count === 1 ? "" : "s"})`);
      if (!c.tool_call_count) litStage("llm", "ok", tsShort(timestamp));
      break;

    default:
      // Unknown/uninteresting event types (task_stderr, raw wire echoes,
      // etc.) are intentionally not shown — the Activity Feed is curated,
      // not a raw log dump.
      break;
  }
}

// ── Voice pipeline ───────────────────────────────────────────────────

function handleVoiceLifecycle(c, timestamp) {
  switch (c.phase) {
    case "opened":
      // Deliberately does NOT reset the rest of the timeline (an earlier
      // version did, and a real bug surfaced while seeding demo data:
      // wiring an unrelated OpenCode task's own "opencode"/"task_complete"
      // stages that were still legitimately current — an OpenCode task
      // runs in the background and can outlive the voice turn that
      // triggered it, so a new voice session opening is not evidence
      // those stages went stale). Each node already carries its own
      // last-touched timestamp, which is the honest staleness signal.
      feedRow("🎙", timestamp, `Voice session opened`);
      litStage("voice_session", "ok", tsShort(timestamp));
      break;
    case "transcript_received":
      feedRow("📝", timestamp, `Transcript received: <span class="k">${esc(truncate(c.transcript, 80))}</span>`);
      litStage("voice_session", "ok", tsShort(timestamp));
      break;
    case "turn_completed":
      feedRow("🔊", timestamp, `Response ready: <span class="k">${esc(truncate(c.response, 80))}</span>`);
      litStage("phone_response", "lit", tsShort(timestamp));
      break;
    case "closed":
      feedRow("⏹", timestamp, `Voice session closed (${esc(c.reason)})`);
      break;
    case "failed":
      feedRow("⚠", timestamp, `Voice session failed`, "fail");
      break;
  }
}

// ── Current conversation turn tracking ──────────────────────────────

function startTurn(c) {
  state.currentTurn = {
    conversation_id: c.conversation_id,
    user_message: c.user_message,
    tools: [],
    response: null,
    status: "running",
  };
  renderConversation();
}
function addToolCall(c) {
  if (!state.currentTurn || state.currentTurn.conversation_id !== c.conversation_id) {
    // A tool call with no matching turn-start (e.g. dashboard connected
    // mid-turn) — start a synthetic turn so the tool call is still shown.
    state.currentTurn = { conversation_id: c.conversation_id, user_message: null, tools: [], response: null, status: "running" };
  }
  state.currentTurn.tools.push({ tool: c.tool, args: c.args, result_summary: c.result_summary });
  renderConversation();
}
function completeTurn(c) {
  if (!state.currentTurn || state.currentTurn.conversation_id !== c.conversation_id) {
    state.currentTurn = { conversation_id: c.conversation_id, user_message: c.user_message, tools: [], response: null, status: "running" };
  }
  state.currentTurn.user_message = state.currentTurn.user_message || c.user_message;
  state.currentTurn.response = c.response;
  state.currentTurn.status = "completed";
  renderConversation();
}

function renderConversation() {
  const el = $("current-conversation");
  if (!state.currentTurn) { el.innerHTML = `<p class="cc-empty">No active turn yet.</p>`; return; }
  const t = state.currentTurn;
  const toolsHtml = t.tools.length
    ? t.tools.map((tc) => `<span class="cc-tool-chip">${esc(tc.tool)}</span>`).join("")
    : `<span style="color:var(--muted-2);font-size:12px">none</span>`;
  const argsHtml = t.tools.length
    ? t.tools.map((tc) => `<div class="cc-conv-value mono">${esc(tc.tool)}(${esc(truncate(JSON.stringify(tc.args || {}), 160))})</div>`).join("")
    : "";
  el.innerHTML = `
    <div class="cc-conv-turn">
      <div class="cc-conv-field">
        <div class="cc-conv-label">User / Transcript</div>
        <div class="cc-conv-value">${esc(t.user_message) || "<span style='color:var(--muted-2)'>—</span>"}</div>
      </div>
      <div class="cc-conv-field">
        <div class="cc-conv-label">Chosen Tool(s)</div>
        <div class="cc-conv-value">${toolsHtml}</div>
      </div>
      ${argsHtml ? `<div class="cc-conv-field"><div class="cc-conv-label">Arguments</div>${argsHtml}</div>` : ""}
      <div class="cc-conv-field">
        <div class="cc-conv-label">Status</div>
        <span class="cc-status-pill ${t.status}">${t.status}</span>
      </div>
      <div class="cc-conv-field">
        <div class="cc-conv-label">Response</div>
        <div class="cc-conv-value">${t.response != null ? esc(t.response) : "<span style='color:var(--muted-2)'>awaiting…</span>"}</div>
      </div>
    </div>`;
}

// ── Tasks (Running / Queued / Completed / Failed / Cancelled) ──────

function bucketFor(status) {
  if (status === "running" || status === "waiting_for_user") return "running";
  if (status === "pending") return "queued";
  if (status === "completed") return "completed";
  if (status === "failed" || status === "degraded") return "failed";
  if (status === "cancelled") return "cancelled";
  return "running";
}

function touchTask(taskId, patch) {
  if (!taskId) return;
  const existing = state.tasksById.get(taskId) || { task_id: taskId };
  Object.assign(existing, patch, { _updated_at: new Date().toISOString() });
  state.tasksById.set(taskId, existing);
  renderTasks();
}
function touchTaskProgress(taskId, line) {
  if (!taskId || !line) return;
  state.taskProgress.set(taskId, line);
  const t = state.tasksById.get(taskId);
  if (t) { t._updated_at = new Date().toISOString(); renderTasks(); }
}

function seedTasks(rows, ocRunning) {
  const ocByTaskId = new Map((ocRunning || []).map((t) => [t.task_id, t]));
  for (const row of rows) {
    const oc = ocByTaskId.get(row.task_id);
    state.tasksById.set(row.task_id, {
      task_id: row.task_id,
      name: row.name,
      status: row.status,
      started_at: row.started_at,
      completed_at: row.completed_at,
      _updated_at: row.completed_at || row.started_at,
      _last_evidence: oc ? oc.last_evidence_type : null,
      _last_evidence_at: oc ? oc.last_evidence_at : null,
    });
  }
  renderTasks();
}

function renderTasks() {
  const buckets = { running: [], queued: [], completed: [], failed: [], cancelled: [] };
  const now = Date.now();
  for (const t of state.tasksById.values()) buckets[bucketFor(t.status)].push(t);
  for (const key of Object.keys(buckets)) {
    buckets[key].sort((a, b) => new Date(b._updated_at || b.started_at || 0) - new Date(a._updated_at || a.started_at || 0));
    const listEl = $(`list-${key}`);
    $(`count-${key}`).textContent = buckets[key].length;
    listEl.innerHTML = buckets[key].slice(0, 25).map((t) => {
      const startedMs = t.started_at ? new Date(t.started_at).getTime() : null;
      const endMs = t.completed_at ? new Date(t.completed_at).getTime() : now;
      const elapsed = startedMs != null ? (endMs - startedMs) / 1000 : null;
      const updatedAgoSec = t._updated_at ? (now - new Date(t._updated_at).getTime()) / 1000 : 9e9;
      const isRunningBucket = key === "running";
      const stale = isRunningBucket && updatedAgoSec > 120;
      const old = !isRunningBucket && updatedAgoSec > 600;
      const progress = state.taskProgress.get(t.task_id) || (t._last_evidence ? `${t._last_evidence} · ${fmtAgo(t._last_evidence_at)}` : null);
      return `
        <div class="cc-task-card ${stale ? "stale" : ""} ${old ? "old" : ""}">
          <div class="id">${esc((t.task_id || "").slice(0, 10))} ${stale ? '<span class="stale-flag">STALE</span>' : ""}</div>
          <div class="name">${esc(truncate(t.name || "Unnamed task", 60))}</div>
          <div class="meta"><span>${elapsed != null ? fmtElapsed(elapsed) : "—"}</span><span>${fmtAgo(t._updated_at)}</span></div>
          ${progress ? `<div class="progress" title="${esc(progress)}">${esc(truncate(progress, 44))}</div>` : ""}
        </div>`;
    }).join("") || `<p class="cc-empty" style="font-size:11px">—</p>`;
  }
}

// ── Attention Center ─────────────────────────────────────────────────

const ATTENTION_LABELS = {
  QUESTION: "Waiting for clarification",
  PERMISSION: "Permission required",
  TASK_FAILURE: "Task failed",
  TASK_COMPLETION: "Notification sent",
};

function upsertAttention(row) {
  if (!row || !row.attention_request_id) return;
  state.attentionById.set(row.attention_request_id, row);
  renderAttention();
}
function removeAttention(id) {
  if (!id) return;
  state.attentionById.delete(id);
  renderAttention();
}
function seedAttention(rows) {
  state.attentionById.clear();
  for (const r of rows) state.attentionById.set(r.attention_request_id, r);
  renderAttention();
}

function renderAttention() {
  const el = $("attention-list");
  const items = Array.from(state.attentionById.values());
  $("attention-count").textContent = items.length;
  if (!items.length) { el.innerHTML = `<p class="cc-empty">Nothing needs attention.</p>`; return; }
  el.innerHTML = items.map((a) => {
    const urgent = a.urgency === "HIGH" || a.status === "pending";
    const label = ATTENTION_LABELS[a.attention_type] || a.attention_type;
    const statusNote = a.status === "deferred" && a.deferred_until
      ? `retry pending · ${fmtAgo(a.deferred_until)}`
      : a.status === "contacting" ? "notification sent" : a.status;
    return `
      <div class="cc-attn-card ${urgent ? "urgent" : ""}">
        <div class="kind">${esc(label)}</div>
        <div class="summary">${esc(truncate(a.summary, 100))}</div>
        <div class="meta">${esc(statusNote)} · task ${esc((a.task_id || "—").slice(0, 8))}</div>
      </div>`;
  }).join("");
}

// ── Connectivity / System Health / Background Services (periodic) ──

function applySnapshotHealthAndConnectivity(snap) {
  state.serverStartedAt = snap.server.started_at;
  state.opencodeAlive = snap.opencode.server_alive;
  state.connectivity = snap.connectivity;
  state.lastHeartbeatAt = snap.connectivity.last_heartbeat_at;
  // The server's connectivity snapshot is authoritative for "is a phone
  // currently connected" (ConnectionManager clears its device_status
  // entry on disconnect) — it must always win here, including back to
  // null. A real bug found while seeding demo data: an `||` fallback
  // let a stale value survive forever once set, because a live
  // device_status_update event (or a replayed historical one from the
  // events backfill) could set state.deviceStatus, and no snapshot poll
  // could ever un-set it again even after the simulated phone actually
  // disconnected — "Phone connected: yes" kept showing with a 10+
  // minute-old device long after it was gone.
  state.deviceStatus = snap.connectivity.device_status;

  $("stat-uptime").textContent = fmtElapsed(snap.server.uptime_seconds);
  $("stat-connections").textContent = snap.connectivity.connections_now;

  const phoneUp = !!state.deviceStatus;
  const heartbeatFresh = state.lastHeartbeatAt && (Date.now() - new Date(state.lastHeartbeatAt).getTime()) < 90000;
  setHealth("android", phoneUp && heartbeatFresh ? "ok" : phoneUp ? "warn" : "unknown",
    phoneUp ? fmtAgo(state.lastHeartbeatAt) : "no device");
  setHealth("backend", "ok", fmtElapsed(snap.server.uptime_seconds));
  setHealth("websocket", ws && ws.readyState === WebSocket.OPEN ? "ok" : "bad",
    state.latencyMs != null ? `${state.latencyMs}ms` : "");
  setHealth("supervisor", "ok");
  setHealth("llm", "ok");
  const wasAlive = state.opencodeAliveKnown;
  setHealth("opencode", snap.opencode.server_alive ? "ok" : "bad", snap.opencode.server_alive ? "" : "unreachable");
  if (state.opencodeAliveKnown != null && state.opencodeAliveKnown !== snap.opencode.server_alive) {
    pushAlert(snap.opencode.server_alive ? "OpenCode back online" : "OpenCode unavailable",
      snap.opencode.server_alive ? "info" : "critical");
  }
  state.opencodeAliveKnown = snap.opencode.server_alive;
  setHealth("database", "ok");
  setHealth("notifications", "ok");

  // Phase 5 (Control Center hardening): this used to stash _prevReconnect
  // on snap.connectivity itself, which is a brand-new object parsed fresh
  // from every poll's response body -- the field set here was always gone
  // by the next poll's new object, so the comparison always read
  // undefined and this alert could never fire. state.prevReconnectCount
  // lives on the one long-lived `state` object instead (same pattern as
  // state.opencodeAliveKnown just above), so it actually survives between
  // polls.
  if (state.prevReconnectCount != null && snap.connectivity.reconnect_count > state.prevReconnectCount) {
    pushAlert("Phone reconnected", "info");
  }
  state.prevReconnectCount = snap.connectivity.reconnect_count;

  const grid = $("connectivity-grid");
  const ds = state.deviceStatus || {};
  grid.innerHTML = [
    ["Phone connected", phoneUp ? "yes" : "no", phoneUp ? "ok" : "bad"],
    ["Last heartbeat", fmtAgo(state.lastHeartbeatAt), heartbeatFresh ? "ok" : "warn"],
    ["Dashboard WS latency", state.latencyMs != null ? `${state.latencyMs} ms` : "—", ""],
    ["Reconnects (all clients)", String(snap.connectivity.reconnect_count), ""],
    ["Active connections", String(snap.connectivity.connections_now), ""],
    ["Total connections since start", String(snap.connectivity.total_connections_since_start), ""],
    ["Current device", ds.device_id ? truncate(ds.device_id, 20) : "—", ""],
    ["OpenCode server", snap.opencode.server_alive ? "available" : "unavailable", snap.opencode.server_alive ? "ok" : "bad"],
    ["Server uptime", fmtElapsed(snap.server.uptime_seconds), ""],
  ].map(([label, value, cls]) => `
    <div class="cc-kv"><div class="cc-kv-label">${label}</div><div class="cc-kv-value ${cls}">${esc(value)}</div></div>
  `).join("");

  renderServices(snap);
}

function renderServices(snap) {
  const services = [
    { name: "PresenceService (Android)", detail: state.deviceStatus ? "signal received" : "no signal", ok: !!state.deviceStatus },
    { name: "VoiceSessionManager", detail: "state machine active", ok: true },
    { name: "Supervisor", detail: "tool loop active", ok: true },
    { name: "AttentionScheduler", detail: "tick=15s", ok: true },
    { name: "VoiceSessionReaper", detail: "tick=60s, max_idle=900s", ok: true },
    { name: "Notification queue", detail: `${snap.pending_notifications.length} pending`, ok: true },
    { name: "OpenCode supervisor", detail: snap.opencode.server_alive ? "serving" : "down", ok: snap.opencode.server_alive },
  ];
  $("service-list").innerHTML = services.map((s) => `
    <div class="cc-service-row">
      <div><div class="cc-service-name">${esc(s.name)}</div><div class="cc-service-detail">${esc(s.detail)}</div></div>
      <div class="cc-service-status"><span class="cc-dot ${s.ok ? "ok" : "bad"}"></span>${s.ok ? "running" : "down"}</div>
    </div>`).join("");
}

// ── Snapshot ingestion ───────────────────────────────────────────────

async function loadSnapshot() {
  const res = await fetch("/api/dashboard/snapshot", { headers: authHeaders() });
  if (!res.ok) {
    // JARVIS_API_TOKEN set, no/wrong jarvis_api_token in localStorage.
    // Fail visibly, not by throwing out of boot() and silently killing
    // connectWs() (which runs right after this call) along with it.
    $("stat-ws-state").textContent = res.status === 401 ? "unauthorized" : `error ${res.status}`;
    return null;
  }
  const snap = await res.json();
  applySnapshotHealthAndConnectivity(snap);
  seedTasks(snap.tasks, snap.opencode_running_tasks);
  seedAttention(snap.pending_attention);
  for (const n of snap.pending_notifications) {
    pushAlert(`Notification: ${n.title}`, "info", n.created_at);
  }
  // Backfill the feed/decision stream/conversation from recent_events —
  // oldest first so newest ends up on top once rendered.
  for (const ev of snap.recent_events) {
    handleNamedEvent(ev.type, ev.content, ev.timestamp);
  }
  return snap;
}

// ── WebSocket ─────────────────────────────────────────────────────────

let ws = null;
let pingTimer = null;

async function connectWs() {
  $("stat-ws-state").textContent = "connecting…";
  let token = "";
  try {
    const r = await fetch("/api/ws-token", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ client_id: "control-center" }),
    });
    const j = await r.json();
    token = j.token || j.access_token || j.ws_token || "";
    // fetch() doesn't throw on a 401 -- if JARVIS_API_TOKEN is set and
    // localStorage has no/the wrong jarvis_api_token, r.json() here is
    // the {"detail": "Unauthorized"} error body, none of the token
    // fields above match, and token stays "". The /ws handshake below
    // then correctly gets WS_CLOSE_TOKEN_MISSING (the browser WebSocket
    // API can't set an Authorization header on the handshake either),
    // and onclose's retry loop keeps surfacing "reconnecting…" until a
    // valid token is set -- the same fail-closed behavior every other
    // authenticated client gets, not a silent bypass.
  } catch (e) { /* network error only (fetch() itself rejects) */ }

  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws${token ? `?token=${encodeURIComponent(token)}` : ""}`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    $("stat-ws-state").textContent = "live";
    setHealth("websocket", "ok");
    document.getElementById("brand-pulse").classList.remove("down");
    if (state.wsConnectedSince == null) state.wsConnectedSince = Date.now();
    else state.wsReconnects += 1;
    // Opts this connection into the dashboard-only event fan-out
    // (Supervisor tool calls/turns, voice-session lifecycle,
    // device_status echoes) — see ConnectionManager.broadcast_observers
    // in app/connection_manager.py. Never sent by Android/the PWA.
    try { ws.send(JSON.stringify({ type: "register_observer" })); } catch {}
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = setInterval(() => {
      state.lastPingSentAt = Date.now();
      try { ws.send(JSON.stringify({ type: "ping", t: state.lastPingSentAt })); } catch {}
    }, 5000);
  };

  ws.onclose = () => {
    $("stat-ws-state").textContent = "reconnecting…";
    setHealth("websocket", "bad");
    document.getElementById("brand-pulse").classList.add("down");
    if (pingTimer) clearInterval(pingTimer);
    setTimeout(connectWs, 2000);
  };

  ws.onerror = () => { /* onclose follows; nothing extra to do */ };

  ws.onmessage = (evt) => {
    let data;
    try { data = JSON.parse(evt.data); } catch { return; }
    routeWsMessage(data);
  };
}

function routeWsMessage(data) {
  switch (data.type) {
    case "pong":
      if (typeof data.t === "number") state.latencyMs = Date.now() - data.t;
      break;
    case "history":
      // /ws sends this same connection's own initial history dump on
      // every connect/reconnect, and GET /api/dashboard/snapshot's
      // recent_events already backfilled the exact same underlying
      // events table on page load. Rendering both was a real bug found
      // while verifying this end to end (every backfilled row appeared
      // twice, once from each source) — deliberately a no-op here so
      // the snapshot fetch stays the single source of truth for
      // historical backfill; this frame only matters to phone/PWA
      // clients, which have no separate snapshot endpoint of their own.
      break;
    case "running_tasks":
      for (const t of data.tasks || []) touchTask(t.task_id, { name: t.name, status: t.status });
      break;
    case "pending_questions":
      // Represented via Attention Center already; nothing additional to render.
      break;
    case "pending_notifications":
      // Same double-backfill reasoning as "history" above — GET
      // /api/dashboard/snapshot's pending_notifications already seeded
      // these as alert chips on page load.
      break;
    case "pending_attention":
      seedAttention(data.attention_requests || []);
      break;
    case "opencode_status":
      setHealth("opencode", data.server_alive ? "ok" : "bad");
      break;
    case "notification":
      pushAlert(`Notification: ${data.title}`, "info", data.created_at);
      break;
    default:
      // Everything else (including all the events-table style broadcasts,
      // attention_*, voice_session_*, supervisor_*, device_status_update)
      // shares the exact same handler the snapshot backfill uses — a
      // live broadcast and a historical events-table row have the same
      // {type, content, timestamp} shape for every type this dashboard
      // renders.
      handleNamedEvent(data.type, data.content ?? data, data.timestamp);
      break;
  }
}

// ── Clock / periodic connectivity poll ──────────────────────────────

function tickClock() {
  $("stat-clock").textContent = new Date().toLocaleTimeString([], { hour12: false });
}

async function pollConnectivity() {
  try {
    const res = await fetch("/api/dashboard/snapshot", { headers: authHeaders() });
    const snap = await res.json();
    applySnapshotHealthAndConnectivity(snap);
  } catch (e) { /* transient — next poll will retry */ }
}

// ── Boot ──────────────────────────────────────────────────────────────

(async function boot() {
  buildHealthRow();
  buildTimeline();
  await loadSnapshot();
  connectWs();
  setInterval(tickClock, 1000);
  setInterval(pollConnectivity, 5000);
  tickClock();
})();
