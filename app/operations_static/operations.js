const PENDING_STATES = new Set(["STARTING", "STOPPING"]);
const POLL_INTERVAL_MS = 1500;

let lastDashboard = null;

const els = {
  jarvis: {
    state: document.getElementById("jv-state"),
    health: document.getElementById("jv-health"),
    lastOp: document.getElementById("jv-last-op"),
    lastResult: document.getElementById("jv-last-result"),
    lastUpdated: document.getElementById("jv-last-updated"),
    pending: document.getElementById("jv-pending"),
    error: document.getElementById("jv-error"),
    start: document.getElementById("jv-start"),
    stop: document.getElementById("jv-stop"),
    restart: document.getElementById("jv-restart"),
  },
  opencode: {
    state: document.getElementById("oc-state"),
    health: document.getElementById("oc-health"),
    lastOp: document.getElementById("oc-last-op"),
    lastResult: document.getElementById("oc-last-result"),
    lastUpdated: document.getElementById("oc-last-updated"),
    pending: document.getElementById("oc-pending"),
    error: document.getElementById("oc-error"),
    start: document.getElementById("oc-start"),
    stop: document.getElementById("oc-stop"),
    restart: document.getElementById("oc-restart"),
  },
  supervisorState: document.getElementById("sup-state"),
  wsConnections: document.getElementById("ws-connections"),
  wsObservers: document.getElementById("ws-observers"),
  wsReconnects: document.getElementById("ws-reconnects"),
  wsHeartbeat: document.getElementById("ws-heartbeat"),
  phConnection: document.getElementById("ph-connection"),
  phDevice: document.getElementById("ph-device"),
  cpMode: document.getElementById("cp-mode"),
  cpSelect: document.getElementById("cp-select"),
  cpSet: document.getElementById("cp-set"),
  cpError: document.getElementById("cp-error"),
  histTarget: document.getElementById("hist-target"),
  histStatus: document.getElementById("hist-status"),
  histRefresh: document.getElementById("hist-refresh"),
  histBody: document.getElementById("hist-body"),
};

function setClass(el, prefix, value) {
  el.className = "";
  if (value) el.classList.add(`${prefix}-${value}`);
  el.textContent = value || "—";
}

function renderLifecyclePanel(p, status) {
  if (!status) {
    setClass(p.state, "state", null);
    setClass(p.health, "health", null);
    p.error.hidden = true;
    p.start.disabled = true;
    p.stop.disabled = true;
    p.restart.disabled = true;
    return;
  }
  setClass(p.state, "state", status.state);
  setClass(p.health, "health", status.health);
  p.lastOp.textContent = status.last_operation || "—";
  p.lastResult.textContent = status.last_operation_result || "—";
  p.lastUpdated.textContent = status.last_updated || "—";
  p.pending.textContent = PENDING_STATES.has(status.state) ? (status.last_operation || "in progress") : "None";

  if (status.last_operation_result === "failure" && status.last_operation_error) {
    p.error.hidden = false;
    p.error.textContent = `Last ${status.last_operation} failed: ${status.last_operation_error}`;
  } else {
    p.error.hidden = true;
  }

  const busy = PENDING_STATES.has(status.state);
  p.start.disabled = busy || status.state === "RUNNING";
  p.stop.disabled = busy || !(status.state === "RUNNING" || status.state === "FAILED");
  p.restart.disabled = busy || !(status.state === "RUNNING" || status.state === "FAILED");
}

function renderReadonly(dashboard) {
  const sup = dashboard.supervisor;
  els.supervisorState.textContent = sup ? sup.state : "—";

  const ws = dashboard.websocket;
  els.wsConnections.textContent = ws ? ws.connections_now : "—";
  els.wsObservers.textContent = ws ? ws.observers_now : "—";
  els.wsReconnects.textContent = ws ? ws.reconnect_count : "—";
  els.wsHeartbeat.textContent = ws ? (ws.last_heartbeat_at || "never") : "—";

  const phone = dashboard.phone;
  const deviceStatus = phone ? phone.device_status : null;
  const connections = phone ? phone.connections_now : 0;
  setClass(els.phConnection, "connection", connections > 0 ? "CONNECTED" : "DISCONNECTED");
  els.phDevice.textContent = deviceStatus ? JSON.stringify(deviceStatus) : "none reported";

  els.cpMode.textContent = dashboard.connectivity_mode || "—";
  if (dashboard.connectivity_mode && document.activeElement !== els.cpSelect) {
    els.cpSelect.value = dashboard.connectivity_mode;
  }
}

function showError(el, message) {
  el.hidden = false;
  el.textContent = message;
}

async function fetchDashboard() {
  try {
    const resp = await fetch("/api/console/dashboard");
    const body = await resp.json();
    if (!resp.ok) {
      showError(els.jarvis.error, body.detail || "Failed to fetch dashboard");
      return;
    }
    lastDashboard = body;
    renderLifecyclePanel(els.jarvis, body.jarvis);
    renderLifecyclePanel(els.opencode, body.opencode);
    renderReadonly(body);
  } catch (e) {
    showError(els.jarvis.error, `Cannot reach the Operations console: ${e}`);
  }
}

function pendingStateFor(action) {
  if (action === "start") return "STARTING";
  return "STOPPING";
}

async function callAction(panelKey, apiTarget, action, needsConfirm, confirmMessage) {
  if (needsConfirm && !window.confirm(confirmMessage)) {
    return;
  }
  const p = els[panelKey];
  const current = lastDashboard ? lastDashboard[panelKey] : null;
  renderLifecyclePanel(p, { ...(current || {}), state: pendingStateFor(action) });
  try {
    const resp = await fetch(`/api/console/${apiTarget}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      showError(p.error, body.detail || `${action} failed`);
      await fetchDashboard();
    } else {
      renderLifecyclePanel(p, body);
    }
  } catch (e) {
    showError(p.error, `Cannot reach the Operations console: ${e}`);
  }
}

function wireLifecycleButtons(panelKey, apiTarget, label) {
  const p = els[panelKey];
  p.start.addEventListener("click", () => callAction(panelKey, apiTarget, "start", false));
  p.stop.addEventListener("click", () => callAction(panelKey, apiTarget, "stop", true,
    `Stop ${label} now? This will interrupt anything currently in progress.`));
  p.restart.addEventListener("click", () => callAction(panelKey, apiTarget, "restart", true,
    `Restart ${label} now? This will interrupt anything currently in progress.`));
}

wireLifecycleButtons("jarvis", "jarvis", "Jarvis");
wireLifecycleButtons("opencode", "opencode", "OpenCode");

els.cpSet.addEventListener("click", async () => {
  els.cpError.hidden = true;
  els.cpSet.disabled = true;
  try {
    const resp = await fetch("/api/console/connectivity/policy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: els.cpSelect.value }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      showError(els.cpError, body.detail || "Failed to set policy");
    } else {
      els.cpMode.textContent = body.mode;
    }
  } catch (e) {
    showError(els.cpError, `Cannot reach the Operations console: ${e}`);
  } finally {
    els.cpSet.disabled = false;
  }
});

function formatDuration(ms) {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

async function fetchHistory() {
  const params = new URLSearchParams();
  if (els.histTarget.value) params.set("target", els.histTarget.value);
  if (els.histStatus.value) params.set("status", els.histStatus.value);
  try {
    const resp = await fetch(`/api/console/operations?${params}`);
    const rows = await resp.json();
    els.histBody.innerHTML = "";
    for (const row of rows) {
      const tr = document.createElement("tr");
      const statusClass = `status-${row.status}`;
      tr.innerHTML = `
        <td>${row.created_at || "—"}</td>
        <td>${row.operator}</td>
        <td>${row.target}</td>
        <td>${row.action}</td>
        <td class="${statusClass}">${row.status}</td>
        <td>${formatDuration(row.duration_ms)}</td>
        <td class="error-cell">${row.error || ""}</td>
      `;
      els.histBody.appendChild(tr);
    }
  } catch (e) {
    // History is secondary to live status; fail quietly rather than
    // blocking the rest of the dashboard with an error banner.
  }
}

els.histRefresh.addEventListener("click", fetchHistory);
els.histTarget.addEventListener("change", fetchHistory);
els.histStatus.addEventListener("change", fetchHistory);

fetchDashboard();
fetchHistory();
setInterval(fetchDashboard, POLL_INTERVAL_MS);
setInterval(fetchHistory, POLL_INTERVAL_MS * 4);
