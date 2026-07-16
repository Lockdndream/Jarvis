# Jarvis Architecture

**Status**: Living document, authoritative as of Milestone 9B.0 (2026-07-12).
**Audience**: Engineers working on or extending Jarvis.
**Scope**: This document describes *why* Jarvis is designed the way it is,
and the invariants future work must preserve. It is not a changelog
(`SESSION.md`) and not a setup guide (`README.md`). It should change slowly
— implementation details move faster than architecture, and this document
should only change when the architecture itself changes.

---

## 1. Vision

Jarvis is a personal AI assistant built around one non-negotiable idea:
**the laptop is the brain, and every other device is a surface**.

Concretely:

- **The laptop runs the only reasoning process.** The `Supervisor`, its LLM
  tool-call loop, all state machines, all persistence, and all worker
  orchestration live on one machine, in one process family, backed by one
  SQLite database. There is exactly one place where "what should happen
  next" gets decided.
- **Edge devices are interaction surfaces, not brains.** A phone, a
  browser tab, or a future ESP32 presence device exists to let a human
  *reach* Jarvis and *be reached by* Jarvis — voice in, voice out,
  notifications, a widget, a button. None of them run their own copy of
  Jarvis's reasoning, and none of them are allowed to accumulate reasoning
  logic over time by accretion. A client that starts making its own
  decisions about what a user's answer means, or when to re-contact them,
  has stopped being a client and started being a second, uncoordinated
  brain — this is the single most important failure mode this
  architecture is designed to prevent.
- **Deterministic infrastructure carries the weight; the LLM is used
  narrowly.** State transitions, scheduling, interruption decisions,
  deduplication, and delivery tracking are all plain deterministic code.
  The LLM's job is bounded to conversational reasoning and tool selection
  inside the `Supervisor` — it is not trusted to decide whether a
  notification should exist, whether a task is really done, or whether an
  answer arrived twice.
- **Local-first.** Jarvis's default deployment is a laptop and a phone on
  the same LAN, no cloud relay, no third-party server holding
  conversation state. Anything that would require a public relay or
  always-on cloud dependency is treated as a major architectural decision
  requiring explicit justification, not a default.
- **Modular, not monolithic.** Attention, voice, tasks, notifications,
  and OpenCode orchestration are separate modules with explicit
  boundaries (see §4, §5), not one large object doing everything. This is
  what has made it possible to add a native Android companion (M9A/M9B.0)
  and, eventually, an ESP32 presence device (§11) without rewriting the
  reasoning layer.
- **Extensible by addition, not by rewrite.** New workers, new contact
  channels, new transports, and new devices are expected to plug into
  existing seams (§15) rather than requiring changes to the Supervisor or
  the state machines themselves.

The long-term picture is one laptop-resident Jarvis, reachable and
reachable-from through multiple thin surfaces — a browser PWA today, a
native Android companion for capabilities a browser categorically cannot
provide, and eventually a dedicated presence device — all speaking to the
same brain, none of them able to make a decision on their own.

---

## 2. Design Principles

These are the rules that have held up under real implementation pressure
across nine milestones. Where a principle was learned the hard way, the
originating milestone is noted — not as history, but because the "why"
often *is* the incident.

**Single source of truth.** Every piece of state that matters —
conversation history, task status, attention state, voice session state
— has exactly one authoritative table and one module that is allowed to
write to it. Other modules read it or request a transition through the
owning module's own guarded API; they never mutate it directly. See §5
for the full ownership table.

**The Supervisor owns reasoning; workers execute.** The conversational
`Supervisor` is a router with a tool-call loop, not an executor. It never
touches a file, never runs a shell command, never opens a raw socket to
do work — it calls into `TaskManager` or `OpenCodeSupervisor`, which do
the actual work and report back through verified state. This is what
keeps "who can affect the real world" to a small, auditable surface.

**Clients never become brains.** Stated in §1, worth restating as a
design rule: no client-side code — browser, Android, or future ESP32 — is
permitted to duplicate deferral parsing, answer matching, or
interruption-scoring logic. `VoiceSessionManager` is deliberately a thin
pass-through to `Supervisor.process_message()`, not a reimplementation
(Milestone 8).

**Explicit state transitions, not implicit ones.** Every stateful entity
in Jarvis (`AttentionRequest`, `VoiceSession`, OpenCode tasks) has a
declared set of legal transitions and a single guarded function that
performs them. An entity does not become `resolved` because a caller
believes it should be — it becomes `resolved` because a specific,
allowed, atomic transition was executed. This was tightened repeatedly
after real concurrency bugs (Milestone 8's conditional-UPDATE guards,
Milestone 8's real thread-race tests).

**Deterministic policies where possible.** `AttentionPolicy` and
`InterruptionPolicy` — the two components that decide whether and how
urgently to interrupt a user — are pure functions of known state
(urgency, quiet hours, prior contact count, connection state). Neither
uses an LLM. This is a deliberate, repeated decision (Milestone 7,
reaffirmed Milestone 8): interruption is a safety-adjacent decision, and
an LLM's occasional creative reinterpretation of "should I bother this
person right now" is not an acceptable failure mode.

**Idempotency over exactly-once delivery.** Notifications dedupe on a
`dedup_key` UNIQUE constraint, not caller discipline (Milestone 7).
`AttentionRequest` creation is idempotent per source (Milestone 8). SSE
reconnects can replay events; the system is built to tolerate replay
rather than assume it never happens.

**Honest state reporting over optimistic state reporting.** A task is
never marked `completed` because time passed with no errors — it is
marked `completed` because a specific, native, verifiable event said so
(Milestone 6's entire premise). If Jarvis restarts mid-task, the task
becomes `degraded` (unknown), never silently `failed` or silently
`completed` — both would be a lie about something no one actually
observed.

**Evidence over inference.** This is the single most repeated lesson
across this project's incident history: HTTP 200, elapsed silence, or LLM
prose are never treated as proof that something happened. A completed
OpenCode task requires session-native evidence (`session.idle`,
`session.error`); a wake-word spike's screen-off survival claim requires
independent client-*and*-server timestamped telemetry (Milestone 9B.0),
not one side's word for it. A delegated code review's findings are
independently spot-checked against authoritative sources before being
accepted, not trusted because they read plausibly (Milestone 9B.0's D4).

**Security and cost boundaries before convenience.** Storage isolation
(never touch OpenCode Desktop's shared database), credential isolation
(never let an ambient environment variable reach a delegated subprocess),
and explicit model pinning (never let a delegated agent silently choose a
paid model) were all added *after* a real incident demonstrated the
convenient default was unsafe (Milestone 6.1, Milestone 9B.0). The
standing rule going forward is to assume the convenient default is unsafe
until proven otherwise, not the reverse.

---

## 3. High-Level Architecture

```
                                   ┌───────────────────────────┐
                                   │      Laptop (the brain)    │
                                   │                             │
                                   │   ┌─────────────────────┐   │
                                   │   │      Supervisor       │   │
                                   │   │  (reasoning, routing) │   │
                                   │   └──────────┬───────────┘   │
                                   │              │               │
                          ┌────────┼──────────────┼───────────────┼────────┐
                          │        │              │               │        │
                          ▼        ▼              ▼               ▼        ▼
                   TaskManager  AttentionManager  VoiceSessionMgr  OpenCode  Notifications
                   (local proc) (state machine)   (session state) Supervisor (delivery)
                          │        │              │               │        │
                          └────────┴──────┬───────┴───────┬───────┴────────┘
                                          │               │
                                    SQLite (jarvis.db)   Isolated OpenCode
                                    single source of      runtime (M6.1)
                                    truth for all state
                                   └───────────────────────────┘
                                              ▲
                                              │  WebSocket / REST / Push
                        ┌─────────────────────┼─────────────────────┐
                        │                     │                     │
                        ▼                     ▼                     ▼
                 Browser PWA          Android Companion       ESP Presence
                 (implemented,        (M9B.0 spike stage;      Device
                  rich client)         production not started) (future, §11)
```

Every arrow into "Laptop" is a *client relationship*: the client sends
input (typed text, voice transcript, a button press) and receives state
(timeline events, attention prompts, TTS output). No arrow represents a
client making an autonomous decision about Jarvis's own state — that
authority stays inside the dashed box.

---

## 4. Core Components

For each component: purpose, inputs, outputs, ownership, dependencies,
state.

### Supervisor (`app/supervisor/supervisor.py`)

- **Purpose**: conversational entry point for free-text input. Routes
  between fast deterministic paths (status questions, defer commands) and
  an LLM tool-call loop for everything else.
- **Inputs**: free-text message, `conversation_id`, optional
  `bound_attention_request_id` (when the message came from a bound voice
  session).
- **Outputs**: response text, tool-call side effects (via `ToolRegistry`),
  updated `conversation_id`.
- **Owns**: nothing persistent. It is a router, not a state holder.
- **Depends on**: `ToolRegistry` (`tools.py`), `LLMProvider` (`llm.py`),
  `context.py`, `projects.py`; ultimately delegates to `TaskManager` and
  `OpenCodeSupervisor` for any real work.
- **Must never**: execute file/shell work directly, duplicate any other
  module's state-transition logic, or create a `Notification` or
  `AttentionRequest` directly.

### AttentionManager (`app/attention_manager.py`)

- **Purpose**: the single state machine for "something needs the user's
  attention" — a question, a permission, a failure, or a proactive
  contact need.
- **Inputs**: `get_or_create()` calls from `worker_events.py`
  (question/permission/failure detected) or the Supervisor (deferral).
- **Outputs**: `AttentionRequest` rows, guarded transitions, a real-time
  broadcast hook consumed by the WebSocket layer.
- **Owns**: the `attention_requests` table exclusively. See §5.
- **Depends on**: `database.py`, `interruption_policy.py` (to decide
  whether/how to contact), `contact_channels.py` (to actually attempt
  contact).
- **Invariant**: creation is idempotent by `dedup_key` — the same
  underlying source (a specific question, a specific worker failure)
  never produces two `AttentionRequest` rows.

### VoiceSessionManager (`app/voice_session_manager.py`)

- **Purpose**: tracks the lifecycle of one voice conversation turn-cycle
  — opening, listening, processing, speaking, waiting for the next turn,
  deferred, closing.
- **Inputs**: `open_session()`, `handle_transcript()`, `close_session()`
  from the WebSocket layer.
- **Outputs**: `VoiceSession` state, a greeting (if bound to an
  `AttentionRequest`), a thin pass-through call into
  `Supervisor.process_message()`.
- **Owns**: the `voice_sessions` table exclusively.
- **Depends on**: `Supervisor` (for the actual conversational turn),
  `attention_manager` (to re-check bound-source status after a turn).
- **Invariant** (Milestone 8, restated as a hard rule after being
  violated once): must never contain answer-matching, defer-parsing, or
  any other conversational logic itself. It is lifecycle and correlation
  only.

### InterruptionPolicy (`app/interruption_policy.py`)

- **Purpose**: deterministic decision function — given an
  `AttentionRequest`'s type, urgency, prior contact count, connection
  state, and quiet-hours configuration, should Jarvis contact the user
  now, and through which channel?
- **Inputs**: `AttentionRequest` row + connection state.
- **Outputs**: a contact decision (channel + timing), never a side
  effect itself.
- **Owns**: no persistent state — a pure function over its inputs.
- **Depends on**: nothing but its own inputs and configuration
  (`JARVIS_ATTENTION_RETRY_MINUTES`, read once at import time — a
  deployment-time knob, not a runtime one).
- **Invariant**: no LLM call, ever, in this decision path.

### AttentionScheduler (`app/attention_scheduler.py`)

- **Purpose**: DB-driven ticking clock that surfaces `AttentionRequest`
  rows whose `deferred_until`/`next_contact_at` has arrived.
- **Inputs**: none external — polls `attention_requests` on a fixed
  interval (15s tick).
- **Outputs**: triggers `InterruptionPolicy`/contact-attempt flow for due
  requests.
- **Owns**: nothing — reads `attention_requests`, does not own the table.
- **Invariant**: entirely server-side and DB-driven. No part of "when is
  this due" logic may live in client-side JavaScript or any client at
  all — a deferred request must survive browser closure, phone reboot,
  and a full Jarvis restart identically.

### TaskManager (`app/task_manager.py`)

- **Purpose**: lifecycle of local subprocess-based work — start, stream
  stdout/stderr, detect the `JARVIS_QUESTION:` protocol, deliver answers
  via stdin, cancel.
- **Inputs**: task-start requests (project/command), cancel requests,
  answers.
- **Outputs**: `tasks`/`questions` rows, broadcast events.
- **Owns**: the `tasks` and `questions` tables (for local, non-OpenCode
  tasks).
- **Depends on**: OS process APIs directly; no delegation beneath it.

### OpenCode integration (`app/integrations/opencode_*.py`)

- **Purpose**: the coding-agent worker. `OpenCodeServerManager` owns the
  subprocess lifecycle of `opencode serve`; `OpenCodeAdapter` is the thin
  REST/SSE client; `OpenCodeSupervisor` is the orchestration layer that
  maps Jarvis tasks to OpenCode sessions and interprets SSE evidence into
  verified state transitions.
- **Inputs**: `start_session()`, `send_instruction()`, question/permission
  answers.
- **Outputs**: `opencode_tasks` rows, verified state transitions (never
  inferred from silence — see §14), telemetry (SSE event log).
- **Owns**: the `opencode_tasks` table; the isolated OpenCode runtime
  directory (`%LOCALAPPDATA%\JarvisOpenCodeRuntime`, never Desktop's
  shared storage — see §12).
- **Invariant**: every delegated prompt explicitly pins provider+model
  (never lets OpenCode fall back to its own default selection); the
  owned-spawn subprocess environment is an explicit OS-essential
  allowlist, never a full inherited environment (Milestone 9B.0 — see
  §12 for the incident this closed).

### NotificationManager (`app/notifications.py`, `app/push.py`)

- **Purpose**: the single entry point for creating a `Notification` and
  the delivery mechanism (in-app broadcast, Web Push).
- **Inputs**: calls from `worker_events.py`/`attention_manager.py` after
  a verified state change — never from LLM prose.
- **Outputs**: `notifications` rows, push delivery attempts.
- **Owns**: the `notifications` table, exclusively through `notify()`.
- **Invariant**: `notify()` is the only creation path; deduplication
  depends on the `dedup_key` UNIQUE constraint (`INSERT OR IGNORE` +
  re-select), not caller discipline — do not "optimize" this into a
  plain `INSERT`.
- **Known looseness, disclosed rather than papered over**: `push_subscriptions`
  is written directly from `app/main.py`'s subscribe endpoint
  (`save_push_subscription()`), not through this module — `app/push.py`
  only *reads* it (`get_push_subscriptions()`) to send. This is a real
  gap relative to the "single owning module" principle (§2), not an
  intentional design choice; a future pass should route subscription
  writes through a dedicated function in this module instead of a direct
  `database.py` call from the route handler.

### Contact channels (`app/contact_channels.py`)

- **Purpose**: abstraction over *how* Jarvis actually reaches the user —
  currently `PushChannel` (real) and reserved names `NATIVE_ANDROID` /
  `PHONE_CALL` / `SMS` (raise `ValueError` by design — not implemented,
  not stubbed).
- **Outputs**: a result dict (status, error code) that `attention_manager.py`
  persists into `contact_attempts` — the channel itself never writes to
  the database directly.
- **Invariant**: a channel must never claim more delivery certainty than
  the platform provides — e.g. `PushChannel` reports
  `"PUSH_ACCEPTED_BY_PUSH_SERVICE (delivery to device not confirmed)"`,
  never `"delivered"`, because the platform genuinely cannot confirm
  that.

### Conversation management (`app/database.py` conversation functions,
`conversation_id` handshake in `app/main.py`)

- **Purpose**: stable identity for a WebSocket client's conversation
  across turns and reconnects.
- **Owns**: the `conversations` table (message history).
- **Invariant**: `conversation_id` is minted or validated server-side; a
  malformed or hostile client-supplied ID is rejected, not trusted.

### Persistence / Database (`app/database.py`)

- **Purpose**: the single SQLite database (`jarvis.db`) backing every
  table in §13. All CRUD, schema migration (`_ensure_column`), and
  startup reconciliation logic lives here.
- **Invariant**: this is the actual single source of truth referenced
  throughout this document — every other module's "ownership" of a table
  means "the only module permitted to call the corresponding
  `database.py` write functions," not a separate copy of the data.

### Workers (`app/workers/mock_worker.py`, OpenCode)

- **Purpose**: the actual units of work Jarvis supervises. A worker
  speaks one of two protocols: the local `JARVIS_QUESTION:` stdout/stdin
  protocol, or OpenCode's native REST/SSE session protocol.
- **Extensibility seam**: see §15 — a new worker type means a new
  protocol adapter, not changes to `Supervisor` or the state machines.

### Projects (`app/supervisor/projects.py`, `projects.json`)

- **Purpose**: named, path-scoped aliases a Supervisor tool call can
  target, so an LLM never receives or needs to reason about raw
  filesystem paths.
- **Invariant** (learned the hard way — Milestone 9B.0): alias scoping is
  *instruction-level*, not filesystem-enforced. A "read-only" alias is
  only as safe as the discipline of the agent honoring it and the
  narrowness of the directory it points at — write access must be
  assumed possible and the blast radius kept small (a fresh, disposable
  directory with nothing sensitive in it), never assumed impossible.

### LLM provider (`app/supervisor/llm.py`)

- **Purpose**: minimal OpenAI-compatible chat-completion client for the
  Supervisor's own reasoning, plus the free-only cost guard
  (`validate_free_only_model()`) shared with the OpenCode delegation path
  (`app/integrations/opencode_adapter.py`'s `validate_opencode_model()`
  wraps, never replaces, this same function).
- **Invariant**: `JARVIS_LLM_FREE_ONLY` gates the Supervisor's own model
  choice; do not bypass or weaken this check. A separate, narrower,
  explicitly-named paid-model allowlist exists for delegated OpenCode
  worker sessions only (`JARVIS_OPENCODE_ALLOW_PAID` — see §12), and it
  does not touch this function's behavior for the Supervisor itself.

---

## 5. State Ownership

| Entity | Owning module | Owning table | Written by anyone else? |
|---|---|---|---|
| Conversation history | `database.py` conversation functions | `conversations` | No |
| `AttentionRequest` | `attention_manager.py` | `attention_requests` | No — not from UI rendering, polling, notification delivery, or SSE replay |
| `VoiceSession` | `voice_session_manager.py` | `voice_sessions` | No |
| `Notification` | `notifications.py::notify()` | `notifications` | No — this is the *only* creation path, enforced as a rule, not just a convention |
| Push subscription | `app/main.py`'s subscribe endpoint (writes); `push.py` (reads only) | `push_subscriptions` | Yes — a real, disclosed looseness relative to the single-owning-module principle; see §4 NotificationManager |
| Local Task | `task_manager.py` | `tasks`, `questions` | No |
| OpenCode Task/Session | `opencode_supervisor.py` | `opencode_tasks` | No — state transitions require native SSE evidence, never inferred |
| Question/Permission | `task_manager.py` (local) / `opencode_supervisor.py` (OpenCode) | `questions` | No |
| `ContactAttempt` | `attention_manager.py` (persists the result; `contact_channels.py` provides the channel abstraction but never writes the table itself) | `contact_attempts` | No |
| Worker process state | `task_manager.py` (local) / `opencode_server.py` (OpenCode subprocess) | in-memory + `tasks`/`opencode_tasks` | No |
| Android presence (future) | Not yet built — see §9 | N/A | N/A |

**Four concepts that must never be conflated** (Milestone 8, stated as a
hard rule after a near-miss): `AttentionRequest`, `ContactAttempt`,
`Notification`, and `VoiceSession` are four distinct things.
A `Notification` is a delivery artifact of a `ContactAttempt`, not the
underlying attention state. An `AttentionRequest` must never be marked
`resolved` before the underlying native delivery has already succeeded —
`resolve()` is always called *after* the native action returns without
raising, never before or optimistically.

---

## 6. Communication Architecture

| Channel | Direction | Used for | Status |
|---|---|---|---|
| WebSocket (`/ws`) | bidirectional | conversation, timeline, attention broadcasts, voice session turns | Implemented, primary channel |
| REST (`Executor` routes) | client → server | slash commands (`/status`, `/cancel`, `/opencode-*`, etc.) | Implemented |
| SSE (from OpenCode) | OpenCode → Jarvis | session lifecycle evidence (`session.idle`, `session.error`, `session.diff`, question/permission triggers) | Implemented, internal only (not exposed to clients directly) |
| Web Push | server → client | background notification when the WebSocket is not open | Implemented; real background delivery on a real device is unreliable (platform limitation, not a Jarvis defect — see §14, Known Limitation #35) |
| Future Android transport | bidirectional | persistent presence + the same `/ws` protocol, over the same real Jarvis endpoint | Spiked (M9B.0) — the disposable Android spike speaks the real, existing `/ws` protocol; a production companion is not yet built |
| Future ESP transport | bidirectional | presence, wake word, button | Not designed (§11) |
| Internal events (`app/database.py` events table) | server-internal | audit/timeline log | Implemented |
| Worker protocol (`JARVIS_QUESTION:`) | local subprocess ↔ Jarvis | question/answer over stdout/stdin | Implemented |
| Question/Permission protocol | OpenCode ↔ Jarvis | structured question/permission objects over REST/SSE | Implemented |

**Decision on record** (do not reverse without strong evidence — see
§16 ADR-004): OpenCode integration is REST + SSE, not terminal scraping,
not PTY control. Scored 47/50 against four alternatives; the margin was
not close.

The WebSocket message protocol itself (`conversation_init`,
`user_message`, `voice_session_open`, `voice_session_transcript`,
`voice_session_close`, plus server-pushed `history`/`pending_*`/
`opencode_status`) is treated as a stable contract. Milestone 9B.0's
Android spike deliberately speaks this exact protocol rather than forking
or extending it — a new client is expected to conform to the existing
protocol, not the other way around, unless a real, demonstrated
deficiency in the protocol itself is found (not merely a client-side
implementation gap).

---

## 7. Voice Architecture

```
   ┌───────────────────────────────────────────────────────────┐
   │                    VoiceSession lifecycle                  │
   │                                                             │
   │   idle → opening → listening ⇄ processing → speaking       │
   │                        │            │                       │
   │                        ▼            ▼                       │
   │                     waiting ──► deferred                    │
   │                        │                                    │
   │                        ▼                                    │
   │                     closing → closed          [any] → failed│
   └───────────────────────────────────────────────────────────┘
```

- **Speech recognition and TTS today**: the browser's own
  `SpeechRecognition`/`speechSynthesis` APIs, same code path as typed
  text — voice input is never given its own conversational logic (§2).
  Real-device-validated (Milestone 7.1); browser STT accuracy is a real,
  disclosed, unfixable-without-a-paid-API limitation (§14).
- **Barge-in**: the existing overlap/stale-source guard applies uniformly
  to voice, whether the session is bound to an `AttentionRequest` or not.
- **Multi-turn correctness**: `handle_transcript()` must complete the
  `WAITING → LISTENING` transition after every turn — this was silently
  broken from Milestone 8 through Milestone 9A (every second turn in any
  session was rejected) until found and fixed in Milestone 9A. It is
  called out here because it is exactly the kind of state-machine
  completeness bug this document's emphasis on explicit transitions (§2)
  exists to prevent.
- **Future wake word** (gated on Milestone 9B.0's D1/D5 — see §9, §11):
  a wake word is a *local, offline* trigger that opens a voice session;
  it never itself interprets what is said. `microWakeWord` is the
  current leading candidate (Apache-2.0, a real production Android
  precedent, a pretrained "Hey Jarvis" model already available) — see
  Milestone 9B.0 for the full evidence.
- **Future audio routing / Bluetooth / ESP** (`FUTURE DESIGN`): not
  designed. The architectural constraint that will apply when they are
  designed: audio focus, ducking, and routing decisions are device-local
  concerns that affect *how* a voice session's audio reaches the user,
  never *what* the Supervisor decides to say or do.
- **VoiceSession ownership lease** (Milestone 9B.4, closing TD-002/ADR-007's
  disclosed gap): `attention_requests.active_voice_session_id`, an atomic
  set-if-null claim (`db.try_claim_voice_session_lease()`), prevents two
  clients (the PWA and the Android companion, now that both are
  voice-capable) from each independently opening an uncoordinated
  `VoiceSession` bound to the same `AttentionRequest`. A second client's
  `open_session()` call raises `VoiceSessionError` instead of silently
  proceeding. The lease is released on `close_session()`/`fail_session()`,
  and — since nothing previously cleaned up a session left open by an
  abruptly disconnected WebSocket — `app/main.py`'s connection handler now
  closes any still-open session for that connection in a `finally` block,
  so a lease can never be held forever by a connection that no longer
  exists.
- **Android speech input** (Milestone 9B.4): `SpeechInputController`
  (`android/.../voice/SpeechInputController.kt`) wraps
  `android.speech.SpeechRecognizer` for foreground, user-initiated (tap-to-
  talk) capture only — one tap, one recognition attempt, the recognizer is
  destroyed immediately on result/error/cancel. No wake word, no
  continuous/background listening; this is a deliberately narrower
  scope than the eventual wake-word milestone (9B.5), explicitly confirmed
  with the project owner before implementation.

**Why the phone never owns reasoning** (restating §1/§2 in voice-specific
terms): a phone-side wake word, once it fires, does exactly one thing —
open (or resume) a `VoiceSession` against the laptop. It does not
transcribe-and-decide locally, it does not defer-parse locally, and it
does not carry any memory of the conversation itself. The laptop remains
the only place where "what was said" becomes "what should happen."

---

## 8. Attention Architecture

```
Worker event (question/permission/failure)
          │
          ▼
  worker_events.create_attention()
          │
          ▼
  AttentionManager.get_or_create()  ── idempotent by dedup_key
          │
          ▼
     STATUS_PENDING ──────────────► STATUS_CONTACTING
          │                              │
          ▼                              ▼
   STATUS_DEFERRED               InterruptionPolicy.decide()
          │                              │
          │                              ▼
          │                      ContactChannel.attempt_contact()
          │                              │
          │                              ▼
          │                      ContactAttempt row + Notification
          │                              │
   AttentionScheduler                    ▼
   (DB tick, due-check)          STATUS_RESOLVING ──► STATUS_RESOLVED
          │                       (only after the native action
          └──────────────────────► actually succeeds — never before)
```

- **`AttentionRequest`**: the durable record of "something needs the
  user." States: `pending`, `contacting`, `deferred`, `resolving`,
  `resolved`, `cancelled`, `expired` (see §5, §13).
- **`ContactAttempt`**: one record per actual attempt to reach the user
  through a specific channel — distinct from the `AttentionRequest`
  itself, since one request may need several attempts across time.
- **`InterruptionPolicy`**: deterministic decision of whether/how to
  contact (§4).
- **`AttentionScheduler`**: DB-driven due-check (§4) — never a client
  timer.
- **Voice contact**: a bound `VoiceSession` correlated to the
  `AttentionRequest` via `attention_request_id`, proactively stating
  context before listening (a real, phone-found gap, fixed in Milestone
  8.1).
- **Push**: best-effort, platform-limited (§14); never claims more
  certainty than confirmed.
- **Widget** (future): a home-screen surface that reads current attention
  state and offers Talk/Respond/Reconnect actions — not yet designed
  beyond the M9A capability matrix.
- **Future channels**: `NATIVE_ANDROID`/`PHONE_CALL`/`SMS` are reserved
  names in `contact_channels.py`, deliberately unimplemented (raising
  `ValueError`), not stubbed.

---

## 9. Android Companion

**Status as of this document**: a disposable technical-risk spike exists
(`spikes/android-presence/`, Milestone 9B.0, retained as historical
reference only). The production companion (`android/`, `com.jarvis.companion`)
now has: pairing, transport/reconnect, presence service, telemetry, config
storage, notification channels, runtime permissions (Milestone 9B.1); unified
short-lived-token WebSocket authentication (Milestone 9B.2, ADR-014); a
home-screen attention widget plus a full-list in-app screen (Milestone
9B.3, ADR-015); and now a full voice infrastructure — `VoiceActivity`, a
`VoiceSession` client mirror, `AudioFocusManager`, `PlaybackManager`
(Android TTS), and foreground tap-to-talk speech input (Milestone 9B.4,
ADR-016), real-device-validated end-to-end on the S20 FE. Wake word remains
not yet built (see Roadmap, §18 — Milestone 9B.5).

**The M9A Hybrid decision** (the architectural decision this whole
component rests on): the PWA remains the rich conversational client. A
thin native companion is justified *only* for four capabilities
categorically unavailable to a PWA on Android — not merely unreliable,
but structurally impossible for a browser tab to provide:

1. A home-screen widget (no PWA API provides this at all).
2. Real audio-focus/ducking control (`AudioFocusRequest` is
   Android-native-only).
3. A wake word that survives backgrounding (Android suspends backgrounded
   JS; the Web Speech API is cloud-backed even in the foreground).
4. Presence that doesn't depend on a browser tab staying alive (a real,
   demonstrated platform behavior — Android discards a backgrounded PWA
   tab's execution context; this is not a bug to fix, it is the platform
   working as documented).

**What belongs on Android** (the companion's intended responsibility, once
built): a foreground service holding a persistent WebSocket connection to
the laptop; local wake-word detection; native audio focus/ducking; a
home-screen widget; a call-style attention UI equivalent to the PWA's.
The widget's own concrete design (`FUTURE DESIGN`) exists only as a
3-tier mockup in the M9A artifact — not authoritative, not yet decided.

**What MUST remain on the laptop, and why**: all conversational
reasoning, all state machines (`AttentionRequest`, `VoiceSession`, task
state), all persistence, all scheduling. The companion's actual module
boundary is a single Gradle module (`android/app/`) with Kotlin packages
`core`, `pairing`, `network`, `telemetry`, `service`, `settings`,
`diagnostics`, `ui`, `attention` (Milestone 9B.3), `widget` (Milestone
9B.3), `voice` and `audio` (Milestone 9B.4) under `com.jarvis.companion`
— a deliberate departure from the M9A proposal's separate-Gradle-module
names: multi-module Gradle adds real build-complexity overhead not
justified for a companion this size. `attention`/`widget` are a
**client-side rendering mirror only** (`AttentionRepository`, rebuilt from
server broadcasts, never a second source of truth) — not reasoning, not a
state machine of their own. `voice.VoiceSessionRepository` follows the
exact same mirror-only pattern for `VoiceSession` state; `voice.PlaybackManager`
and `audio.AudioFocusManager` own real Android system resources (TTS
engine, audio focus) but no conversational logic — they speak text and
manage focus/routing, they never decide what to say. `wakeword` is not
yet built. Explicitly, none of the built packages contain Supervisor logic.
This is not a preference; it is the same constraint stated in §1 and §2,
applied to a specific device.
The backend has been independently code-reviewed (Milestone 9A's D2) and
confirmed to support a native client without any reasoning duplication or
architecture change.

**Real risk findings from the Milestone 9B.0 spike** (not yet resolved
into a production design, but load-bearing for one):

- A real foreground service survives extended screen-off operation
  indefinitely when the app is granted Unrestricted battery status — 96
  continuous minutes, zero disconnects, real-device-verified.
- Under Samsung's *default* battery optimization, the same service
  suffers 5.5–11 minute silent gaps and real, repeated connection drops
  — a production companion will need to either request Unrestricted
  battery status during setup or design around this constraint. This is
  not a bug in Jarvis's own code; it is an OEM power-management policy
  that must be designed for, not assumed away.
- A Wi-Fi→cellular handover leaves a phone unable to reach the laptop's
  private LAN address at all — expected, correct network behavior, but a
  real deployment-architecture gap tracked separately as **Transport
  Reachability** (not a battery or lifecycle issue — see §16 and the
  Roadmap in §18).
- **microWakeWord** is the current evidence-based recommendation for the
  wake-word engine (see §7, §11), pending an actual build spike (gated,
  not yet started as of this document).

---

## 10. Browser PWA

**Strengths**: the PWA is, and will likely remain, the rich conversational
surface. Full voice input/output, timeline, notifications, deep links,
reconnect-safe conversation continuity, and a call-style attention UI are
all implemented and real-device-validated (Milestones 7, 7.1, 8, 8.1).

**Limitations, all categorically platform-imposed, not implementation
gaps**: no home-screen widget, no reliable background wake word, no real
audio-focus API, and unreliable background push delivery on at least one
real tested device (Doze-idle backgrounding defeats it despite every
standard server-side mitigation). None of these are things more browser
engineering can fix — this is precisely the evidence base for the M9A
Hybrid decision (§9).

**Why it remains**: for everything it *can* do, it does it well, requires
no install friction beyond "add to home screen," and needs no native
build/signing/distribution pipeline. There is no reason to replace a
capability the PWA already handles correctly.

**Coexistence with Android**: the intended model is not "Android replaces
the PWA" — it is "Android adds exactly the four capabilities the PWA
cannot have, and the PWA keeps everything else." Both surfaces talk to
the same laptop brain over the same protocol family (§6); neither is
aware of the other's existence beyond both being connections the
`ConnectionManager` tracks.

---

## 11. ESP Presence Device

**Status**: long-term vision only. No implementation, no hardware
selection, no protocol design has been done. This section exists so that
future design work has a stated direction to design *toward*, not to
prescribe an implementation.

**Conceptual role**: a small, dedicated, always-on presence device — not
a phone, not a browser tab — with a wake word, a physical button, an LED
for state feedback, a speaker, and a microphone, sitting in a room the
way a smart speaker does. Its entire job is: detect the wake word or
button press, open a connection to the laptop, relay audio, show state
via the LED, and nothing else.

**Explicitly out of scope for the device itself**: any reasoning, any
conversation memory, any decision about what an answer means. Every
principle in §1/§2/§9 about clients never becoming brains applies with
zero exceptions to this device — if anything, it applies more strictly,
since a presence device has even less business than a phone holding any
state beyond "am I currently connected, and what should my LED show."

**Open questions, deliberately unresolved here** (`FUTURE DESIGN`):
routing between the ESP device and Android/phone audio (e.g. does a call
on the phone take priority over the ESP device's mic), Bluetooth
interaction model, and the actual wake-word/hardware selection. These are
not decided and should not be inferred from this document.

---

## 12. Security Model

**OpenCode runtime isolation** (Milestone 6.1, the foundational decision):
a Jarvis-owned `opencode serve` instance runs against
`%LOCALAPPDATA%\JarvisOpenCodeRuntime` — a completely separate
data/config/cache/state tree from OpenCode Desktop's shared
`~/.local/share/opencode`/`~/.config/opencode`. This was necessary
because the shared database had cross-version-incompatible rows causing
a hard constraint violation on every write; isolating storage per-runtime
was chosen over four alternatives (upgrading, pinning, migrating, or
continuing shared) and won on every axis but one. **Never run the real
`opencode.exe` binary against the shared/default path for any diagnostic
purpose** — a partial-environment-variable invocation once coincided with
real, unrecovered data loss in the shared database.

**Credential isolation** (Milestone 9B.0, a second, distinct isolation
axis found necessary after storage isolation alone proved insufficient):
storage isolation does not isolate *environment variables*. A real
incident showed an ambient, Jarvis-unrelated `OPENAI_API_KEY` on the host
machine reaching the isolated OpenCode subprocess and being used for a
real paid model call. The fix: the owned-spawn subprocess environment is
built from an explicit, minimal, OS-essential allowlist — never the full
inherited environment. The isolated runtime's own provider credential
comes only from its own isolated `auth.json`, provisioned once from
`JARVIS_OPENCODE_OPENROUTER_KEY`/`JARVIS_LLM_API_KEY`, never from Desktop's
credentials, never logged.

**Explicit model pinning** (Milestone 9B.0, the same incident's other
half): every delegated OpenCode prompt now sends an explicit
`provider`/`model` field rather than letting OpenCode fall back to its
own default selection among whatever credentials happen to be visible.
This is validated through the same free-only guard the Supervisor's own
LLM uses (`JARVIS_LLM_FREE_ONLY`), with one narrow, explicitly-named,
currently-disabled emergency override (`JARVIS_OPENCODE_ALLOW_PAID`,
allowing exactly one named paid model, requiring explicit user approval
each time it is enabled) for situations where free-tier capacity is
genuinely unavailable.

**API keys and credentials, generally**: never logged (multiple tests
enforce this directly via `caplog` inspection), never committed, never
read from Desktop's OpenCode installation. `.env` holds Jarvis's own
credentials; a documented fragility exists around trailing-newline
corruption on append-based setup scripts (a procedural, not code-level,
mitigation).

**HTTPS / certificates**: mkcert-generated LAN certificates back the real
HTTPS deployment (`certs/jarvis-lan-*.pem`) — required for the "secure
context" APIs (microphone, service worker, push) to function at all on a
phone. The Android spike's `network_security_config.xml` (Milestone
9B.0, static per-IP trust — since retired with the spike) is superseded
by the production companion's trust-on-first-use pinning
(`pairing.PinnedTrustManager`, ADR-011): the user confirms the server's
certificate fingerprint once during pairing, and every subsequent
connection is trusted only if the presented certificate matches exactly
— the native equivalent of `ssh`'s host-key model, not a one-time
browser warning.

**WebSocket authentication** (Milestone 9B.1/9B.2, ADR-011/ADR-014):
`/ws` optionally requires a credential via `JARVIS_API_TOKEN` — a no-op
by default (today's actual deployment), a real check once an operator
sets it. Two mechanisms exist: a short-lived signed token
(`POST /api/ws-token` + `?token=`, ADR-014) that works for both the
browser PWA and the Android companion — the only mechanism a browser can
actually use, since it cannot set custom headers on a WebSocket
handshake — and a deprecated `Authorization: Bearer` header, kept working
for already-paired native clients during migration. See
`docs/protocols/websocket-protocol-v1.md` for the wire-level detail.

**Current gaps, honestly disclosed, not yet closed**: LAN-only
assumptions throughout (no secure remote connectivity); the browser PWA
has no credential-entry/storage UI, so `POST /api/ws-token` only works
unauthenticated (today's default, `JARVIS_API_TOKEN` unset) — the
*mechanism* asymmetry between PWA and Android is closed, but a PWA
credential-entry UI would still be needed before an operator could
actually enable `JARVIS_API_TOKEN` with both clients in use (TD-018).
These are acceptable for the current local-first, single-user, LAN-only
deployment model (§1) and become blocking concerns the moment remote
connectivity is considered (see Transport Reachability, §18).

**Future pairing / authentication / device identity**: `OPEN DECISION`.
M9A's own security design work (QR-pairing bootstrap, Android Keystore
credential storage, mkcert CA bundling) exists as a proposal in the
published M9A artifact but has not been implemented and is not
authoritative until it is.

---

## 13. Data Model

```
conversations ──────────┐
                         │ conversation_id (loose FK, no cascade)
tasks ───┬── questions   │
         │               │
         └── opencode_tasks
                         │
attention_requests ──┬── contact_attempts
                      │
                      └── voice_sessions (attention_request_id, nullable)

notifications (source_type/source_id → loosely correlates to task_id
                or attention_request_id; not a hard FK)

push_subscriptions (conversation_id, nullable)

settings (key-value, standalone)

events (append-only audit/timeline log, standalone)
```

| Table | Key fields | Purpose |
|---|---|---|
| `events` | `type`, `timestamp`, `content` | Append-only timeline/audit log |
| `tasks` | `task_id` (unique), `status`, `exit_code` | Local subprocess lifecycle |
| `questions` | `question_id` (unique), `task_id`, `status`, `answer` | Worker question/answer protocol |
| `opencode_tasks` | `task_id` (unique, FK→`tasks`), `session_id`, `status`, `last_evidence_type` | OpenCode session mapping + verified-evidence state |
| `conversations` | `conversation_id`, `role`, `content` | Per-turn message history |
| `notifications` | `notification_id` (unique), `dedup_key` (unique), `status` | Delivery-artifact record, deduplicated |
| `push_subscriptions` | `endpoint` (unique) | Web Push registrations |
| `settings` | `key` (PK), `value` | Simple key-value config |
| `attention_requests` | `attention_request_id` (unique), `dedup_key` (unique), `status`, `deferred_until` | The attention state machine (§8) |
| `contact_attempts` | `contact_attempt_id` (unique), `attention_request_id`, `channel`, `status` | One record per contact attempt |
| `voice_sessions` | `voice_session_id` (unique), `conversation_id`, `attention_request_id` (nullable), `state` | Voice session lifecycle (§7) |

Relationships are deliberately loose (string correlation, not always a
declared SQL foreign key with cascade) — this reflects that Jarvis's
tables were grown incrementally milestone-by-milestone, a known,
disclosed piece of technical debt (see `Known Bugs, Limitations, and
Technical Debt` in `SESSION.md`), not an architectural endorsement of
loose coupling as a goal in itself.

---

## 14. Failure Philosophy

**Never fake success.** A task, an OpenCode session, or a delivery is
never reported as succeeded because nothing indicated failure — silence
is not evidence (§2). This is the single most repeated lesson in this
project's history and the direct cause of several of its real bug fixes
(Milestone 6's entire verified-evidence rebuild; Milestone 9B.0's
insistence on cross-verifying client and server telemetry independently
before accepting a "survived" claim).

**Verified state over assumed state.** Every terminal state transition
(`completed`, `failed`, `resolved`) requires a specific, named piece of
evidence — a native event, a confirmed native action, an independently
observable log line — not elapsed time or absence of error.

**Retries are bounded and explicit.** Reconnect/backoff logic (WebSocket,
OpenCode SSE consumer, Android spike's WebSocket client) uses bounded
exponential backoff, never unbounded retry storms.

**Degraded state exists precisely to avoid lying.** A task interrupted by
a Jarvis restart becomes `degraded` (state genuinely unknown), not
`failed` — because a restart does not prove the underlying work failed,
and claiming it did would be a fabrication in the other direction.

**Restart/crash recovery is explicit, not accidental.** `TaskManager` and
`OpenCodeSupervisor` both have documented `reconcile_on_startup()`
behavior; `AttentionRequest`s and their bound `VoiceSession`s are
correctly cancelled together on a local-task restart cascade (a real
phone-found bug, fixed in Milestone 8.1) rather than left pointing at
work that no longer exists.

**Duplicate suppression is structural.** Notification deduplication
depends on a UNIQUE constraint at the database layer, not caller
discipline — the same underlying pattern used for `AttentionRequest`
idempotent creation. Both are designed to survive replay, reconnect, and
retry without producing a second logical copy of the same event.

**A known, disclosed, unresolved gap is preferable to a hidden one.**
This document, and `SESSION.md` before it, consistently records real
platform limitations (Doze-idle push delivery, browser STT accuracy,
Android background restrictions) as disclosed facts rather than treating
them as solved or ignoring them. A future engineer should be able to
trust that anything not listed as a known limitation has actually been
checked, not merely not-yet-encountered.

---

## 15. Extensibility

Jarvis is designed to grow by *addition at defined seams*, not by
modifying the reasoning core. The seams that exist today:

- **New worker type**: implement a protocol adapter (stdout/stdin
  question protocol, or a REST/SSE adapter analogous to
  `opencode_adapter.py`) and register it with `TaskManager` or an
  equivalent supervisor. The `Supervisor`'s tool-call loop and the
  attention/notification layers do not need to know the worker's
  internals — they consume the same verified-evidence contract every
  other worker does.
- **New contact channel**: implement `ContactChannel`'s interface
  (`attempt_contact()` returning a result with honest delivery
  certainty — §14) and register the channel name. `NATIVE_ANDROID`,
  `PHONE_CALL`, and `SMS` are reserved names awaiting exactly this.
- **New transport** (a new client type, e.g. the Android companion or a
  future ESP device): the client must speak the existing WebSocket
  protocol (§6) rather than requiring protocol changes, unless a real,
  demonstrated deficiency in the protocol itself — not merely "this
  client's first implementation is incomplete" — is found through actual
  use.
- **New device**: any new physical or software client is, architecturally,
  just another WebSocket connection plus whatever device-local
  capabilities it adds (audio focus, a widget, a wake word) — it never
  gets its own copy of `AttentionRequest`/`VoiceSession`/task state.
- **New LLM provider**: `LLMProvider`/`FakeLLMProvider` in
  `app/supervisor/llm.py` is the abstraction boundary; a new provider
  implements the same minimal interface and passes through the same
  free-only validation.
- **New wake-word engine**: plugs in at the point where a `VoiceSession`
  is opened — the engine's only contract with the rest of the system is
  "detect the phrase, then open a session," never anything about what
  happens after.

---

## 16. Architectural Decisions

These are the load-bearing decisions that must not be silently reversed.
Each references the milestone where it was established; full evidence
lives in `SESSION.md`'s "Architecture Decisions That Must Not Be
Accidentally Reversed" section, which this ADR list summarizes at the
"why" level rather than duplicating verbatim.

**ADR-001 — The laptop remains the brain.** All reasoning, state
machines, and persistence live on the laptop. No client is permitted to
accumulate reasoning logic. (§1, §2; reaffirmed at every milestone
touching a new client surface.)

**ADR-002 — Hybrid Android architecture.** A native Android companion is
justified only for capabilities categorically unavailable to a PWA
(widget, audio focus, backgrounding-resistant wake word, always-on
presence) — not as a general replacement for the PWA. (Milestone 9A.)

**ADR-003 — Deterministic attention and interruption.** `AttentionPolicy`
and `InterruptionPolicy` are pure functions with no LLM involvement.
(Milestones 7, 8.)

**ADR-004 — OpenCode via REST + SSE, not terminal scraping.** Scored
47/50 against PTY control, `opencode run`, and ACP across ten weighted
criteria. (Milestone 4/5/6.)

**ADR-005 — OpenCode storage isolation.** A Jarvis-owned OpenCode server
never touches OpenCode Desktop's shared database or config. (Milestone
6.1.)

**ADR-006 — OpenCode credential isolation and explicit model pinning.**
The owned-spawn subprocess environment is an allowlist, not an inherited
environment; every delegated prompt pins provider+model explicitly.
(Milestone 9B.0.)

**ADR-007 — Notifications are idempotent by database constraint, not
caller discipline.** `dedup_key` UNIQUE + `INSERT OR IGNORE`. (Milestone
7.)

**ADR-008 — `AttentionRequest` creation is idempotent by source.**
`get_or_create()`'s `dedup_key`, never created from UI rendering, polling,
or SSE replay. (Milestone 8.)

**ADR-009 — Voice input shares the exact same code path as typed text.**
No voice-specific conversational logic; only the input modality differs.
(Milestone 7.)

**ADR-010 — `VoiceSessionManager` must never duplicate `Supervisor`
reasoning.** Thin pass-through only. (Milestone 8, violated once and
corrected.)

**ADR-011 — The attention scheduler is entirely DB-driven.** No part of
"when is this due" logic lives client-side. (Milestone 8.)

**ADR-012 — Free-only LLM guard for the Supervisor, with a narrow,
separately-scoped, explicitly-approved paid-model allowlist for delegated
OpenCode workers only.** The two guards share the same underlying
validation function but are configured independently
(`JARVIS_LLM_FREE_ONLY` vs. `JARVIS_OPENCODE_ALLOW_PAID`), and the latter
requires re-approval each time it is enabled. (Milestone 5; Milestone
9B.0.)

**ADR-013 — Evidence-based state transitions for delegated/native work.**
Neither an OpenCode task's completion nor a native device's "survived
screen-off" claim is accepted without independently-verifiable evidence
from more than one source where more than one source is available.
(Milestone 6; Milestone 9B.0.)

---

## 17. Current System Status

**Implemented** (real, tested, in most cases real-device-validated):
WebSocket phone interface; local task management; OpenCode integration
(REST/SSE, verified lifecycle, isolated storage, credential isolation,
explicit model pinning); conversational Supervisor with tool-call loop;
`AttentionRequest`/`ContactAttempt`/`Notification`/`VoiceSession` state
machines; deterministic `AttentionPolicy`/`InterruptionPolicy`; DB-driven
scheduler; voice input/output (browser); Web Push (foreground/recently-
backgrounded delivery); PWA install/manifest/service worker; deep
linking; natural-language deferral parsing.

**Prototyped** (real code, real physical-device evidence, explicitly not
production): the Android presence spike (`spikes/android-presence/`) —
foreground service, WebSocket transport with connection-generation
tracking, telemetry, real connection to the real Jarvis server over the
real protocol. Classified DISPOSABLE (Milestone 9B.0) — proved technical
feasibility, retained as historical reference, not built upon directly.

**Built, not yet real-device-validated** (Milestone 9B.1): the production
Android companion (`android/`, `com.jarvis.companion`) — TOFU
certificate-pinned pairing (ADR-011), production WebSocket client,
`PresenceService`, encrypted config storage, telemetry, three UI screens.
`./gradlew assembleDebug`/`testDebugUnitTest` pass (14/14); no `adb`
device was connected during this milestone's work, so install/pairing/
connection behavior on real hardware is unverified — required before this
milestone can close (ADR-010).

**Planned** (architecture proposed, not started): wake-word production
integration (gated on a build spike, D5, itself gated on D1 acceptance —
accepted, not yet built); Android audio integration and home-screen widget
(companion capabilities beyond this foundation); VoiceSession ownership
guard (designed, not implemented — matters once a
second simultaneous client exists); separate `permissions` table;
event/task table rotation.

**Future ideas** (vision-level only, no design): ESP presence device
(§11); remote/non-LAN reachability (§18); Bluetooth audio routing;
LLM-based interruption scoring (explicitly rejected as a direction so
far, kept deterministic instead — would require strong evidence to
revisit).

---

## 18. Roadmap

The sequence below reflects dependency order actually discovered through
the milestones so far, not an arbitrary plan.

**M9B.0 (closed)**: technical risk reduction only — resolved whether a
native foreground presence service survives real-world conditions, and
which wake-word engine is buildable. Not production code. Both primary
risks got real-device evidence (battery-optimization dependency identified
and mitigated; microWakeWord recommended, pending a build spike). See
`docs/RELEASE_CHECKPOINT_M9B0.md`.

**M9B.1 (production foundation, in progress)**: the production Android
companion (`android/`), following the module boundary in §9 — pairing,
transport/reconnect, presence service, telemetry, config storage,
notification channels, runtime permissions, and minimal UI. Explicitly
excludes wake word, widgets, Bluetooth, remote connectivity, background
microphone, AI/LLM, and business logic — those require separate explicit
approval as later milestones. Expected order after this: audio integration
→ widget → wake word — deferring wake word to last because it is the
highest-uncertainty, highest-effort piece and the rest of the companion has
value independent of it.

**M10 (candidate, not scoped)**: whichever of the "carried-over, not
designed" items (separate permissions table, table rotation, VoiceSession
ownership guard, iOS Safari validation) becomes highest-priority once
M9B production work creates real pressure on them — e.g. the ownership
guard becomes load-bearing the moment a second simultaneous client
exists.

**M10A — Remote Reachability Research (candidate, not scoped)**:
Transport Reachability (§9, §12) — what happens when the phone is not on
the same LAN as the laptop. Deliberately not investigated during M9B.0
per explicit scope discipline; likely requires its own security-model
extension (§12's current gaps become blocking the moment "remote" is on
the table) rather than a quick fix.

**M11 and beyond (vision-level only)**: ESP presence device (§11) once
the Android companion has proven out the presence/transport/attention
model on a second device; broader multi-client coordination (the
VoiceSession ownership guard is the first piece of this).

The guiding rule for this sequence, restated: production Android work
should not begin until the disposable spike has answered the questions it
was built to answer, and each subsequent milestone should be justified by
real pressure from the milestone before it, not scheduled in advance of
that evidence.

---

## Document Review Notes

This section records the self-review performed before considering this
document complete, per the explicit instruction to check for
contradictions, duplicated concepts, missing ownership, ambiguous
responsibilities, and drift from implemented reality.

- **State ownership** (§5) was cross-checked directly against
  `app/database.py`'s actual `CREATE TABLE` statements and each owning
  module's state constants (`attention_manager.py`'s `STATUS_*`,
  `voice_session_manager.py`'s `STATE_*`), not reconstructed from memory
  — this document's table names and states match the running schema
  exactly as of Milestone 9B.0.
- **No module is described as owning a table it does not exclusively
  write to.** Checked `contact_attempts`/`notifications` specifically
  since they are the two tables most likely to be conflated (§5's "four
  concepts" warning exists because this conflation has been a real risk
  before).
- **The Android companion's status is stated once, consistently, in
  §9, §17, and §18** — a spike exists, production has not begun — to
  avoid the document implying anywhere else that more has been built
  than actually has.
- **Security gaps (§12) are stated as currently-accepted, not
  resolved** — this document does not claim authentication/TLS-by-default
  exists; it explicitly says it does not, and says why that is currently
  acceptable (LAN-only, single-user deployment) rather than silently
  omitting it.
- **No contradiction found between §2's "deterministic policies where
  possible" and §4's Supervisor description** — the Supervisor's own use
  of an LLM is scoped to conversational routing/tool-selection, which is
  the one place this document says an LLM is appropriate; every other
  policy component (§2, §4, §8) is explicitly pure/deterministic. This
  boundary was checked for consistency across all three sections.
- **ADR list (§16) cross-checked against `SESSION.md`'s own numbered
  "Architecture Decisions" list** for the load-bearing subset — this
  document does not attempt to restate all 29 of those entries (many are
  implementation-level, e.g. exact `flex-shrink` CSS rules, which belong
  in `SESSION.md`, not here) but does not contradict any of them.
- **Roadmap (§18) deliberately does not repeat `SESSION.md`'s
  content** — it states the dependency reasoning for the sequence, which
  `SESSION.md` (a chronological record) does not itself synthesize.
