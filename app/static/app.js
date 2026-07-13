var ws = null;
var reconnectTimer = null;
var wsToken = null;
var wsTokenExpiresAt = 0;
var activeTasks = {};
var pendingQuestions = {};
var conversationId = null;

/* Milestone 8: persistent AttentionRequest call-style UI + voice session
   client state. Kept separate from pendingQuestions/pendingPermissions
   above — those remain the M7 worker-question answer UI; this tracks
   Jarvis's own "someone needs to be contacted" state. */
var attentionRequests = {};
var ATTENTION_TERMINAL_STATUSES = ["resolved", "cancelled", "expired"];
var currentVoiceSessionId = null;
var voiceSessionAttentionId = null;

/* Milestone 9B.2 (ADR-014): persistent client identity for WS token flow. */
function loadStoredClientId() {
  try {
    var id = window.localStorage.getItem("jarvis_client_id");
    if (id) return id;
    /* First visit — generate a UUID. crypto.randomUUID() is available in
       secure contexts (HTTPS), which this app already requires. */
    id = (crypto.randomUUID && crypto.randomUUID()) || generateFallbackId();
    storeClientId(id);
    return id;
  } catch (e) {
    return null;
  }
}

function storeClientId(id) {
  try {
    window.localStorage.setItem("jarvis_client_id", id);
  } catch (e) {
    /* localStorage unavailable — best-effort only, not fatal. */
  }
}

function generateFallbackId() {
  /* crypto.randomUUID fallback for any non-secure-context edge case. */
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    var r = Math.random() * 16 | 0;
    return (c === "x" ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

/* Milestone 6 Phase 7: stable conversation identity across reconnects.
   localStorage is acceptable for this prototype (single browser/user);
   a server-generated ID is still authoritative — the client never invents
   one itself, it only persists what the server hands back. */
function loadStoredConversationId() {
  try {
    return window.localStorage.getItem("jarvis_conversation_id");
  } catch (e) {
    return null;
  }
}

function storeConversationId(id) {
  try {
    window.localStorage.setItem("jarvis_conversation_id", id);
  } catch (e) {
    /* localStorage unavailable (private browsing etc.) — continuity is
       best-effort only in that case, not fatal. */
  }
}

/* Milestone 9B.2 (ADR-014): fetch a short-lived signed WS token. */
function fetchToken(clientId) {
  return fetch("/api/ws-token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: clientId }),
  }).then(function (r) {
    if (!r.ok) throw new Error("Token fetch failed: " + r.status);
    return r.json();
  });
}

function sendUserMessage(content) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "user_message", content: content, conversation_id: conversationId }));
}

async function connect() {
  var proto = location.protocol === "https:" ? "wss:" : "ws:";
  var now = Math.floor(Date.now() / 1000);

  if (!wsToken || !wsTokenExpiresAt || (wsTokenExpiresAt - now) < 120) {
    var clientId = loadStoredClientId();
    if (!clientId) {
      /* localStorage unavailable — connect without a token; the server may
         accept the connection or reject it (code 4003). */
      ws = new WebSocket(proto + "//" + location.host + "/ws");
      attachWsHandlers();
      return;
    }
    try {
      var data = await fetchToken(clientId);
      wsToken = data.token;
      wsTokenExpiresAt = data.expires_at;
    } catch (e) {
      setStatus("error");
      scheduleReconnect();
      return;
    }
  }

  ws = new WebSocket(proto + "//" + location.host + "/ws?token=" + encodeURIComponent(wsToken));
  attachWsHandlers();
}

function attachWsHandlers() {
  ws.onopen = function () {
    setStatus("connected");
    clearTimeout(reconnectTimer);
    ws.send(JSON.stringify({ type: "conversation_init", conversation_id: loadStoredConversationId() }));
  };

  ws.onclose = function (event) {
    setStatus("disconnected");
    if (event.code === 4001 || event.code === 4002) {
      wsToken = null;
      wsTokenExpiresAt = 0;
    }
    scheduleReconnect();
  };

  ws.onerror = function () {
    setStatus("error");
  };

  ws.onmessage = function (event) {
    var data = JSON.parse(event.data);
    handleEvent(data);
  };
}

function scheduleReconnect() {
  reconnectTimer = setTimeout(function () {
    connect();
  }, 3000);
}

function setStatus(status) {
  var dot = document.getElementById("status-dot");
  var text = document.getElementById("status-text");
  dot.className = "status-" + status;
  var labels = { connected: "Connected", disconnected: "Disconnected", error: "Error" };
  text.textContent = labels[status] || status;
}

/* ========== Event Router ========== */

function handleEvent(data) {
  if (data.type === "conversation_ready") {
    conversationId = data.conversation_id;
    storeConversationId(conversationId);
    // Only restore assistant/supervisor replies here — user messages for
    // this conversation are already replayed by the "history" event below
    // (the global event timeline). Assistant replies were never persisted
    // there at all (main.py never calls save_event for supervisor_message),
    // so this is filling a real gap, not creating a duplicate.
    (data.history || []).forEach(function (m) {
      if (m.role !== "assistant") return;
      addToTimeline({ type: "supervisor_message", content: m.content, timestamp: m.created_at });
    });
    return;
  }
  if (data.type === "history") {
    data.events.forEach(function (e) { addToTimeline(e); });
    return;
  }
  if (data.type === "running_tasks") {
    data.tasks.forEach(function (t) { addActiveTask(t); });
    return;
  }
  if (data.type === "pending_questions") {
    data.questions.forEach(function (q) { addPendingQuestion(q); });
    return;
  }
  if (data.type === "opencode_status") {
    handleOpenCodeStatus(data);
    return;
  }
  if (data.type === "task_permission") {
    addPendingPermission(data);
    return;
  }
  if (data.type === "opencode_permission_handled") {
    removePendingPermission(data.permission_id);
    if (activeTasks[data.task_id]) {
      activeTasks[data.task_id].status = "running";
      renderActiveTasks();
    }
    return;
  }
  if (data.type === "opencode_task_created") {
    addActiveTask({ task_id: data.task_id, name: data.instruction ? data.instruction.substring(0, 50) : "OpenCode Task", status: "running", elapsed: 0, timestamp: data.timestamp });
    return;
  }
  if (data.type === "opencode_task_completed" || data.type === "opencode_task_cancelled") {
    removeActiveTask(data.task_id);
    addToTimeline(data);
    return;
  }
  if (data.type === "opencode_question_answered" || data.type === "opencode_question_rejected") {
    removePendingQuestion(data.question_id);
    if (activeTasks[data.task_id]) {
      activeTasks[data.task_id].status = "running";
      renderActiveTasks();
    }
    return;
  }
  if (data.type === "supervisor_thinking") {
    showSupervisorThinking();
    return;
  }
  if (data.type === "supervisor_message") {
    hideSupervisorThinking();
    addToTimeline(data);
    speak(data.content);
    return;
  }
  if (data.type === "notification") {
    handleLiveNotification(data);
    return;
  }
  if (data.type === "pending_notifications") {
    (data.notifications || []).forEach(function (n) { renderNotificationInTimeline(n); });
    return;
  }
  if (data.type === "pending_attention") {
    (data.attention_requests || []).forEach(function (a) { handleAttentionUpdate(a); });
    return;
  }
  if (
    data.type === "attention_created" || data.type === "attention_contacting" ||
    data.type === "attention_pending" || data.type === "attention_deferred" ||
    data.type === "attention_resolving" || data.type === "attention_resolved" ||
    data.type === "attention_cancelled" || data.type === "attention_expired"
  ) {
    handleAttentionUpdate(data);
    return;
  }
  if (data.type === "voice_session_opened") {
    currentVoiceSessionId = data.voice_session_id;
    voiceSessionAttentionId = data.attention_request_id || null;
    if (!conversationId && data.conversation_id) {
      conversationId = data.conversation_id;
      storeConversationId(conversationId);
    }
    if (data.greeting) {
      // Real-phone finding (Milestone 8.1, 2026-07-10): a bound voice
      // session used to go straight to listening with nothing spoken —
      // a re-contacted user had no way to know what they were being
      // asked about. speakForVoiceSession() shows+speaks the context,
      // then starts listening itself once done.
      showVoiceSessionBar("Jarvis is speaking…");
      speakForVoiceSession(data.greeting);
    } else {
      showVoiceSessionBar("Listening…");
      startVoiceSessionListening();
    }
    return;
  }
  if (data.type === "voice_session_response") {
    if (data.voice_session_id !== currentVoiceSessionId) return; // stale/already-closed session
    if (data.voice_session_state === "deferred") {
      speakOnceThenCloseVoiceSession(data.response);
    } else {
      speakForVoiceSession(data.response);
    }
    return;
  }
  if (data.type === "voice_session_error") {
    setTransientStatus(data.error || "Voice session error.");
    return;
  }
  if (data.type === "voice_session_closed") {
    return; // client already updates its own UI when it initiates a close
  }
  if (data.type === "voice_session_invitation") {
    // Phase 13 architecture-bridge signal for a future proactive voice
    // presence — the attention_* broadcasts already drive the call-style
    // card (Talk now/snooze/dismiss) for this same event, so this is
    // intentionally a no-op here rather than a raw timeline entry.
    return;
  }

  addToTimeline(data);

  if (data.type === "task_started" && data.content) {
    try { var c = JSON.parse(data.content); addActiveTask({ task_id: c.task_id, name: c.name, status: "running", elapsed: 0, timestamp: data.timestamp }); } catch (e) {}
    return;
  }
  if (data.type === "task_stdout" && data.content) {
    try { var c = JSON.parse(data.content); updateActiveTaskLine(c.task_id, c.line); } catch (e) {}
    return;
  }
  if (data.type === "task_completed" || data.type === "task_failed" || data.type === "task_cancelled") {
    try { var c = JSON.parse(data.content); removeActiveTask(c.task_id); } catch (e) {}
    return;
  }
  if (data.type === "question_asked" && data.content) {
    try {
      var c = JSON.parse(data.content);
      addPendingQuestion(c);
      if (activeTasks[c.task_id]) {
        activeTasks[c.task_id].status = "waiting_for_user";
        renderActiveTasks();
      }
    } catch (e) {}
    return;
  }
  if (data.type === "question_answered" && data.content) {
    try {
      var c = JSON.parse(data.content);
      removePendingQuestion(c.question_id);
      if (activeTasks[c.task_id]) {
        activeTasks[c.task_id].status = "running";
        renderActiveTasks();
      }
    } catch (e) {}
    return;
  }
  if (data.type === "question_cancelled" && data.content) {
    try {
      var c = JSON.parse(data.content);
      removePendingQuestion(c.question_id);
    } catch (e) {}
    return;
  }
}

/* ========== Timeline ========== */

var renderedEventKeys = {};

function addToTimeline(ev) {
  // Milestone 6 Phase 10/11: an event that was already rendered live (via
  // WebSocket broadcast) is also persisted and replayed by the "history"
  // event on every reconnect — without this guard it renders twice.
  // Timestamps carry microsecond precision (see database.py utcnow()), so
  // type+timestamp+content is a practically-unique key for this purpose.
  var dedupKey = ev.type + "|" + (ev.timestamp || "") + "|" + (ev.content || "");
  if (renderedEventKeys[dedupKey]) return;
  renderedEventKeys[dedupKey] = true;

  var tl = document.getElementById("timeline");
  var el = document.createElement("div");
  el.className = "event event-" + ev.type;

  var displayContent = ev.content || "";
  if (isTaskEvent(ev.type) && ev.content) {
    try {
      var c = JSON.parse(ev.content);
      if (ev.type === "task_started") displayContent = "Task started: " + (c.name || "");
      else if (ev.type === "task_stdout") displayContent = "[" + (c.name || "task") + "] " + (c.line || "");
      else if (ev.type === "task_stderr") displayContent = "[" + (c.name || "task") + " stderr] " + (c.line || "");
      else if (ev.type === "task_completed") displayContent = "Task completed: " + (c.name || "") + " (exit=" + (c.exit_code !== undefined ? c.exit_code : "?") + ")";
      else if (ev.type === "task_failed") displayContent = "Task failed: " + (c.name || "") + " (exit=" + (c.exit_code !== undefined ? c.exit_code : "?") + ")";
      else if (ev.type === "task_cancelled") displayContent = "Task cancelled: " + (c.name || "");
    } catch (e) {}
  }
  if (isQuestionEvent(ev.type) && ev.content) {
    try {
      var c = JSON.parse(ev.content);
      if (ev.type === "question_asked") displayContent = (c.task_name || "Task") + " needs your attention: " + (c.question || "");
      else if (ev.type === "question_answered") displayContent = "You answered: " + (c.answer || "") + " \u2192 delivered to " + (c.task_name || "task");
      else if (ev.type === "question_cancelled") displayContent = "Question cancelled for " + (c.task_name || "task");
    } catch (e) {}
  }
  if (ev.type === "opencode_message") {
    displayContent = "[" + ev.role + "] " + (ev.content || "");
  }
  if (ev.type === "opencode_task_completed") {
    var statusWord = ev.status === "failed" ? "failed" : ev.status === "cancelled" ? "cancelled" : "completed";
    displayContent = "OpenCode task " + statusWord + ": " + (ev.task_id ? ev.task_id.substring(0, 12) : "");
  }
  if (ev.type === "opencode_error") {
    displayContent = "OpenCode error";
  }

  var label = eventLabel(ev.type);
  var time = (ev.timestamp || "").replace("T", " ").replace("Z", "").substring(0, 19);

  el.innerHTML = "<strong>" + label + "</strong><p>" + escapeHtml(displayContent) + "</p><small>" + time + "</small>";
  tl.appendChild(el);
  tl.scrollTop = tl.scrollHeight;
}

function isTaskEvent(type) {
  return type === "task_started" || type === "task_stdout" || type === "task_stderr" || type === "task_completed" || type === "task_failed" || type === "task_cancelled";
}

function isQuestionEvent(type) {
  return type === "question_asked" || type === "question_answered" || type === "question_cancelled";
}

function eventLabel(type) {
  switch (type) {
    case "user_message": return "You";
    case "command_started": return "Jarvis";
    case "command_output": return "Output";
    case "command_completed": return "Done";
    case "command_failed": return "Error";
    case "system_message": return "System";
    case "task_started": return "Task";
    case "task_stdout": return "Task";
    case "task_stderr": return "Task";
    case "task_completed": return "Task";
    case "task_failed": return "Task";
    case "task_cancelled": return "Task";
    case "question_asked": return "Question";
    case "question_answered": return "Answer";
    case "question_cancelled": return "Question";
    case "opencode_message": return "OpenCode";
    case "opencode_task_completed": return "OpenCode";
    case "opencode_error": return "OpenCode Error";
    case "supervisor_message": return "Jarvis";
    default: return type;
  }
}

function escapeHtml(text) {
  var d = document.createElement("div");
  d.appendChild(document.createTextNode(text));
  return d.innerHTML;
}

function sendMessage() {
  var input = document.getElementById("message-input");
  var content = input.value.trim();
  if (!content || !ws || ws.readyState !== WebSocket.OPEN) return;

  sendUserMessage(content);
  input.value = "";
}

/* ========== Active Tasks ========== */

function addActiveTask(task) {
  activeTasks[task.task_id] = task;
  task.startedAt = task.startedAt || Date.now();
  renderActiveTasks();
  showActiveTasks();
}

function updateActiveTaskLine(taskId, line) {
  if (activeTasks[taskId]) {
    activeTasks[taskId].latestLine = line;
    renderActiveTasks();
  }
}

function removeActiveTask(taskId) {
  delete activeTasks[taskId];
  renderActiveTasks();
  if (Object.keys(activeTasks).length === 0) {
    hideActiveTasks();
  }
}

function showActiveTasks() {
  document.getElementById("active-tasks").style.display = "block";
}

function hideActiveTasks() {
  document.getElementById("active-tasks").style.display = "none";
}

function renderActiveTasks() {
  var list = document.getElementById("task-list");
  list.innerHTML = "";
  var now = Date.now();
  Object.keys(activeTasks).forEach(function (tid) {
    var t = activeTasks[tid];
    var elapsed = Math.floor((now - (t.startedAt || now)) / 1000);
    var m = Math.floor(elapsed / 60);
    var s = elapsed % 60;
    var elapsedStr = m + ":" + (s < 10 ? "0" : "") + s;
    var line = t.latestLine || "";
    var status = t.status || "running";
    var statusClass = status === "waiting_for_user" ? "status-waiting" : "status-running";
    var statusLabel = status === "waiting_for_user" ? "waiting" : status === "running" ? "running" : status;
    var item = document.createElement("div");
    item.className = "task-item";
    item.setAttribute("data-task-id", tid);
    item.innerHTML =
      '<div class="task-header">' +
        '<span class="task-name">' + escapeHtml(t.name || "Task") + '</span>' +
        '<span class="task-elapsed">' + elapsedStr + '</span>' +
        '<span class="task-status-badge ' + statusClass + '">' + statusLabel + '</span>' +
      '</div>' +
      '<div class="task-latest">' + escapeHtml(line) + '</div>' +
      '<button class="cancel-btn" data-task-id="' + escapeHtml(tid) + '">Cancel</button>';
    list.appendChild(item);
  });
}

/* ========== Pending Questions ========== */

function addPendingQuestion(q) {
  pendingQuestions[q.question_id] = q;
  renderPendingQuestions();
}

function removePendingQuestion(qid) {
  delete pendingQuestions[qid];
  renderPendingQuestions();
}

function renderPendingQuestions() {
  var container = document.getElementById("needs-attention");
  var list = document.getElementById("question-list");
  list.innerHTML = "";
  var qids = Object.keys(pendingQuestions);
  if (qids.length === 0) {
    container.style.display = "none";
    return;
  }
  container.style.display = "block";
  qids.forEach(function (qid) {
    var q = pendingQuestions[qid];
    var item = document.createElement("div");
    item.className = "question-item";
    item.setAttribute("data-question-id", qid);
    var html = '<div class="question-task-name">' + escapeHtml(q.task_name || "Task") + '</div>';
    html += '<div class="question-text">' + escapeHtml(q.question || "") + '</div>';
    if (q.context) {
      html += '<div class="question-context">' + escapeHtml(q.context) + '</div>';
    }
    if (q.options && q.options.length > 0) {
      html += '<div class="question-options">';
      q.options.forEach(function (opt) {
        html += '<button class="option-btn" data-question-id="' + escapeHtml(qid) + '" data-answer="' + escapeHtml(opt) + '">' + escapeHtml(opt) + '</button>';
      });
      html += '</div>';
    }
    html += '<div class="question-custom-answer">';
    if (voiceSupported()) {
      html += '<button class="question-mic-btn icon-btn mic-idle" type="button" data-question-id="' + escapeHtml(qid) + '" aria-label="Answer by voice">🎙️</button>';
    }
    html += '<input type="text" class="question-answer-input" placeholder="Type custom answer..." data-question-id="' + escapeHtml(qid) + '">';
    html += '<button class="question-send-btn" data-question-id="' + escapeHtml(qid) + '">Send</button>';
    html += '</div>';
    html += '<button class="cancel-btn" data-task-id="' + escapeHtml(q.task_id) + '">Cancel Task</button>';
    item.innerHTML = html;
    list.appendChild(item);
  });
}

/* ========== Permissions ========== */

var pendingPermissions = {};

function addPendingPermission(p) {
  pendingPermissions[p.permission_id] = p;
  renderPendingPermissions();
}

function removePendingPermission(pid) {
  delete pendingPermissions[pid];
  renderPendingPermissions();
}

function renderPendingPermissions() {
  var list = document.getElementById("question-list");
  var pids = Object.keys(pendingPermissions);
  if (pids.length === 0) return;
  document.getElementById("needs-attention").style.display = "block";
  pids.forEach(function (pid) {
    var p = pendingPermissions[pid];
    if (document.querySelector('[data-permission-id="' + escapeHtml(pid) + '"]')) return;
    var item = document.createElement("div");
    item.className = "question-item permission-item";
    item.setAttribute("data-permission-id", pid);
    item.innerHTML =
      '<div class="question-task-name">Permission: ' + escapeHtml(p.task_id ? p.task_id.substring(0, 12) : "Task") + '</div>' +
      '<div class="question-text">Allow <strong>' + escapeHtml(p.action || "unknown") + '</strong> ' + escapeHtml(p.path || "") + '?</div>' +
      '<div class="question-options">' +
        '<button class="permission-approve-btn" data-permission-id="' + escapeHtml(pid) + '" data-approved="true">Approve</button>' +
        '<button class="permission-deny-btn" data-permission-id="' + escapeHtml(pid) + '" data-approved="false">Deny</button>' +
      '</div>';
    list.appendChild(item);
  });
}

/* ========== Attention Calls (Milestone 8) ========== */
/* Call-style UI for a persistent AttentionRequest: reason, Talk now,
   snooze options, dismiss. Distinct from the pendingQuestions/
   pendingPermissions panel above, which remains the direct worker-answer
   UI unchanged since Milestone 7 — this is about *contact*, not about
   answering. Talk now opens a bound voice session (below); the snooze/
   dismiss buttons reuse the exact same deterministic natural-language
   defer fast path (app/deferral.py) a spoken "come back in 15 minutes"
   would hit, scoped to this one request via bound_attention_request_id so
   it never has to guess which item among several unrelated ones. */

function handleAttentionUpdate(data) {
  if (data.status && ATTENTION_TERMINAL_STATUSES.indexOf(data.status) !== -1) {
    delete attentionRequests[data.attention_request_id];
    renderAttentionCalls();
    return;
  }
  var existing = attentionRequests[data.attention_request_id] || {};
  var merged = {};
  for (var k in existing) merged[k] = existing[k];
  for (var k2 in data) merged[k2] = data[k2];
  attentionRequests[data.attention_request_id] = merged;
  renderAttentionCalls();
}

function sendAttentionCommand(attentionRequestId, phrase) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({
    type: "user_message",
    content: phrase,
    conversation_id: conversationId,
    bound_attention_request_id: attentionRequestId,
  }));
}

function formatDeferredUntil(iso) {
  try {
    return new Date(iso).toLocaleString([], { hour: "2-digit", minute: "2-digit" });
  } catch (e) {
    return iso;
  }
}

function renderAttentionCalls() {
  var container = document.getElementById("attention-calls");
  var list = document.getElementById("attention-call-list");
  list.innerHTML = "";
  var ids = Object.keys(attentionRequests);
  if (ids.length === 0) {
    container.style.display = "none";
    return;
  }
  container.style.display = "block";
  ids.forEach(function (id) {
    var a = attentionRequests[id];
    var deferred = a.status === "deferred";
    var item = document.createElement("div");
    item.className = "attention-call-item" + (deferred ? " attention-call-deferred" : "");
    item.setAttribute("data-attention-request-id", id);
    var html = '<div class="attention-call-summary">' + escapeHtml(a.summary || "Jarvis needs your input") + '</div>';
    if (deferred && a.deferred_until) {
      html += '<div class="attention-call-meta">Deferred until ' + escapeHtml(formatDeferredUntil(a.deferred_until)) + '</div>';
    } else {
      html += '<div class="attention-call-meta">' + escapeHtml(a.attention_type || "") + '</div>';
    }
    if (!deferred) {
      html += '<div class="attention-call-actions">';
      html += '<button class="attention-call-talk-btn" data-attention-request-id="' + escapeHtml(id) + '">Talk now</button>';
      html += '<button class="attention-call-snooze-btn" data-attention-request-id="' + escapeHtml(id) + '" data-phrase="15 minutes">15 min</button>';
      html += '<button class="attention-call-snooze-btn" data-attention-request-id="' + escapeHtml(id) + '" data-phrase="1 hour">1 hour</button>';
      html += '<button class="attention-call-snooze-btn" data-attention-request-id="' + escapeHtml(id) + '" data-phrase="tomorrow morning">Tomorrow AM</button>';
      html += '<button class="attention-call-dismiss-btn" data-attention-request-id="' + escapeHtml(id) + '" data-phrase="later">Dismiss</button>';
      html += '</div>';
    }
    item.innerHTML = html;
    list.appendChild(item);
  });
}

/* ========== Voice Session Client (Milestone 8 Phase 10/11/12) ========== */
/* Bridges the browser's existing SpeechRecognition/TTS (unchanged since
   Milestone 7) to the server-side VoiceSessionManager state machine. This
   is a second, independent conversational loop from the main-input mic
   above — reuses the same `recognition`/`voiceCancelled` singleton so
   "exactly one concurrent recognition session" still holds across every
   mic entry point in the app, but drives it via voice_session_* WS
   messages instead of a plain user_message. */

function showVoiceSessionBar(label) {
  var bar = document.getElementById("voice-session-bar");
  var lbl = document.getElementById("voice-session-state-label");
  if (lbl) lbl.textContent = label;
  if (bar) bar.style.display = "flex";
}

function hideVoiceSessionBar() {
  var bar = document.getElementById("voice-session-bar");
  if (bar) bar.style.display = "none";
}

function openVoiceSession(attentionRequestId) {
  if (!voiceSupported()) {
    setTransientStatus("Voice input is not supported in this browser.");
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (currentVoiceSessionId) return; // one voice session at a time
  stopSpeaking();
  ws.send(JSON.stringify({
    type: "voice_session_open",
    conversation_id: conversationId,
    attention_request_id: attentionRequestId || null,
  }));
}

function startVoiceSessionListening() {
  if (!voiceSupported() || !currentVoiceSessionId) return;
  if (recognition) {
    try { recognition.abort(); } catch (e) {}
    recognition = null;
  }
  voiceCancelled = false;
  recognition = new SpeechRecognitionCtor();
  recognition.lang = "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = function () {
    showVoiceSessionBar("Listening…");
  };
  recognition.onresult = function (event) {
    if (voiceCancelled || !currentVoiceSessionId) return;
    var transcript = (event.results[0][0].transcript || "").trim();
    if (!transcript || !ws || ws.readyState !== WebSocket.OPEN) return;
    // Real-phone finding (Milestone 8.1, 2026-07-10): unlike the main-mic
    // flow (whose transcript is echoed via the normal user_message
    // broadcast), a bound voice session's recognized transcript was never
    // shown anywhere — the user could not tell what Jarvis heard, even
    // though it acted on it correctly. Echo it into the timeline the same
    // way a typed message would appear, before sending it to the server.
    addToTimeline({ type: "user_message", content: transcript, timestamp: new Date().toISOString() });
    showVoiceSessionBar("Thinking…");
    ws.send(JSON.stringify({
      type: "voice_session_transcript",
      voice_session_id: currentVoiceSessionId,
      transcript: transcript,
    }));
  };
  recognition.onerror = function (event) {
    recognition = null;
    // Give the user another chance to speak rather than ending the call on
    // a single recognition hiccup (e.g. transient "no-speech").
    if (!voiceCancelled && currentVoiceSessionId) startVoiceSessionListening();
  };
  recognition.onend = function () {
    recognition = null;
  };

  try {
    recognition.start();
  } catch (e) {}
}

function speakForVoiceSession(text) {
  addToTimeline({ type: "supervisor_message", content: text, timestamp: new Date().toISOString() });
  if (!ttsSupported || !speechEnabled) {
    if (currentVoiceSessionId) startVoiceSessionListening();
    return;
  }
  if (window.speechSynthesis.speaking) window.speechSynthesis.cancel();
  var utter = new SpeechSynthesisUtterance(truncateForSpeech(text));
  utter.onend = function () { if (currentVoiceSessionId) startVoiceSessionListening(); };
  utter.onerror = function () { if (currentVoiceSessionId) startVoiceSessionListening(); };
  window.speechSynthesis.speak(utter);
}

function speakOnceThenCloseVoiceSession(text) {
  addToTimeline({ type: "supervisor_message", content: text, timestamp: new Date().toISOString() });
  showVoiceSessionBar("Deferred");
  var finish = function () { closeVoiceSession(); };
  if (!ttsSupported || !speechEnabled) {
    setTimeout(finish, 800);
    return;
  }
  if (window.speechSynthesis.speaking) window.speechSynthesis.cancel();
  var utter = new SpeechSynthesisUtterance(truncateForSpeech(text));
  utter.onend = finish;
  utter.onerror = finish;
  window.speechSynthesis.speak(utter);
}

function closeVoiceSession() {
  if (recognition) {
    try { recognition.abort(); } catch (e) {}
    recognition = null;
  }
  stopSpeaking();
  if (ws && ws.readyState === WebSocket.OPEN && currentVoiceSessionId) {
    ws.send(JSON.stringify({ type: "voice_session_close", voice_session_id: currentVoiceSessionId }));
  }
  currentVoiceSessionId = null;
  voiceSessionAttentionId = null;
  hideVoiceSessionBar();
}

/* ========== OpenCode Status ========== */

function handleOpenCodeStatus(data) {
  var statusEl = document.getElementById("status");
  var existingOc = document.getElementById("opencode-status");
  if (!existingOc) {
    var ocEl = document.createElement("div");
    ocEl.id = "opencode-status";
    ocEl.style.cssText = "font-size:11px;color:#888;margin-top:2px;";
    statusEl.appendChild(ocEl);
  }
  var el = document.getElementById("opencode-status");
  el.textContent = "OpenCode: " + (data.server_alive ? "connected" : "disconnected");
  el.style.color = data.server_alive ? "#4caf50" : "#f44336";
}

/* ========== Supervisor Thinking Indicator ========== */

function showSupervisorThinking() {
  var existing = document.getElementById("supervisor-thinking");
  if (existing) return;
  var tl = document.getElementById("timeline");
  var el = document.createElement("div");
  el.id = "supervisor-thinking";
  el.className = "event event-supervisor-thinking";
  el.innerHTML = "<strong>Jarvis</strong><p><em>Thinking...</em></p>";
  tl.appendChild(el);
  tl.scrollTop = tl.scrollHeight;
}

function hideSupervisorThinking() {
  var el = document.getElementById("supervisor-thinking");
  if (el) el.remove();
}

/* ========== Click Handlers ========== */

document.addEventListener("click", function (e) {
  // Cancel button (shared across active tasks and questions)
  if (e.target.classList.contains("cancel-btn")) {
    var tid = e.target.getAttribute("data-task-id");
    if (!tid) return;
    e.target.disabled = true;
    e.target.textContent = "Cancelling...";
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "user_message", content: "/cancel " + tid }));
    }
    return;
  }
  // Option button
  if (e.target.classList.contains("option-btn")) {
    var qid = e.target.getAttribute("data-question-id");
    var answer = e.target.getAttribute("data-answer");
    if (qid && answer && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "user_message", content: "/answer " + qid + " " + answer }));
      e.target.disabled = true;
      e.target.textContent = "Sent";
    }
    return;
  }
  // Per-question mic button (Milestone 7 Phase 18 real-phone finding: the
  // only mic was in the main chat input, which routes through the full
  // supervisor/LLM pipeline — answering a specific pending question is
  // more direct and more reliable from right where the question is shown)
  if (e.target.classList.contains("question-mic-btn")) {
    var qqid = e.target.getAttribute("data-question-id");
    if (qqid) startVoiceAnswerForQuestion(qqid, e.target);
    return;
  }
  // Custom answer send button
  if (e.target.classList.contains("question-send-btn")) {
    var qid = e.target.getAttribute("data-question-id");
    var input = document.querySelector('.question-answer-input[data-question-id="' + qid + '"]');
    var answer = input ? input.value.trim() : "";
    if (qid && answer && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "user_message", content: "/answer " + qid + " " + answer }));
      input.value = "";
      e.target.disabled = true;
      e.target.textContent = "Sent";
    }
    return;
  }
  // Attention call: Talk now
  if (e.target.classList.contains("attention-call-talk-btn")) {
    var talkAid = e.target.getAttribute("data-attention-request-id");
    if (talkAid) openVoiceSession(talkAid);
    return;
  }
  // Attention call: snooze / dismiss
  if (e.target.classList.contains("attention-call-snooze-btn") || e.target.classList.contains("attention-call-dismiss-btn")) {
    var snoozeAid = e.target.getAttribute("data-attention-request-id");
    var phrase = e.target.getAttribute("data-phrase");
    if (snoozeAid && phrase) {
      sendAttentionCommand(snoozeAid, phrase);
      e.target.disabled = true;
      e.target.textContent = "Sent";
    }
    return;
  }
  // Permission approve/deny buttons
  if (e.target.classList.contains("permission-approve-btn") || e.target.classList.contains("permission-deny-btn")) {
    var pid = e.target.getAttribute("data-permission-id");
    var approved = e.target.getAttribute("data-approved") === "true";
    if (pid && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "user_message", content: "/opencode-permit " + pid + " " + (approved ? "approve" : "deny") }));
      e.target.disabled = true;
      e.target.textContent = approved ? "Approved" : "Denied";
    }
    return;
  }
});

/* ========== Elapsed Timer ========== */

setInterval(function () {
  if (Object.keys(activeTasks).length > 0) {
    renderActiveTasks();
  }
}, 1000);

/* ========== Voice Input (Milestone 7 Phase 4) ========== */
/* Voice is just another input transport into the same WebSocket
   user_message pipeline as typed text (Milestone 7 principle 1) — see
   submitTranscript(), which calls the exact same sendUserMessage() the
   Send button uses. No separate voice conversation store, no raw audio
   persistence: only the recognized text ever leaves the browser. */

var SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
var recognition = null;
var voiceState = "idle"; // idle | listening | processing | error
var voiceCancelled = false;
var voiceSubmitInFlight = false;

function voiceSupported() {
  return !!SpeechRecognitionCtor;
}

function setVoiceState(state, message) {
  voiceState = state;
  var btn = document.getElementById("mic-btn");
  var status = document.getElementById("voice-status");
  if (btn) {
    btn.classList.remove("mic-idle", "mic-listening", "mic-processing", "mic-error");
    btn.classList.add("mic-" + state);
    btn.setAttribute("aria-label", state === "listening" ? "Stop voice input" : "Start voice input");
  }
  if (status) {
    if (message) {
      status.textContent = message;
      status.style.display = "block";
    } else {
      status.style.display = "none";
    }
  }
}

function startVoiceInput() {
  if (!voiceSupported()) {
    setVoiceState("error", "Voice input is not supported in this browser. Please type instead.");
    setTimeout(function () { if (voiceState === "error") setVoiceState("idle"); }, 4000);
    return;
  }
  if (voiceState === "listening") {
    cancelVoiceInput();
    return;
  }
  // Exactly one concurrent recognition session, ever.
  if (recognition) {
    try { recognition.abort(); } catch (e) {}
    recognition = null;
  }
  stopSpeaking(); // interrupt any TTS before listening (Phase 5)
  voiceCancelled = false;
  recognition = new SpeechRecognitionCtor();
  recognition.lang = "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = function () {
    setVoiceState("listening", "Listening…");
  };
  recognition.onresult = function (event) {
    if (voiceCancelled) return;
    var transcript = event.results[0][0].transcript;
    submitTranscript(transcript);
  };
  recognition.onerror = function (event) {
    if (voiceCancelled) return;
    var msg = (event.error === "not-allowed" || event.error === "permission-denied" || event.error === "service-not-allowed")
      ? "Microphone permission denied. You can still type."
      : "Voice input error (" + event.error + "). Please type instead.";
    setVoiceState("error", msg);
    setTimeout(function () { if (voiceState === "error") setVoiceState("idle"); }, 4000);
  };
  recognition.onend = function () {
    recognition = null;
    // Ended with no result and no error (silence/timeout): back to idle so
    // the mic button never gets stuck showing "listening".
    if (voiceState === "listening") setVoiceState("idle");
  };

  try {
    recognition.start();
  } catch (e) {
    setVoiceState("error", "Could not start voice input.");
    setTimeout(function () { if (voiceState === "error") setVoiceState("idle"); }, 4000);
  }
}

function cancelVoiceInput() {
  voiceCancelled = true;
  if (recognition) {
    try { recognition.abort(); } catch (e) {}
    recognition = null;
  }
  setVoiceState("idle");
}

function submitTranscript(transcript) {
  transcript = (transcript || "").trim();
  if (!transcript || voiceCancelled || voiceSubmitInFlight) {
    setVoiceState("idle");
    return;
  }
  voiceSubmitInFlight = true;
  setVoiceState("processing", "Sending: “" + transcript + "”");
  sendUserMessage(transcript);
  setTimeout(function () {
    voiceSubmitInFlight = false;
    setVoiceState("idle");
  }, 300);
}

/* Real-phone finding (Milestone 7 Phase 18, 2026-07-09): answering by
   voice from the main chat mic routes through the full supervisor/LLM
   pipeline, which only recognizes a narrow deterministic grammar
   ("Answer B") — a natural spoken answer like "approach a" didn't
   connect to the pending question at all. This per-question mic answers
   *that specific question* directly via the same /answer command the
   Send button next to it already uses, bypassing that ambiguity
   entirely. Deliberately a separate, self-contained function rather than
   a refactor of startVoiceInput() above — that flow is already covered
   by extensive tests and real-device validation; duplicating a small
   amount of logic here carries far less regression risk than reshaping
   it to serve two different callers. Still shares the same
   recognition/voiceState singleton, so "exactly one concurrent
   recognition session" continues to hold across both mic buttons. */
function startVoiceAnswerForQuestion(questionId, btnEl) {
  if (!voiceSupported()) {
    setTransientStatus("Voice input is not supported in this browser.");
    return;
  }
  if (voiceState === "listening") {
    cancelVoiceInput();
    return;
  }
  if (recognition) {
    try { recognition.abort(); } catch (e) {}
    recognition = null;
  }
  stopSpeaking();
  voiceCancelled = false;
  recognition = new SpeechRecognitionCtor();
  recognition.lang = "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  voiceState = "listening";
  btnEl.classList.remove("mic-idle", "mic-error");
  btnEl.classList.add("mic-listening");

  function reset() {
    recognition = null;
    btnEl.classList.remove("mic-listening");
    btnEl.classList.add("mic-idle");
    if (voiceState === "listening") voiceState = "idle";
  }

  recognition.onresult = function (event) {
    if (voiceCancelled) return;
    var transcript = (event.results[0][0].transcript || "").trim();
    reset();
    if (transcript && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "user_message", content: "/answer " + questionId + " " + transcript }));
    }
  };
  recognition.onerror = function (event) {
    reset();
    if (!voiceCancelled) setTransientStatus("Voice input error. Please type instead.");
  };
  recognition.onend = reset;

  try {
    recognition.start();
  } catch (e) {
    reset();
  }
}

/* ========== Text-to-Speech (Milestone 7 Phase 5) ========== */

var ttsSupported = "speechSynthesis" in window;
var speechEnabled = false;
var currentUtterance = null;
var speechQueue = [];

function loadSpeechPreference() {
  try {
    return window.localStorage.getItem("jarvis_speech_enabled") === "true";
  } catch (e) {
    return false;
  }
}

function storeSpeechPreference(enabled) {
  try {
    window.localStorage.setItem("jarvis_speech_enabled", enabled ? "true" : "false");
  } catch (e) {
    /* private browsing etc. — preference just won't persist */
  }
}

function setSpeechEnabled(enabled) {
  speechEnabled = !!enabled && ttsSupported;
  storeSpeechPreference(speechEnabled);
  var btn = document.getElementById("speech-toggle-btn");
  if (btn) {
    btn.setAttribute("aria-pressed", speechEnabled ? "true" : "false");
    btn.textContent = speechEnabled ? "🔊" : "🔈";
    btn.title = speechEnabled ? "Spoken responses on (tap to disable)" : "Spoken responses off (tap to enable)";
  }
  if (!speechEnabled) stopSpeaking();
}

function stopSpeaking() {
  speechQueue = [];
  if (ttsSupported) window.speechSynthesis.cancel();
  currentUtterance = null;
  var stopBtn = document.getElementById("speech-stop-btn");
  if (stopBtn) stopBtn.style.display = "none";
}

function truncateForSpeech(text, maxLen) {
  maxLen = maxLen || 240;
  if (text.length <= maxLen) return text;
  var cut = text.slice(0, maxLen);
  var lastSpace = cut.lastIndexOf(" ");
  if (lastSpace > 40) cut = cut.slice(0, lastSpace);
  return cut + "…";
}

function speak(text) {
  // Never speaks raw task stdout or every SSE progress event: the only
  // two call sites are the supervisor's direct conversational reply and
  // handleLiveNotification() (Phase 5/6/7) — both already concise and
  // already filtered server-side by AttentionPolicy before they ever
  // reach the browser as a "notification" event.
  if (!speechEnabled || !ttsSupported || !text) return;
  var utter = new SpeechSynthesisUtterance(truncateForSpeech(text));
  utter.onend = function () {
    currentUtterance = null;
    if (speechQueue.length === 0) {
      var stopBtn = document.getElementById("speech-stop-btn");
      if (stopBtn) stopBtn.style.display = "none";
    } else {
      var next = speechQueue.shift();
      currentUtterance = next;
      window.speechSynthesis.speak(next);
    }
  };
  utter.onerror = function () { currentUtterance = null; };

  if (window.speechSynthesis.speaking || currentUtterance) {
    // Never overlap utterances; bounded queue so a burst of events can't
    // talk for minutes after the user has moved on.
    if (speechQueue.length < 3) speechQueue.push(utter);
    return;
  }
  currentUtterance = utter;
  var stopBtn = document.getElementById("speech-stop-btn");
  if (stopBtn) stopBtn.style.display = "inline-block";
  window.speechSynthesis.speak(utter);
}

/* ========== Notifications (Milestone 7 Phase 6/7) ========== */

var renderedNotificationIds = {};

function handleLiveNotification(n) {
  renderNotificationInTimeline(n);
  // Live delivery only — pending_notifications (reconnect catch-up) never
  // triggers speech, so a reconnect/replay/restart cannot cause repeated
  // speech for the same underlying event (Phase 5/14).
  speak(spokenTextForNotification(n));
}

/* Real-phone finding (Milestone 7 Phase 18, 2026-07-09): speaking just the
   generic notification title/body ("Jarvis needs your answer") forces the
   user to look at the screen anyway to find out what's actually being
   asked, defeating much of the point of a spoken prompt. The push /
   lock-screen text must stay generic (Phase 15 - no sensitive detail on a
   lock screen), but the in-app spoken version can safely use the real
   question/permission content already visible on-screen once the user is
   in the authenticated app - so look it up from the same pendingQuestions/
   pendingPermissions state the UI itself renders from, and fall back to
   the generic text only if that lookup fails for any reason. */
function spokenTextForNotification(n) {
  if (n.notification_type === "QUESTION_REQUIRED" && n.task_id) {
    var q = findPendingByTaskId(pendingQuestions, n.task_id);
    if (q && q.question) {
      var optionsText = q.options && q.options.length ? " Options: " + q.options.join(", ") + "." : "";
      return truncateForSpeech(q.question) + optionsText;
    }
  }
  if (n.notification_type === "PERMISSION_REQUIRED" && n.task_id) {
    var p = findPendingByTaskId(pendingPermissions, n.task_id);
    if (p) {
      return "Permission needed: " + (p.action || "an action") + (p.path ? " on " + truncateForSpeech(p.path, 80) : "") + ".";
    }
  }
  return n.title + ". " + n.body;
}

function findPendingByTaskId(dict, taskId) {
  for (var key in dict) {
    if (dict[key].task_id === taskId) return dict[key];
  }
  return null;
}

function renderNotificationInTimeline(n) {
  if (renderedNotificationIds[n.notification_id]) return; // dedup across live + reconnect resend
  renderedNotificationIds[n.notification_id] = true;
  var tl = document.getElementById("timeline");
  var el = document.createElement("div");
  el.className = "event event-notification event-priority-" + (n.priority || "NORMAL").toLowerCase();
  el.setAttribute("data-notification-id", n.notification_id);
  el.innerHTML = "<strong>" + escapeHtml(n.title) + "</strong><p>" + escapeHtml(n.body) + "</p>";
  tl.appendChild(el);
  tl.scrollTop = tl.scrollHeight;
}

/* ========== Push Notification Opt-in (Milestone 7 Phase 8) ========== */

var swRegistration = null;

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function urlBase64ToUint8Array(base64String) {
  var padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  var rawData = window.atob(base64);
  var outputArray = new Uint8Array(rawData.length);
  for (var i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return Promise.resolve(null);
  return navigator.serviceWorker
    .register("/sw.js")
    .then(function (reg) {
      swRegistration = reg;
      return reg;
    })
    .catch(function () {
      return null;
    });
}

/* Real-phone finding (Milestone 7 Phase 18, 2026-07-09): the bell icon
   always started in the "off" visual state on every page load, even when
   a real push subscription already existed from an earlier session —
   there was no code path that checked actual subscription state, which
   is confusing (looks off even when it's genuinely on) and was part of
   why a real subscription's existence went unnoticed. Reflect the real
   state on load instead of assuming "off". */
function reflectExistingPushSubscription() {
  if (!pushSupported()) return;
  navigator.serviceWorker
    .getRegistration()
    .then(function (reg) {
      if (!reg) return null;
      return reg.pushManager.getSubscription();
    })
    .then(function (sub) {
      var btn = document.getElementById("notify-opt-in-btn");
      if (!btn) return;
      var isOn = !!sub && Notification.permission === "granted";
      btn.setAttribute("aria-pressed", isOn ? "true" : "false");
      btn.title = isOn ? "Push notifications enabled (tap to re-check)" : "Enable push notifications";
    })
    .catch(function () {
      /* no existing registration/subscription — leave the default "off" state */
    });
}

function setTransientStatus(msg) {
  var el = document.getElementById("voice-status");
  if (!el) return;
  el.textContent = msg;
  el.style.display = "block";
  setTimeout(function () { el.style.display = "none"; }, 4000);
}

function enablePushNotifications() {
  if (!pushSupported()) {
    setTransientStatus("Push notifications are not supported in this browser.");
    return;
  }
  Notification.requestPermission().then(function (permission) {
    if (permission !== "granted") {
      setTransientStatus("Notification permission was not granted.");
      return;
    }
    registerServiceWorker().then(function (reg) {
      if (!reg) return;
      fetch("/api/vapid-public-key")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.push_configured || !data.vapid_public_key) {
            setTransientStatus("Push isn't configured on the server yet — in-app notifications still work.");
            return;
          }
          return reg.pushManager
            .subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(data.vapid_public_key) })
            .then(function (sub) {
              var json = sub.toJSON();
              return fetch("/api/push/subscribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys, conversation_id: conversationId }),
              });
            })
            .then(function () {
              setTransientStatus("Push notifications enabled.");
              var btn = document.getElementById("notify-opt-in-btn");
              if (btn) btn.setAttribute("aria-pressed", "true");
            });
        })
        .catch(function () {
          setTransientStatus("Could not enable push notifications.");
        });
    });
  });
}

/* ========== Completion-notification policy toggle ========== */

function initCompletionToggle() {
  fetch("/api/settings")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var btn = document.getElementById("notify-completion-toggle-btn");
      if (!btn) return;
      btn.setAttribute("aria-pressed", data.notify_on_completion ? "true" : "false");
      btn.title = data.notify_on_completion ? "Notify me when tasks complete (on)" : "Notify me when tasks complete (off)";
    })
    .catch(function () {});
}

/* ========== Deep Linking (Milestone 7 Phase 9) ========== */

function parseDeepLinkParams() {
  var params = new URLSearchParams(window.location.search);
  return {
    conversation: params.get("conversation"),
    task: params.get("task"),
    question: params.get("question"),
    permission: params.get("permission"),
    notification: params.get("notification"),
    attention: params.get("attention"),
  };
}

function isValidConversationIdFormat(id) {
  return /^conv_[0-9a-f]{12}$/.test(id);
}

function cssEscapeAttr(s) {
  return window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&");
}

function applyDeepLink(params) {
  if (params.notification) {
    fetch("/api/notification/" + encodeURIComponent(params.notification))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        var resolved = {
          conversation: data.conversation_id || params.conversation,
          task: data.task_id || params.task,
          question: data.source_type === "opencode_question" ? data.source_id : params.question,
          permission: data.source_type === "opencode_permission" ? data.source_id : params.permission,
        };
        highlightDeepLinkTargets(resolved);
      });
    return;
  }
  if (params.attention) {
    fetch("/api/attention/" + encodeURIComponent(params.attention))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        var resolved = {
          conversation: data.conversation_id || params.conversation,
          task: data.task_id || params.task,
          attention: data.attention_request_id,
        };
        highlightDeepLinkTargets(resolved);
        if (data.status && ["resolved", "cancelled", "expired"].indexOf(data.status) !== -1) {
          setTransientStatus("That item was already " + data.status + ".");
        }
      });
    return;
  }
  highlightDeepLinkTargets(params);
}

function highlightDeepLinkTargets(params) {
  var targetId = params.attention || params.question || params.permission || params.task;
  if (!targetId) return;
  var attempts = 0;
  var timer = setInterval(function () {
    attempts++;
    var el =
      document.querySelector('[data-attention-request-id="' + cssEscapeAttr(targetId) + '"]') ||
      document.querySelector('[data-question-id="' + cssEscapeAttr(targetId) + '"]') ||
      document.querySelector('[data-permission-id="' + cssEscapeAttr(targetId) + '"]') ||
      document.querySelector('[data-task-id="' + cssEscapeAttr(targetId) + '"]');
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("deep-link-highlight");
      clearInterval(timer);
    } else if (attempts > 20) {
      clearInterval(timer);
      // Not currently rendered — it may already be resolved. Confirm via
      // REST instead of silently doing nothing (Phase 9 requirement 4: no
      // stale answer controls for an already-resolved item).
      if (params.question || params.permission) {
        fetch("/api/question/" + encodeURIComponent(targetId))
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (q) {
            if (q && q.status !== "pending") setTransientStatus("That item was already " + q.status + ".");
          });
      }
    }
  }, 250);
}

/* ========== Init ========== */

document.addEventListener("DOMContentLoaded", function () {
  document.getElementById("send-btn").addEventListener("click", sendMessage);
  document.getElementById("message-input").addEventListener("keypress", function (e) {
    if (e.key === "Enter") sendMessage();
  });

  var micBtn = document.getElementById("mic-btn");
  if (!voiceSupported()) {
    micBtn.disabled = true;
    micBtn.title = "Voice input is not supported in this browser.";
  } else {
    micBtn.addEventListener("click", function () {
      if (voiceState === "listening") cancelVoiceInput();
      else startVoiceInput();
    });
  }

  var speechBtn = document.getElementById("speech-toggle-btn");
  if (!ttsSupported) {
    speechBtn.disabled = true;
    speechBtn.title = "Spoken responses are not supported in this browser.";
  } else {
    speechBtn.addEventListener("click", function () { setSpeechEnabled(!speechEnabled); });
    setSpeechEnabled(loadSpeechPreference());
  }

  document.getElementById("speech-stop-btn").addEventListener("click", stopSpeaking);
  document.getElementById("voice-session-close-btn").addEventListener("click", closeVoiceSession);
  document.getElementById("notify-opt-in-btn").addEventListener("click", enablePushNotifications);
  document.getElementById("notify-completion-toggle-btn").addEventListener("click", function () {
    var btn = this;
    var currentlyOn = btn.getAttribute("aria-pressed") === "true";
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notify_on_completion: !currentlyOn }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        btn.setAttribute("aria-pressed", data.notify_on_completion ? "true" : "false");
        btn.title = data.notify_on_completion ? "Notify me when tasks complete (on)" : "Notify me when tasks complete (off)";
      });
  });
  initCompletionToggle();
  reflectExistingPushSubscription();

  var deepLinkParams = parseDeepLinkParams();
  if (deepLinkParams.conversation && isValidConversationIdFormat(deepLinkParams.conversation)) {
    storeConversationId(deepLinkParams.conversation);
  }

  connect();

  if (deepLinkParams.notification || deepLinkParams.question || deepLinkParams.permission || deepLinkParams.task || deepLinkParams.attention) {
    setTimeout(function () { applyDeepLink(deepLinkParams); }, 900);
  }
});