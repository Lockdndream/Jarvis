# Jarvis Technical Debt Register

This is not a bug list. Routine bugs are tracked and fixed inline (see
`SESSION.md`'s "Known Bugs, Limitations, and Technical Debt" for the full
itemized history). This register exists for debt that is *structural* —
things a future milestone must deliberately schedule work against,
because they will not get fixed as a side effect of unrelated feature
work.

Each item: **ID**, **Description**, **Category**, **Severity**, **Owner**,
**Origin milestone**, **Risk**, **Recommended milestone**, **Status**.

Categories: **Architecture debt** (a design decision that needs
revisiting), **Implementation debt** (the design is right, the
implementation hasn't caught up), **Testing debt** (a real gap in
verification coverage), **Documentation debt** (the docs and the system
have drifted apart), **Operational debt** (a deployment/runtime concern
with no code fix, or a process gap).

Severity: **Critical** (blocks safe production use of the affected
area), **High** (a known, real risk that should be resolved before the
affected area sees more load-bearing use), **Medium** (should be fixed,
not urgently), **Low** (cosmetic or very low probability of mattering).

---

## Architecture Debt

### TD-001 — `push_subscriptions` ownership violates the single-owning-module principle

- **Description**: `push_subscriptions` is written directly from
  `app/main.py`'s subscribe endpoint (`save_push_subscription()`), not
  through `app/notifications.py`/`app/push.py`, which only read it. Every
  other table in the system has exactly one owning module; this one does
  not.
- **Severity**: Medium
- **Owner**: `app/main.py` (writer), `app/push.py` (reader) — should
  become `app/push.py` (or a dedicated module) exclusively.
- **Origin milestone**: Milestone 7
- **Risk**: Low immediate risk (the table is simple, single-purpose,
  unlikely to develop write-conflict bugs) but sets a precedent that
  weakens the "single owning module" principle (`ARCHITECTURE.md` §2) if
  left as a model for future tables.
- **Recommended milestone**: Any future pass touching push notifications;
  not urgent enough to justify a dedicated milestone alone.
- **Status**: Open, disclosed (`ARCHITECTURE.md` §4/§5, ADR-006)

### TD-002 — VoiceSession has no multi-client ownership guard

- **Description**: Two clients could each successfully call
  `open_session()` against the same bound `AttentionRequest`
  simultaneously, creating two independent `VoiceSession`s with no
  coordination. Fixed: a nullable `attention_requests.active_voice_session_id`
  column, claimed atomically (set-if-null,
  `db.try_claim_voice_session_lease()`) by `open_session()`, released by
  `close_session()`/`fail_session()`. A second client's attempt to bind an
  already-leased `AttentionRequest` now raises `VoiceSessionError` instead
  of silently proceeding.
- **Severity**: Was High before a second simultaneous voice-capable client
  existed — now resolved, since Milestone 9B.4 is exactly that trigger
  (the Android companion gained real voice capability).
- **Owner**: `app/voice_session_manager.py`, `app/attention_manager.py`,
  `app/database.py`
- **Origin milestone**: Milestone 9A (found by independent code review)
- **Resolved milestone**: Milestone 9B.4 (ADR-016), 2026-07-14. A related
  gap found and fixed during the same milestone: nothing previously closed
  a voice session left open by an abruptly disconnected WebSocket, which
  would have let a lease be held forever — `app/main.py`'s connection
  handler now closes any still-open session for that connection in a
  `finally` block.
- **Status**: Resolved (ADR-007, ADR-016, Known Limitation #46)

### TD-003 — Transport Reachability (remote/non-LAN connectivity)

- **Description**: A real Wi-Fi→cellular handover leaves a phone unable
  to reach the laptop's private LAN address at all — expected network
  behavior, but no architecture exists yet for a client that is not on
  the same LAN as the laptop.
- **Severity**: High for any deployment scenario beyond "phone and laptop
  always on the same LAN"; Low for the current deployment model.
- **Owner**: Unowned — no module currently addresses this.
- **Origin milestone**: Milestone 9B.0 (Wi-Fi→cellular incident, real
  device evidence)
- **Risk**: Any real-world use where the phone leaves the house Wi-Fi
  network loses presence/connectivity entirely, with no fallback and no
  user-facing explanation of why.
- **Recommended milestone**: **M10A — Remote Reachability Research**
  (candidate, not yet scoped) — deliberately deferred, not investigated
  during M9B.0 per explicit scope discipline.
- **Status**: Open, explicitly deferred (`ARCHITECTURE.md` §18, Known
  Limitation #50)

### TD-005 — Notification routing has no `conversation_id` link for OpenCode-originated notifications

- **Description**: `opencode_tasks` isn't currently linked back to the
  conversation that started it, so OpenCode-originated notifications
  route by `task_id`/`question_id` only, not by conversation.
- **Severity**: Low — a real gap, but does not currently cause incorrect
  behavior, only a missing correlation capability.
- **Owner**: `app/notifications.py`, `app/integrations/opencode_supervisor.py`
- **Origin milestone**: Milestone 7
- **Risk**: Blocks any future feature that wants to show "which
  conversation triggered this OpenCode notification" — currently
  unanswerable from the data model as-is.
- **Recommended milestone**: Whenever the conversation→task linkage
  itself is designed (currently unscheduled).
- **Status**: Open, disclosed (Known Limitation #33)

### TD-006 — Permission/Question conflation in the `questions` table

- **Description**: Permissions are stored in the `questions` table with
  prefixed text rather than a separate `permissions` table — mixing two
  semantically distinct concepts into one schema.
- **Severity**: Medium — works correctly today, but the conflation makes
  the schema harder to reason about and blocks clean permission-specific
  querying/indexing.
- **Owner**: `app/task_manager.py`, `app/database.py`
- **Origin milestone**: Milestone 3/4 (original design)
- **Risk**: Low functional risk; primarily a maintainability/clarity cost
  that compounds the longer it's deferred.
- **Recommended milestone**: Any future schema-migration pass; carried
  over as a named item since Milestone 6.
- **Status**: Open, long-disclosed, unaddressed

### TD-025 — Control Center's `currentTurn` cannot represent two concurrent turns

- **Description**: `dashboard.js`'s "Current Conversation" panel holds a
  single global `state.currentTurn` slot. If a second turn begins (e.g.
  a voice turn and a PWA/text turn both in flight at once) before the
  first is rendered as complete, `addToolCall`'s synthetic-turn fallback
  silently discards the previous turn's state rather than representing
  both. Found during the Control Center's independent architectural
  review (Milestone 9B.10) and deliberately left unfixed in the same
  milestone's hardening pass, since resolving it is a data-model change
  (a turn history/stack, not a single slot), not a bug fix, and was
  explicitly out of that pass's fix-only scope.
- **Severity**: Low today — Jarvis is currently a single-user,
  effectively-single-active-conversation system in practice, so true
  concurrent turns are rare. Would become more relevant if multi-device
  simultaneous use (see the Control Center roadmap's Version 2.0 ideas)
  is ever pursued.
- **Owner**: `app/static/dashboard/dashboard.js`
- **Origin milestone**: Milestone 9B.10 (Control Center architectural
  review)
- **Risk**: A dashboard viewer could see one turn's data silently
  replaced by another's mid-observation, with no indication anything
  was dropped — a correctness gap in what the panel implicitly promises
  ("this is the current conversation"), not a crash risk.
- **Recommended milestone**: Only if/when multi-device concurrent use
  becomes a real product goal; unscheduled otherwise.
- **Status**: Open, disclosed (`docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`)

### TD-028 — Raw audio capture has no speaker isolation; any voice in range is transcribed as the user

- **Description**: Discovered live during Interaction Layer Step 5
  real-device testing (transcript verification made this visible for
  the first time — the user could suddenly see, not just hear, what got
  transcribed). `useRawAudioCapture`'s pipeline
  (`SpeechInputController.startListeningRaw` → one WAV blob per
  utterance → Groq Whisper) has no mechanism to identify who is
  speaking, how close they are to the microphone, or to reject/attenuate
  a second voice in range. A real session captured a phrase the user
  did not say ("Can cancel current license") appended to their actual
  question, most plausibly ambient/second-party speech blended into the
  same recording window. This is a capture-and-pipeline design property,
  not a one-off transcription error — there is no diarization,
  voice-print matching, or proximity gating anywhere between the
  microphone and Groq.
- **Severity**: High for any multi-person environment (the user's own
  words: "almost unusable if I have people around me") — the phone
  cannot currently distinguish its owner's voice from anyone else's
  in mic range, and a contaminated transcript is sent to the Supervisor
  as fact, with no client-side or server-side signal that it might be
  mixed-speaker input.
- **Owner**: `android/.../voice/AndroidAudioCaptureEngine.kt` (capture),
  `app/stt.py` (Groq Whisper call) — no existing module owns
  speaker/proximity discrimination.
- **Origin milestone**: STT feature (Month 2, raw-audio-capture rollout)
  first noted "voice isolation" as an open concern in passing; concretely
  reproduced with a specific bad transcript during Interaction Layer
  Step 5 (this milestone) once the new conversation view made the
  contaminated transcript visible rather than only heard as a slightly
  odd spoken reply.
- **Risk**: Any voice command interpreted from contaminated input can
  trigger a real tool call or task on the user's behalf that they never
  actually asked for — this is a correctness and, depending on the
  command, a safety-adjacent risk, not merely an annoyance.
- **Recommended milestone**: Unscheduled — needs real investigation into
  what's feasible (on-device voice-print enrollment/matching, a
  proximity/volume gate before capture even starts, or a lighter-weight
  mitigation like echoing the transcript back for confirmation before
  acting on anything non-trivial). A meaningfully different capture
  architecture, not a small patch.
- **Status**: Open, newly discovered and documented this milestone.

### TD-029 — Silence detection can fail to trigger end-of-utterance at all, silently losing the entire recording

- **Description**: Discovered live during Interaction Layer Step 5
  real-device testing, distinct from and more severe than TD-028's
  cross-talk contamination finding — here nothing was contaminated,
  nothing was transcribed at all. The user said "cancel it" to a
  listening session; the server log shows the session opened
  (`voice session opened: id=vs_63270348a885`) and then sat in
  `listening` state for roughly a minute with **zero** Groq
  transcription calls logged, before finally transitioning
  `listening -> closing -> closed` when the app was backgrounded — the
  captured audio (if any was ever actually buffered) was never sent to
  `sendVoiceSessionAudio` at all. `SpeechInputController.startListeningRaw`'s
  end-of-utterance/silence-detection logic did not fire for this
  utterance, so the turn was silently and completely lost rather than
  merely producing a bad transcript. The user separately reported the
  same underlying symptom conversationally ("he just continues to
  listen even after I finished talking... I blocked both mics on my
  phone and it still won't stop listening") before this specific,
  loggable reproduction. **Reproduced a third time** later in the same
  Step 5 session under the most ordinary possible condition — a room
  fan running, no other people talking, no unusual noise — with the
  user's own assessment: "almost unusable unless I'm in a... silent
  room with acoustic foam." This is not an edge case requiring unusual
  input; ordinary ambient noise (a fan, presumably equally an AC unit,
  street noise, etc.) reproduces it directly.
- **Severity**: **Critical**, upgraded from the original High rating —
  the third reproduction shows this triggers under completely ordinary
  home/office ambient noise, not a rare edge case. A user-issued
  command (in the second reproduction, a task-cancellation request)
  can be silently dropped with no error shown, indistinguishable from
  the app simply ignoring the user until the session eventually times
  out on its own. As currently observed, voice interaction is
  effectively unreliable outside a near-silent room, which undermines
  the core interaction mode this entire Interaction Layer milestone
  was built to make transparent.
- **Owner**: `android/.../voice/SpeechInputController.kt`,
  `AndroidAudioCaptureEngine.kt` (silence/end-of-utterance detection).
- **Origin milestone**: Silence-detection brittleness was already a
  named open concern from the STT feature's original rollout (Month 2);
  concretely reproduced with a specific, timestamped server-log gap
  during Interaction Layer Step 5 (this milestone).
- **Risk**: Any command, not just informational questions, can be lost
  this way — including safety-relevant ones like "cancel it"/"stop it".
  A user has no on-screen indication that their utterance was never
  even sent, only that the app eventually gives up and returns to idle.
- **Recommended milestone**: **The first fix after this sprint closes**
  (explicit user priority call, given Critical severity) — a focused
  piece of work, not a research project. Root cause per user direction:
  the current end-of-utterance logic uses a flat RMS/amplitude
  threshold, which cannot distinguish speech from steady-state ambient
  noise (a fan, AC, traffic) — any such noise simply never drops below
  the threshold, so silence is never detected. Replace with a proper
  VAD (voice activity detection) that discriminates speech spectral/
  temporal characteristics from steady-state noise, rather than tuning
  the existing amplitude threshold further. May share implementation
  territory with TD-028's speaker-isolation fix, but the VAD swap itself
  is the well-scoped, immediately actionable piece.
- **Status**: Open, newly discovered and documented this milestone.
  Explicit user priority: fix immediately after Interaction Layer closes.

---

## Implementation Debt

### TD-007 — Isolated OpenCode's auxiliary "title" generation call is not covered by the free-model pin

- **Description**: `send_prompt()`'s explicit model pin only applies to
  the primary "build" agent call; OpenCode's own automatic per-session
  title-summarization call uses its own internal small-model selection
  and was observed using a non-free model even after the cost-boundary
  fix.
- **Severity**: Low — small, cheap, per-session cost, not a repeat of the
  original large-scale leak this milestone fixed.
- **Owner**: `app/integrations/opencode_adapter.py`
- **Origin milestone**: Milestone 9B.0
- **Risk**: A very small, ongoing real-money cost per delegated session
  that the free-only guarantee does not currently cover.
- **Recommended milestone**: Any future OpenCode-adapter pass; no
  documented OpenCode config knob for pinning this was found within the
  time available this milestone.
- **Status**: Open, disclosed (Known Limitation #51)

### TD-008 — `reconcile_on_startup()` does not use the working message/status endpoints

- **Description**: Degraded-task upgrade logic still performs the
  original `GET /session/{id}` check from Milestone 6, never extended to
  use the message-history endpoint that has actually worked since
  Milestone 6.1.
- **Severity**: Low
- **Owner**: `app/integrations/opencode_supervisor.py`
- **Origin milestone**: Milestone 6.1
- **Risk**: `degraded` OpenCode tasks from a Jarvis restart may remain
  permanently unreconciled when they could, in principle, now be
  resolved with the working endpoint.
- **Recommended milestone**: Unscheduled; carried over since Milestone
  6.1.
- **Status**: Open, long-disclosed (Known Limitations #28, #39)

### TD-009 — `.jarvis_opencode_owner.json` is a single global file, not scoped per-port

- **Description**: Running two Jarvis instances against two different
  OpenCode ports on one machine would need this ownership-marker
  mechanism generalized — it currently assumes one Jarvis instance per
  machine.
- **Severity**: Low — not a problem under the current single-instance
  deployment model.
- **Owner**: `app/integrations/opencode_server.py`
- **Origin milestone**: Milestone 6
- **Risk**: Would silently misbehave (incorrect ownership claims) if
  Jarvis is ever run multi-instance on one machine — currently
  hypothetical.
- **Recommended milestone**: Only if multi-instance deployment is ever
  actually needed.
- **Status**: Open, disclosed (Known Limitation #23)

### TD-010 — `contact_attempts.notification_id` is never populated

- **Description**: `ContactChannel.attempt_contact()` implementations
  return a `notification_id` in their result dict, but
  `db.update_contact_attempt_status()` never persists it — the column
  exists and is always `NULL`.
- **Severity**: Low
- **Owner**: `app/database.py`, `app/attention_manager.py`
- **Origin milestone**: Milestone 8.1 (found during real-phone DB
  verification)
- **Risk**: Minor observability gap — traceability from a
  `ContactAttempt` to the `Notification` it produced is currently
  unavailable.
- **Recommended milestone**: Small, low-risk fix; unscheduled.
- **Status**: Open, disclosed (Known Limitation #43)

### TD-026 — `project_dir` provides no real containment; OpenCode can escape its assigned sandbox with zero permission gate

- **Description**: Discovered live during Month 2 Weeks 7-8 (walk-away
  mode) real-device testing, not hypothesized. A voice command ("run
  the tests on Jarvis") resolved to the `jarvis-test` safe-project alias
  (a near-empty 9-file sandbox, `tests/test_projects/safe-test/`,
  containing no real test suite). The dispatched OpenCode task ran for
  ~31 minutes and, per its own self-reported result summary on
  cancellation, actually executed real tests from this repository's
  *actual* `tests/test_plan_executor.py` (confirmed: the reported
  in-progress test name, `test_reconcile_completed_opencode_task_succeeds_step`,
  is a real, verified test in that file) — meaning the agent navigated
  out of its assigned `project_dir` into the live repository root and
  ran the real 859-test pytest suite against real source, with `.env`,
  `certs/`, `jarvis.db` (the live production database), and
  `projects.json` all reachable from that location. Checked directly:
  zero rows exist in the `questions` table for this task, and zero
  `/permission` grants were logged for it anywhere in the server log —
  confirming this happened with **no permission escalation at all**,
  not a permission that was silently auto-approved. `project_dir` is
  implemented purely as the `opencode serve` subprocess's working
  directory (`app/integrations/opencode_server.py`, `cwd=self.project_dir`)
  with no OS-level jail, chroot, container boundary, or filesystem ACL
  enforcing it — confirmed by reading the code, not inferred. The
  project-scoping model this codebase (and `ARCHITECTURE.md`/ADR-004)
  has consistently described as a safety boundary does not actually
  contain simple shell navigation (e.g. `cd ../..` followed by a normal
  command) for at least this class of action.
- **Severity**: **Critical.** Not a hypothetical — a live, unattended
  delegation reached the production database, secrets, and TLS private
  key files with zero gate. Accepted as non-blocking for walk-away
  mode's initial ship under the current usage model (single user,
  WiFi-only, the user present and watching or nearby, per explicit
  scope decisions this same milestone made) — but this severity
  escalates sharply the moment any future milestone removes the
  "user present" assumption further (fully autonomous overnight plan
  runs, remote/non-LAN access per TD-003, or multi-user access), since
  those are exactly the scenarios where nothing else would catch an
  OpenCode agent quietly reading or exfiltrating `.env`/`certs/`/
  `jarvis.db` contents mid-delegation.
- **Owner**: `app/integrations/opencode_server.py` (subprocess `cwd`
  wiring), `app/integrations/opencode_supervisor.py` (project_dir
  plumbing), OpenCode's own permission-request model (external
  dependency — whether *any* class of action can be made to require a
  permission grant when it touches a path outside project_dir is an
  OpenCode-side capability question, not something this codebase alone
  controls).
- **Origin milestone**: Milestone 9A (`jarvis-app-src`'s own
  description already asserts "excludes .env, projects.json, certs/,
  jarvis.db... any write must be explicitly denied" — the isolation
  model has been described as a safety boundary since that milestone,
  never verified against an actual escape attempt until now); discovered
  Month 2 Weeks 7-8 (walk-away mode real-device test).
- **Risk**: Every existing safe-project description in `projects.json`
  (`jarvis-app-src`, `jarvis-repo-tests` newly added this milestone) that
  asserts scoping/read-only guarantees is currently only as strong as
  the calling LLM's own default behavior — there is no enforced fallback
  if the agent (deliberately, confusedly, or via a future model that
  behaves differently) decides to look outside its assigned directory.
  `jarvis-repo-tests` (added this milestone specifically to let
  walk-away mode run the real test suite) was deliberately scoped to
  the full repo root on this exact understood tradeoff — see its
  `projects.json` description, which states this risk explicitly and
  relies on this same unresolved gap.
- **Recommended milestone**: Unscheduled — needs real investigation into
  what OpenCode-side permission configuration (if any) can be made to
  gate filesystem access outside a session's working directory, and/or
  whether an OS-level containment mechanism (a restricted user account,
  a container, a filesystem-level ACL) is warranted before any milestone
  removes the "user present and able to notice something wrong" safety
  net walk-away mode currently still leaves partially intact. Must be
  resolved before any future milestone considers remote/non-LAN access
  (TD-003) or fully unattended overnight autonomous operation.
- **Status**: Open, newly discovered and documented this milestone — not
  fixed. Explicit user decision: does not block shipping walk-away mode
  for personal, WiFi-only, user-present-or-nearby use; must be addressed
  before broadening that usage model.

### TD-027 — Supervisor's tool-calling loop repeats identical tool calls and doesn't recognize a plain conversational decline

- **Description**: Discovered live during Interaction Layer Step 5
  real-device testing — the new `thinking_update` transparency feature
  (Step 1/3) made this visible for the first time; the underlying
  behavior is in `app/supervisor/supervisor.py`'s tool-calling loop, not
  the Interaction Layer itself. Reproduced with an exact transcript
  (`conv_ab1319fb10ef`, 2026-07-31): a single informational question
  ("what's the last thing we did on the calculator-mod project")
  produced 12 tool calls — `catch_me_up`, `list_tasks`, `list_plans`,
  `what_do_you_remember`, `recent_activity`, `get_projects`, each called
  **twice** with identical arguments — before hitting `MAX_TOOL_CALLS`
  and returning only "I've reached the maximum number of actions I can
  take in one response," never a real answer. The user's very next turn,
  a plain conversational close ("No, that's all. Thank you.") with no
  information content requiring any tool at all, triggered the
  **identical 6-tool sequence a third time**, hit the same ceiling, and
  returned the same generic fallback message again.
- **Severity**: High for usability — a user cannot currently end a
  conversation with an ordinary decline phrase without the Supervisor
  launching a full, wasted tool-calling pass; and simple informational
  questions can fail to produce an answer at all by exhausting
  `MAX_TOOL_CALLS` on self-duplicated calls. Not a data-safety issue
  (all calls were read-only informational tools in this reproduction)
  but a first-class correctness/experience defect now that the
  transparency feature makes it directly visible rather than only
  inferred from a slow, unhelpful reply.
- **Owner**: `app/supervisor/supervisor.py` (`SYSTEM_PROMPT`, the main
  tool-calling loop, `_resolve_deterministic_command`'s decline/stop
  grammar).
- **Origin milestone**: Discovered during Interaction Layer Step 5
  (real-device validation); the tool-calling loop and `SYSTEM_PROMPT`
  themselves predate this milestone.
- **Risk**: Any turn resembling "just tell me X" against a
  read-only/informational question is at risk of never producing an
  answer if the model chooses to re-call the same tools rather than
  synthesize a response from the first pass; a plain decline being
  misrouted into the full LLM loop wastes a `MAX_TOOL_CALLS` budget on
  every single "no thanks."
- **Recommended milestone**: Needs dedicated investigation, not a
  same-pass patch: (a) whether `SYSTEM_PROMPT` needs an explicit
  once-per-tool-per-turn instruction or the loop itself needs
  call-signature deduplication, and (b) whether
  `_resolve_deterministic_command`'s grammar should recognize a
  standalone conversational close (no bound pending item, no specific
  command) as a zero-tool acknowledgment rather than falling through to
  the full LLM loop. Explicit user decision this milestone: document
  only, do not fix mid-Step-5.
- **Status**: Open, newly discovered and documented this milestone — not
  fixed.

### TD-024 — Control Center event-shape handling relies on an unenforced naming convention

- **Description**: `dashboard.js`'s `handleNamedEvent`/`routeWsMessage`
  unwraps incoming WS frames via `data.content ?? data` — correct today
  only because no observer event type broadcasting raw top-level fields
  (e.g. `device_status_update`) also happens to name one of those fields
  `content`. Nothing enforces this; it is an implicit contract between
  every future event type's payload shape and this one line of frontend
  code. Found during the Control Center's independent architectural
  review (Milestone 9B.10) and deliberately left as-is in the same
  milestone's hardening pass — fixing it properly means giving every
  observer event an explicit, self-describing envelope, which is a
  protocol change belonging with a future protocol revision, not a
  same-pass bug fix.
- **Severity**: Low today (zero current event types collide), Medium
  if a future event type is added carelessly.
- **Owner**: `app/static/dashboard/dashboard.js`; the actual fix would
  also touch `docs/protocols/control-center-observer-protocol-v1.md`'s
  §2 event-type table.
- **Origin milestone**: Milestone 9B.10 (Control Center architectural
  review)
- **Risk**: A future observer event type that reuses the field name
  `content` for something other than "this is the whole payload" would
  silently mis-render rather than fail loudly.
- **Recommended milestone**: Address the next time a new observer event
  type is added — give every event an explicit envelope
  (`{"type": ..., "payload": {...}}`) rather than patching around this
  one collision risk. Unscheduled otherwise.
- **Status**: Open, disclosed (`docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`)

---

## Testing Debt

### TD-011 — SSE reconnect-mechanics have no dedicated test

- **Description**: `consume_events()`'s exponential backoff (unchanged
  since Milestone 4) was never given a dedicated test — only the
  event-parsing layer built on top of it was verified.
- **Severity**: Medium
- **Owner**: `app/integrations/opencode_events.py`
- **Origin milestone**: Milestone 6
- **Risk**: A regression in reconnect/backoff behavior specifically would
  not be caught by the existing test suite, though the evidence-based
  handlers built on top structurally prevent it from causing a false
  task-completion/failure.
- **Recommended milestone**: Unscheduled.
- **Status**: Open, disclosed (Known Limitation #22)

### TD-012 — iOS Safari behavior is entirely untested

- **Description**: Voice/push assumptions for iOS Safari (limited/no
  SpeechRecognition support historically, Web Push requiring iOS 16.4+
  and homescreen install) are based on general platform knowledge, never
  independently re-derived or real-device-tested. Only Android has been
  tested.
- **Severity**: Medium — a real, sizable class of users (any iPhone user)
  has entirely unverified behavior.
- **Owner**: No owning module — a testing gap, not a code gap.
- **Origin milestone**: Milestone 7
- **Risk**: Unknown — could range from "works fine" to "core features
  silently unavailable," genuinely unverified either way.
- **Recommended milestone**: Before any claim of general phone-platform
  support is made; unscheduled as of this register.
- **Status**: Open, disclosed (Known Limitation #30)

### TD-013 — Extended screen-off survival procedures B/C/E not yet run

- **Description**: Milestone 9B.0 ran Procedure A (20-minute baseline,
  extended informally to 96 minutes) and the Default-battery
  confirmation, but the formal 1-hour (B), multi-hour (C), and
  server-restart-while-asleep (E) procedures from
  `spikes/android-presence/docs/survival-test-protocol.md` were not run
  as separate, dedicated tests.
- **Severity**: Medium
- **Owner**: `spikes/android-presence/` (test protocol), no code owner
  (this is a testing gap)
- **Origin milestone**: Milestone 9B.0
- **Risk**: The 96-minute informal run is strong evidence but was not a
  clean, isolated instance of any single named procedure — a formal
  re-run would remove ambiguity before this evidence is relied on for
  production sizing decisions.
- **Recommended milestone**: Deliberately deferred to become
  production-confidence validation near the end of Milestone 9B, after
  the spike architecture stabilizes (`ARCHITECTURE.md` §18) — not before.
- **Status**: Open, deliberately deferred (not neglected)

### TD-014 — Installed-PWA service-worker cache staleness not re-confirmed as resolved

- **Description**: After frontend fixes with service-worker cache-version
  bumps, the *installed* PWA icon (as opposed to a plain browser tab)
  was not independently re-verified as caught up, after testing switched
  to tab-based verification to keep an earlier session moving.
- **Severity**: Low
- **Owner**: `app/static/sw.js`
- **Origin milestone**: Milestone 8.1
- **Risk**: The underlying code is proven correct by multiple other
  methods; this is specifically about the installed-PWA caching path,
  unconfirmed.
- **Recommended milestone**: Unscheduled; low priority.
- **Status**: Open, disclosed (Known Limitation #42)

---

## Documentation Debt

### TD-015 — SESSION.md and ARCHITECTURE.md's milestone-status language is now stale

- **Description**: As of the Architecture Freeze Validation review,
  `SESSION.md`'s "Current Milestone" section (and its Session Handoff
  paragraph) described Milestone 9B.0 as "in progress, not yet closed" —
  corrected in Milestone 9B.1 (`SESSION.md`'s "Current Milestone" section
  now reflects M9B.0's closure and M9B.1's status). `ARCHITECTURE.md` §18
  has **not** yet been re-checked for the same staleness — this remains
  open for that document specifically.
- **Severity**: Low — purely a staleness issue, not a factual error at
  the time each was written.
- **Owner**: `SESSION.md` (corrected), `ARCHITECTURE.md` (not yet checked)
- **Origin milestone**: Architecture Freeze Validation (identified),
  Milestone 9B.1 (`SESSION.md` corrected)
- **Risk**: Low for `SESSION.md` now; `ARCHITECTURE.md` §18 may still read
  as describing M9B.0 as open.
- **Recommended milestone**: Next documentation pass should re-check
  `ARCHITECTURE.md` §18 specifically.
- **Status**: Partially corrected (`SESSION.md`, Milestone 9B.1);
  `ARCHITECTURE.md` still open

### TD-016 — `ARCHITECTURE.md`'s Conversation management description doesn't name the actual writing module

- **Description**: `ARCHITECTURE.md` §4 attributes conversation
  persistence to "`app/database.py` conversation functions,
  `conversation_id` handshake in `app/main.py`" without naming
  `app/supervisor/supervisor.py` as the actual module that calls
  `save_conversation_message()`. `app/main.py` only handles the ID
  handshake and reads; it does not itself persist messages.
- **Severity**: Low — a clarity gap, not a factual error (nothing in the
  document is technically false), but it could mislead a reader about
  which module to look at first.
- **Owner**: `ARCHITECTURE.md`
- **Origin milestone**: This review
- **Risk**: Low — verified during this same review, so the correct
  answer is now on record even though the document itself is not yet
  edited.
- **Recommended milestone**: Immediate next documentation update.
- **Status**: Open, identified during this review, not yet corrected

### TD-017 — State-machine documentation depth is asymmetric across documents

- **Description**: `SESSION.md` documents the Task and Question state
  machines with full ASCII diagrams; `ARCHITECTURE.md` and the ADR set
  reference these same entities only at the ownership-table level,
  without repeating the diagrams. This is consistent with each
  document's stated scope (`ARCHITECTURE.md` explains *why*, not a full
  restatement of every diagram) but was not a deliberate decision
  documented anywhere — it emerged from how each document was written.
- **Severity**: Low
- **Owner**: `ARCHITECTURE.md`
- **Origin milestone**: This review
- **Risk**: Very low — a reader who wants the Task/Question diagrams
  knows to check `SESSION.md`; this is a stylistic asymmetry, not a
  contradiction.
- **Recommended milestone**: Optional future documentation pass.
- **Status**: Open, identified during this review, low priority

---

## Operational Debt

### TD-004 — Background (Doze-idle) push delivery reliability

- **Description**: Real background push delivery does not reliably work
  on a real tested device (Samsung Galaxy S20 FE) despite every standard
  mitigation (non-zero TTL, `Urgency: high`, battery-unrestricted,
  confirmed not in Samsung's sleeping-apps list, fresh reinstall). The
  server-side pipeline is demonstrably correct; the gap is whether/when
  the OS+browser wakes the service worker while genuinely backgrounded —
  outside Jarvis's control.
- **Severity**: High for any use case depending on background
  notification delivery; the WebSocket-open foreground/recently-active
  path works reliably as a fallback.
- **Owner**: No code owner — a platform limitation, not a Jarvis defect.
- **Origin milestone**: Milestone 7.1
- **Risk**: A user relying on being notified while the phone is
  genuinely idle-backgrounded may simply not be notified, with no
  current mitigation beyond "keep the WebSocket connection alive" (which
  is itself part of the motivation for the Android companion, ADR-002).
- **Recommended milestone**: Partially addressed by Milestone 9B's native
  companion (a persistent foreground service does not have this problem
  the way a backgrounded browser tab does) — not expected to be solved
  for the PWA path itself.
- **Status**: Open, disclosed, platform-imposed (Known Limitation #35)

### TD-018 — No authentication or TLS-by-default

- **Description**: Anyone on the LAN can connect to the WebSocket; no
  user identity or access control exists by default. `JARVIS_API_TOKEN`
  existed as an opt-in gate for push endpoints only, not a general auth
  layer, until Milestone 9B.1 extended the same check to the `/ws`
  handshake (Authorization header — Android only, since browsers cannot
  set custom WebSocket handshake headers), and until Milestone 9B.2
  (ADR-014) added a short-lived signed-token mechanism
  (`POST /api/ws-token` + `?token=`) that works for **both** browser and
  Android, real-browser-validated end-to-end (Playwright, cross-verified
  against the server's own connection log). The *mechanism* asymmetry
  from Milestone 9B.1 is now closed. TLS itself was already mkcert-based
  LAN HTTPS since Milestone 7; this item has always been about the
  *authentication* half, not transport encryption.
- **Severity**: Critical the moment remote/non-LAN connectivity (TD-003)
  is considered; Low under the current strictly-LAN, single-user
  deployment model, which this project has consistently treated as an
  explicit, accepted scope boundary.
- **Owner**: `app/main.py`, `app/integrations/ws_tokens.py`
- **Origin milestone**: Milestone 1 (never addressed since); partially
  addressed Milestone 9B.1 (Android Authorization-header path); mechanism
  gap closed Milestone 9B.2 (ADR-014, signed-token path for all clients)
- **Risk**: Currently accepted risk under the stated deployment model
  (`ARCHITECTURE.md` §12); would become a blocking concern immediately if
  that model changes. **Remaining gap**: when `JARVIS_API_TOKEN` is set,
  the PWA's `POST /api/ws-token` call itself needs a stored credential to
  authenticate with — the PWA has no credential-entry/storage UI. This is
  a *credential-entry* gap now, not a *mechanism* gap — the token flow
  itself works identically for both clients once each has a credential.
  Not yet relevant to the actual current deployment (`JARVIS_API_TOKEN` is
  unset).
- **Recommended milestone**: Must be fully addressed before/alongside M10A
  (Remote Reachability Research) — remote connectivity without this fix
  first would be a real security regression. Building a PWA credential-entry
  UI is the specific remaining piece, needed only once an operator
  actually wants to enable `JARVIS_API_TOKEN`.
- **Status**: Open, substantially addressed (Milestone 9B.2, ADR-014) —
  mechanism symmetry achieved for both clients; PWA credential-entry UI
  remains the one disclosed, unaddressed piece

### TD-019 — Unbounded event/task table growth

- **Description**: No rotation or archiving exists for `events`,
  `tasks`, `questions`, `opencode_tasks`, `attention_requests`,
  `contact_attempts`, `voice_sessions`, `notifications`, or
  `push_subscriptions` — all grow indefinitely.
- **Severity**: Medium — not urgent at current usage volume, but a
  standing operational risk that compounds with every new table added
  (nine and counting).
- **Owner**: `app/database.py`
- **Origin milestone**: Milestone 2 (first noted), carried forward
  through every subsequent milestone that added a new table
- **Risk**: Eventual query/performance degradation and unbounded disk
  usage with no current ceiling or archival strategy.
- **Recommended milestone**: Unscheduled; should be addressed before
  Jarvis is expected to run unattended for very long continuous periods.
- **Status**: Partially resolved (Owner Experience M-OX.1, ADR-020) —
  `events` gained `db.purge_events_older_than(days)`, a concrete,
  callable retention mechanism, deliberately not yet wired to any
  scheduler (that wiring is an operational concern for a later OX
  milestone, not a data-model one). `tasks`, `questions`,
  `opencode_tasks`, `attention_requests`, `contact_attempts`,
  `voice_sessions`, `notifications`, and `push_subscriptions` remain
  entirely unaddressed — this item stays open, not closed.

### TD-020 — `JARVIS_OPENCODE_ALLOW_PAID`'s allowlisted model is hardcoded

- **Description**: `ALLOWED_PAID_OPENCODE_MODEL_ID` is a single
  hardcoded constant — changing which paid model is permitted under the
  emergency override requires a code change, not just a configuration
  change.
- **Severity**: Low — a deliberate friction point (per ADR-009), not an
  oversight, but worth tracking as a real constraint on how quickly the
  override can be adapted if the currently-allowlisted model becomes
  unavailable.
- **Owner**: `app/integrations/opencode_adapter.py`
- **Origin milestone**: Milestone 9B.0
- **Risk**: Low — the override is disabled by default and used rarely;
  this only matters at the moment someone needs to actually use it again
  with a different model.
- **Recommended milestone**: Only if the specific allowlisted model is
  deprecated or a second paid-model use case genuinely arises.
- **Status**: Open, intentional (ADR-009)

### TD-021 — Android companion retries forever against a permanent certificate mismatch

- **Description**: `CompanionWebSocketClient` treats a TOFU
  certificate-fingerprint mismatch (`PinnedTrustManager` rejection) the
  same as any other transient disconnect — it backs off and retries
  indefinitely, never distinguishing it from `WS_AUTH_REJECTED` (close
  code 1008), which is the one failure type currently treated as
  non-retryable-without-user-action. A cert mismatch is exactly as
  permanent (it will never succeed until the user re-pairs), but there is
  no distinct UI state or stopped-retrying signal for it.
- **Severity**: Low — the security property itself is real and confirmed
  enforced (real-device evidence, Milestone 9B.1 Phase 3, item 5B); this
  is a UX/observability gap, not a security gap.
- **Owner**: `android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt`
- **Origin milestone**: Milestone 9B.1 (found during real-device
  acceptance testing, item 5B — the server's TLS cert was regenerated and
  the app correctly rejected every reconnect attempt, but kept retrying
  forever rather than surfacing "re-pairing required")
- **Risk**: A user whose server cert legitimately changes (e.g. mkcert
  re-run) would see the companion silently fail to reconnect forever,
  with no on-screen indication that re-pairing (not waiting) is what's
  needed.
- **Recommended milestone**: Any future Android companion pass; candidate
  fix is to treat a certificate-mismatch failure the same way as
  `WS_AUTH_REJECTED` — stop retrying and surface a distinct state.
- **Status**: **Closed, Milestone 9B.2** (`network.DisconnectReason`/
  `DisconnectClassifier`, `ConnectionState.FAILED_PERMANENT` — see
  ADR-012). Real-device-confirmed: a regenerated server certificate now
  produces exactly one more reconnect attempt (the one already in
  flight), then `WS_PERMANENT_FAILURE reason=CERTIFICATE` and a
  `FAILED_PERMANENT` state with no further `WS_CONNECTING` attempts
  (verified over a 30+ second observation window); the notification text
  changes to "Server certificate changed — re-pair required"; manually
  restarting the companion (Stop → Start) after the correct certificate
  is restored reconnects normally.

### TD-022 — Wake-word background/Doze survival, battery, and adverse-acoustic recall unproven

- **Description**: Milestone 9B.5's D5 spike (`spikes/android-wakeword/`)
  real-device-proved the NDK/CMake + TensorFlow Lite Micro architecture
  builds, loads the model, runs inference, and detects the real wake word
  on the S20 FE — but every test ran with the screen kept awake via
  Developer Options "Stay awake" while charging, deliberately avoiding
  actual screen-lock/Doze conditions. The spike has no foreground service
  or wake lock (unlike the production `PresenceService`); an earlier,
  shorter attempt at the same false-positive test lost its in-memory
  counters the moment Android recreated the backgrounded activity,
  confirming real device evidence that this spike does not currently
  survive ordinary backgrounding. Recall was also only measured from one
  speaker at close range in two room conditions (8/10 non-quiet, 10/10
  quiet) — not against distance, multiple speakers, or genuine background
  noise/conversation. No multi-hour battery-drain figures exist for
  continuous native inference at ~100Hz on this device.
- **Severity**: High for any future production wake-word milestone
  (always-listening is the entire point, so Doze/background survival is
  not optional) — Low for the current state, since no production
  integration exists yet and the spike remains disposable.
- **Owner**: Unowned — no module currently addresses this;
  `spikes/android-wakeword/` is disposable, not a production dependency.
- **Origin milestone**: Milestone 9B.5 (D5 spike, real-device evidence)
- **Risk**: Any future production wake-word milestone that assumes the
  D5 spike's quiet-room/plugged-in results generalize to real always-on
  background operation would be building on REQUIRES-EXPERIMENT-classified
  ground, not FACT — the same category of assumption M9B.0 already found
  and fixed once for `PresenceService` (Samsung Default battery
  optimization silently killing backgrounded work).
- **Recommended milestone**: Any future production wake-word milestone
  must resolve this first, reusing the `PresenceService`
  foreground-service/wake-lock pattern rather than assuming the bare
  spike's behavior carries over.
- **Status**: Open, explicitly disclosed (`SESSION.md` Milestone 9B.5)

---

### TD-023 — Active VoiceSession recovery under real-device failure injection unproven

- **Description**: Milestone 9B.10 validated WebSocket transport reconnect
  on a real device (kill the live backend, confirm the client detects,
  backs off, and reconnects) but that test opened `VoiceActivity` without
  ever speaking, so no VoiceSession was open server-side at the moment of
  the kill. The actual question this milestone exists to answer — does an
  *active* VoiceSession (one with real `listening`/`processing` state on
  both sides) reach a deterministic end-state on both the client and
  server when the backend dies mid-conversation and comes back with its
  in-memory state gone — was not exercised. Three further real-device
  scenarios are also unproven: a client disconnecting mid-conversation
  (not at idle); a killed companion process's `PresenceService`
  `START_STICKY` recovery reconciling correctly with any VoiceSession that
  was open at kill time; and an abandoned client (opened, then walked
  away from) actually being reaped by `VoiceSessionReaper` on the real
  device, as opposed to only in `test_voice_session_reaper.py`.
- **Severity**: Medium — the server-side state machine and reaper logic
  are unit-tested and the transport layer is real-device-proven
  separately, so this is a gap in *combined* real-device evidence, not a
  known-broken path. Escalates to High if a future milestone builds
  additional VoiceSession-dependent features assuming this is settled.
- **Owner**: Unowned — `app/voice_session_manager.py`,
  `app/voice_session_reaper.py`, `PresenceService.kt` are the modules
  whose interaction this would exercise.
- **Origin milestone**: Milestone 9B.10 (real-device finding; deliberately
  not rushed to close per explicit user instruction — each scenario
  needs a real device with an actual conversation in flight, not just a
  launched screen)
- **Risk**: Any claim that Milestone 9B.10's hardening (idle reaper,
  `termination_reason`, the close-during-`process_message` fix) is fully
  proven end-to-end rests on unit tests plus one transport-only device
  test — REQUIRES-EXPERIMENT-classified for the combined real-device
  case, not FACT.
- **Recommended milestone**: Exercise the remaining two scenarios (a
  mid-conversation client disconnect; a killed companion process's
  `START_STICKY` recovery reconciling with an open VoiceSession) in a
  dedicated real-device session.
- **Status**: **Partially resolved, 2026-07-21/22.** The originally-listed
  first scenario (an active VoiceSession's deterministic end-state on
  both sides when the backend dies mid-conversation and comes back with
  its in-memory state gone) was exercised for real: opened a session,
  spoke a real question, killed the backend mid-`listening`/`processing`.
  Found and fixed 3 real defects in the process (#5 WakeWordManager
  audio-focus stuck, #6 the "null" greeting parsing bug, #7 stale-token
  fast-fail) — see `SESSION.md` Milestone 9B.10's continuation entry for
  full detail. The fourth scenario (an abandoned client actually being
  reaped by `VoiceSessionReaper` on the real device) was also confirmed
  working end-to-end during the same real-device session
  (`termination_reason=idle_timeout` observed exactly as designed). The
  remaining two scenarios (mid-conversation disconnect as an isolated,
  deliberate test; process-kill/`START_STICKY` recovery) were not
  specifically exercised — real, unplanned WebSocket disconnects did
  happen repeatedly during the same session's later demo rehearsal and
  were all observed to self-heal correctly, but that is incidental
  evidence, not a controlled test of this scenario, so it is not claimed
  as resolved here.

---

## Summary

| ID | Title | Category | Severity |
|---|---|---|---|
| TD-001 | `push_subscriptions` ownership | Architecture | Medium |
| TD-002 | VoiceSession multi-client lease | Architecture | Resolved (M9B.4) |
| TD-003 | Transport Reachability | Architecture | High (conditional) |
| TD-004 | Background push reliability | Operational | High (conditional) |
| TD-005 | Notification routing ownership | Architecture | Low |
| TD-006 | Permission/Question conflation | Architecture | Medium |
| TD-007 | Title-generation cost-pin gap | Implementation | Low |
| TD-008 | `reconcile_on_startup()` incomplete | Implementation | Low |
| TD-009 | Ownership marker not per-port | Implementation | Low |
| TD-010 | `notification_id` never populated | Implementation | Low |
| TD-011 | No SSE reconnect test | Testing | Medium |
| TD-012 | iOS Safari untested | Testing | Medium |
| TD-013 | Survival procedures B/C/E not run | Testing | Medium |
| TD-014 | Installed-PWA cache staleness | Testing | Low |
| TD-015 | Stale milestone-status language | Documentation | Low |
| TD-016 | Conversation ownership clarity gap | Documentation | Low |
| TD-017 | Asymmetric state-machine documentation depth | Documentation | Low |
| TD-018 | No auth/TLS by default (mechanism symmetry achieved M9B.2; PWA credential-entry UI remains) | Operational | Critical (conditional) |
| TD-019 | Unbounded table growth | Operational | Medium (partially addressed, M-OX.1) |
| TD-020 | Hardcoded paid-model allowlist | Operational | Low |
| TD-021 | Android companion retries forever on cert mismatch | Implementation | **Closed (M9B.2)** |
| TD-022 | Wake-word background/Doze survival, battery, adverse-acoustic recall unproven | Architecture | High (conditional) |
| TD-023 | Active VoiceSession recovery under real-device failure injection unproven | Testing | Medium |
| TD-024 | Control Center event-shape handling relies on an unenforced naming convention | Implementation | Low |
| TD-025 | Control Center's `currentTurn` cannot represent two concurrent turns | Architecture | Low |
| TD-026 | `project_dir` provides no real containment; zero permission gate for sandbox escape | Operational | **Critical** |
| TD-027 | Supervisor tool-calling loop repeats identical calls; doesn't recognize a plain decline | Implementation | High |
| TD-028 | Raw audio capture has no speaker isolation; any nearby voice is transcribed as the user | Architecture | High |
| TD-029 | Silence detection fails under ordinary ambient noise (e.g. a room fan), silently losing the recording | Architecture | **Critical** |

No duplicate entries exist between this register and `SESSION.md`'s own
"Known Bugs, Limitations, and Technical Debt" section — this register is
a curated subset focused specifically on structural/architectural debt
requiring deliberate future scheduling; `SESSION.md` remains the
complete, chronological record of every disclosed limitation.
