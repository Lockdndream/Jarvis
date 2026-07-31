# ADR-030: Interaction Layer — Conversation Transparency and Permission Resolution

## Status

Accepted

## Date

2026-07-31

## Context

Before this milestone, the Android companion's voice interface was a
one-shot request/response: speak, wait, hear `voice_session_response`.
Nothing showed what was heard, nothing showed what the Supervisor was
doing between hearing and answering, and a `PERMISSION`-type
`AttentionRequest` (ADR-003) had no structured way to be resolved from
the phone — the only path was speaking or typing a decision as an
ordinary transcript and hoping `_resolve_deterministic_command`'s
grammar caught it.

Step 0 (deepseek-v4-flash, read-only mapping) confirmed the gap was
exactly this: transcript delivery, thinking/action visibility, and
permission surfacing were all absent, while the underlying mechanisms
they'd need (the AttentionRequest lifecycle, `_resolve_bound_command`,
the WebSocket connection itself) already existed and needed no redesign.

## Decision

**Four additive server-side message types, an Android conversation data
layer, and a RecyclerView-based UI — reusing existing resolution paths
rather than inventing new ones.**

### Server: three new message types, one deliberate omission

- `conversation_turn` (S→C) — emitted between STT resolution and
  supervisor invocation, so the phone can render "here's what Jarvis
  heard" before the answer arrives. `conversation_id` may be `null` on
  a session's first turn, before `_process_transcript` assigns one — a
  known v1 limitation, not fixed here.
- `thinking_update` (S→C) — emitted around every tool call in
  `Supervisor`'s loop (`supervisor.py`), `status: "started"` before the
  call and `status: "completed"` after, with a bounded (≤200-char)
  `detail` synopsis on completion — never raw `args` or an untruncated
  result. Sent via a new `_broadcast_phone()` fan-out, deliberately
  separate from the existing dashboard-only `supervisor_tool_call`
  broadcast: the dashboard's channel is a DB-backed, observer-gated
  audit trail (ADR-018/019); the phone's is a best-effort, no-persistence
  transparency signal. Conflating them would have made the read-only
  Control Center's write path implicitly load-bearing for phone UX.
- `permission_response` (C→S) / `permission_response_ack` (S→C) — the
  phone sends `{attention_request_id, decision: "approve"|"reject"}`;
  the server routes `decision` through the **existing**
  `_resolve_bound_command` path (`bound_attention_request_id` set,
  same PERMISSION-type grammar voice transcripts already use) and
  replies with an ack carrying the result text. No new resolution
  logic — this is a structured entry point onto a path that already
  worked for voice/text.
- **Deliberate omission**: no new `permission_request` push type. A
  PERMISSION-type `AttentionRequest`'s existing `attention_created`
  broadcast already carries `attention_request_id` and a summary;
  adding a second, differently-shaped push for the same event would
  put two representations of one event on the wire for no gain.

Full shapes are in `docs/protocols/websocket-protocol-v1.md` §2.2.2.

### Android: a parallel data layer feeding one adapter

`ConversationMessage`/`ConversationParser`/`ConversationRepository`
(new `conversation/` package) turn the three new frame types (plus the
existing `voice_session_response`) into one ordered, observable list;
`ConversationViewMapper` renders it; `ConversationAdapter` is a
`ListAdapter` with `submitList`'s async diffing, driving two view types
in `activity_voice.xml`'s conversation RecyclerView — a chat bubble row
and a system/thinking row, the latter growing Approve/Reject buttons
when it represents an unresolved PERMISSION request.

## Real-device findings (Step 5)

Five scenarios were run against the S20 FE over WiFi ADB: transcript
verification, tool-call transparency, permission flow, multi-turn
continuity, and disconnect/reconnect resilience. Four real defects
surfaced and were fixed; three were documented as new tech debt without
being fixed under time pressure, matching the precedent ADR-029 set for
TD-026.

### Fixed

1. **`attention_created` broadcast silently dropped by the phone.**
   `attention_manager.py`'s `initiate_contact()` broadcasts
   `attention_created` without a `status` field; `AttentionParser.kt`'s
   `parseAttentionEvent()` requires a non-empty `status` or returns
   `null` — the *entire frame*, not just one field. No PERMISSION_REQUEST
   row ever appeared on the phone, for any permission request, since
   before this milestone existed (permission requests essentially never
   fired before Step 4). Fixed by adding `"status": STATUS_PENDING` to
   the broadcast. Neither side's tests caught this beforehand: every
   existing Kotlin parser test hand-crafted its own JSON with the field
   present, and the Python side asserted nothing about the broadcast's
   shape. Regression tests added on both sides — one asserting the
   Python broadcast includes `status`, two on the Kotlin side asserting
   the parser rejects the field's absence and accepts the exact
   post-fix payload shape `initiate_contact` actually sends.
2. **Wrong OpenCode permission-reply body → live 400.** The user tapped
   Approve; the tap correctly reached the server and the correct
   AttentionRequest resolved, but the underlying OpenCode call failed.
   `opencode_adapter.py`'s `reply_permission()` sent
   `{"approved": bool}`; OpenCode's real API (confirmed via its live
   `GET /doc` OpenAPI schema) requires
   `{"reply": "once"|"always"|"reject"}`, rejecting anything else at
   400 (`additionalProperties: false`). Fixed by mapping approved→`"once"`
   (not `"always"` — a single tap should not silently pre-approve future
   requests of the same kind) and rejected→`"reject"`. `FakeOpenCodeServer`
   in `tests/test_opencode.py` previously accepted any request body
   unconditionally on this endpoint — zero real coverage against the
   live contract. It now validates the same shape OpenCode's real API
   enforces, and a new test (`test_adapter_reply_permission_reject_sends_reject`)
   covers the reject path, which nothing exercised before.
3. **Illegible thinking/system rows.** `?attr/textAppearanceBodySmall`
   and `Widget.Material3.Button.TonalButton` were used in
   `item_conversation_system.xml`, but this app's actual theme is
   `Theme.MaterialComponents.DayNight.NoActionBar` — confirmed in
   `themes.xml` — not Material3. Material3-only attributes/styles under
   a MaterialComponents theme don't reliably fail; they can render
   illegibly (dark, tiny text, as the user found) or silently resolve
   to something wrong, and nothing in this project's test suite
   (no Robolectric/Espresso) would have caught it — only a real device
   surfaces this class of bug. Fixed with explicit `textSize="14sp"`,
   `textColor="?android:attr/textColorSecondary"`, and
   `Widget.MaterialComponents.Button.OutlinedButton` in place of the
   Material3 style, the latter fixed proactively before it could cause
   a second, separate failure once Approve/Reject was actually tested.
4. **Approve/Reject buttons behind long summary text.** The system row
   was a `FrameLayout` with the buttons as a layered sibling of the
   summary `TextView`; a long enough summary put the buttons underneath
   it in z-order, so a tap landed on the text view instead and the
   `permission_response` was silently never sent. Fixed by restructuring
   the root to a vertical `LinearLayout`, giving the buttons guaranteed
   space below the text regardless of its length.

Two additional fixes, not defects but UX findings the user raised live:
long chat bubbles lost start/end distinguishability once wide enough to
fill the screen (fixed with `maxWidth="280dp"` plus a persistent
`colorPrimary` stroke on the user side — a border survives any bubble
width, unlike relying on alignment or fill color alone); all chat and
system-row text was made `textIsSelectable="true"` per explicit
request.

### Documented, not fixed

Each found live, fully reproduced, and not patched mid-step — explicit
user decision, matching the precedent ADR-029 set for TD-026.

- **TD-027** (High) — the Supervisor's tool-calling loop repeated
  identical tool calls (each of 6 tools called twice) hitting
  `MAX_TOOL_CALLS` with no real answer to a simple informational
  question, and a plain conversational decline ("No, that's all, thank
  you.") triggered the identical 6-tool sequence a third time rather
  than being recognized as needing no tool call at all. Found only
  because `thinking_update` (this milestone's own feature) made the
  Supervisor's behavior newly visible — the user's own words: "I can
  visibly tell that it's performing actions, and that's how I know that
  it's not performing the right actions now." Needs dedicated
  investigation into `SYSTEM_PROMPT` deduplication and
  `_resolve_deterministic_command`'s decline grammar, not a same-pass
  patch.
- **TD-028** (High) — raw audio capture has no speaker isolation; a
  transcript captured a phrase the user never said, blended in from
  ambient/second-party speech. Unscheduled pending investigation into
  voice-print matching, proximity gating, or transcript-confirmation
  as a lighter mitigation.
- **TD-029** (**Critical**, upgraded from High after a third live
  reproduction) — end-of-utterance/silence detection uses a flat RMS
  amplitude threshold that cannot distinguish speech from steady-state
  ambient noise, so a session can sit in `listening` indefinitely and
  silently lose the entire utterance, including safety-relevant
  commands like "cancel it". Reproduced three times, the last explicitly
  under a running room fan. Per the user's own explicit direction, given
  verbatim: *"The flat RMS threshold needs to be replaced with a proper
  VAD that distinguishes speech from steady-state ambient noise. That's
  a focused piece of work, not a research project."* Recorded in
  `docs/TECHNICAL_DEBT.md` as the first fix after this sprint closes.

### Permission-flow verification: what is and is not proven

The button mechanism is proven by construction and by a real completed
task: the layout-overlap fix is confirmed visually, the
`permission_response`/`reply_permission` JSON construction is covered
by unit tests on both ends, and a live PERMISSION request was resolved
end-to-end — the underlying OpenCode task reached `status: completed`
after `reply_permission`'s fix. **What was not independently isolated
is a button tap specifically, with voice ruled out as the resolution
path** — every later resolution attempt in this session raced against
TD-029 actively breaking the voice session mid-scenario, and chasing a
voice-free repro under that condition was judged to be fighting the
test environment rather than validating the feature (explicit user
call). This is accepted as sufficient evidence for this milestone, not
represented as a fully isolated device-confirmed button tap.

## Alternatives Considered

**Routing `thinking_update` through the existing `supervisor_tool_call`
dashboard broadcast instead of a new phone-facing fan-out.** Rejected —
that channel is DB-backed and observer-gated (ADR-018/019); making it
also responsible for phone-visible real-time transparency would couple
a read-only audit mechanism to interactive UX it wasn't designed for.
`_broadcast_phone()` is a separate, best-effort, no-persistence path.

**A `permission_request` push type mirroring `attention_created`'s
payload.** Rejected — `attention_created` already reaches the phone
with everything a permission UI needs; a second push for the same
event is redundant wire traffic representing one thing twice.

**Full VAD implementation as part of this milestone**, since TD-029 was
found here. Rejected — explicit user decision. It's Critical and
prioritized as the very next piece of work, but is a distinct,
non-trivial change to `SpeechInputController`/`AndroidAudioCaptureEngine`
that deserves its own focused pass, not a rushed addition to a sprint
already at Step 5 of 6.

**Fixing TD-027 (redundant tool calls) mid-Step-5**, since a live
reproduction was already in hand. Rejected — explicit user decision:
"Document only, fix later," matching how TD-026 was handled in ADR-029.

## Consequences

### Positive Outcomes

- The phone now shows what Jarvis heard, what it's doing while
  answering, and can resolve a permission request with a single tap —
  closing the three gaps Step 0's mapping identified as missing.
- Four real defects were found and fixed specifically because this
  milestone insisted on real-device validation rather than stopping at
  unit tests: a silently-dropped broadcast, a live 400 against
  OpenCode's actual API, an illegible UI element, and a functionally
  broken button — none of which any existing test suite would have
  caught, by construction (no Robolectric/Espresso, no shape assertion
  on the fake OpenCode server, hand-crafted rather than real-payload
  parser test fixtures).
- Two of those fixes closed real testing-strategy gaps, not just the
  bugs themselves: `FakeOpenCodeServer` now enforces the real reply
  schema, and the Kotlin parser test suite now includes a fixture that
  matches the server's actual broadcast payload field-for-field.
- 913 pytest passed, 349 Android unit tests passed, ruff clean.

### Tradeoffs

- `conversation_id` can be `null` on a voice session's first
  `conversation_turn` frame, before `_process_transcript` assigns one —
  a known v1 limitation, not fixed.
- The permission-flow button mechanism is validated by code review,
  unit tests, and one real end-to-end resolved task — not by an
  isolated real device tap independently proven free of any voice-path
  involvement (see above).
- TD-027, TD-028, and TD-029 are documented, not fixed, by explicit
  decision — TD-029 in particular means voice interaction remains
  unreliable under ordinary ambient noise until the very next piece of
  work lands.
- `ConversationRepository` is cleared on disconnect — confirmed real by
  the user's own experience during the disconnect/reconnect scenario:
  the visible transcript is not a continuous, persisted session history.

## Non-Goals (explicit, this milestone)

- **Fixing TD-027, TD-028, or TD-029.** All three were found here and
  documented with full reproduction detail; none were fixed under this
  milestone's time pressure, per explicit user decision on each.
- **Real-time (word-by-word) transcript streaming.** The user asked for
  this directly ("if I can have it real time, it would be amazing") —
  noted as a future product direction, not built. `conversation_turn`
  delivers the full resolved transcript after STT completes, not a
  live partial stream.
- **Persistent, navigable per-conversation chat history.** The user's
  own framing: some way to track and refer back to a previous
  conversation, "like... how all the other AI applications are doing."
  A distinct, larger product idea surfaced by the disconnect/reconnect
  scenario, not addressed by this milestone's `ConversationRepository`
  (which is explicitly a live, in-memory, per-session view).
- **A supplementary text-input interaction mode.** The user requested
  this as a future addition for situations where speaking aloud isn't
  desirable (public settings, personal topics) — noted, not built; the
  Interaction Layer's UI in this milestone is voice-transcript display
  and permission buttons only, no text-entry affordance.
- **A drawer-style permission UI.** The user suggested a drawer showing
  the full permission question as an alternative to inline buttons if
  ever needed for a longer request — noted as a future UX candidate,
  not built; the inline stacked-layout fix was judged sufficient for
  now.
- **Collapsing `thinking_update`'s STARTED/COMPLETED pair into a single
  row per tool call.** Each transitions the same system row's content
  in the Android UI today; a cleaner single-row-updates-in-place
  treatment was not pursued this milestone.
- **Plan-step-level `thinking_update` events.** Only individual tool
  calls inside `Supervisor`'s own loop emit `thinking_update` — a
  dispatched plan step (ADR-027) does not, by design for this version.

## Future Revisit Conditions

- TD-029 is fixed (VAD replacing the flat RMS threshold) — the highest-
  priority follow-up, explicitly ordered as the very next piece of work
  after this sprint closes.
- TD-027 or TD-028 get a dedicated investigation pass with a concrete
  design in hand, rather than remaining documented-only.
- A future milestone wants continuous voice-session identity across
  reconnects, or persistent per-conversation history — both raised live
  by the user during Step 5 — revisit `ConversationRepository`'s
  disconnect-clears-state behavior and the database's conversation
  boundary model together, since they're the same underlying gap seen
  from two angles.
- Real-time partial transcript streaming becomes a priority — revisit
  whether Groq's streaming API (if available) or a client-side partial-
  result path is the right mechanism; this milestone did not
  investigate either.

## References

- `CLAUDE.md` (Supervisor routes, workers execute — ADR-005, untouched;
  evidence over inference — ADR-010, the standard Step 5's verification
  was held to).
- `docs/decisions/ADR-003-deterministic-attention-architecture.md`
  (the AttentionRequest/PERMISSION lifecycle this milestone's
  `permission_response` reuses rather than replaces).
- `docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`
  and `ADR-019-separation-of-observability-and-operations.md` (why
  `thinking_update` is a separate phone-facing broadcast rather than
  reusing the dashboard's `supervisor_tool_call` channel).
- `docs/decisions/ADR-020-trace-id-execution-correlation-model.md`
  (`trace_id` threading, reused as-is for `thinking_update` and the
  enriched `opencode_supervisor.py` broadcasts in this milestone).
- `docs/decisions/ADR-029-walk-away-mode.md` (the precedent for
  documenting a real-device-discovered defect as tech debt rather than
  fixing it mid-milestone under time pressure — followed here for
  TD-027/028/029 exactly as it was for TD-026).
- `docs/protocols/websocket-protocol-v1.md` §2.2.2 (full message shapes
  for `conversation_turn`, `thinking_update`, `permission_response`,
  `permission_response_ack`).
- `docs/TECHNICAL_DEBT.md` TD-027, TD-028, TD-029 (full reproduction
  detail for each, not repeated here).

## Related Milestones

Interaction Layer, Steps 0-6 (this milestone) — Android native
conversation UI plus server-side transparency and permission-resolution
features, following the walk-away mode milestone (ADR-029).

## Related Source Files

- `app/main.py` (`conversation_turn` emission, `permission_response`/
  `permission_response_ack` WebSocket handling)
- `app/supervisor/supervisor.py` (`_broadcast_phone`, `thinking_update`
  emission around both tool-calling loop bodies)
- `app/attention_manager.py` (`initiate_contact`'s `attention_created`
  `status` field fix)
- `app/integrations/opencode_adapter.py` (`reply_permission`'s corrected
  request body)
- `app/integrations/opencode_supervisor.py` (`trace_id` threaded into
  task-lifecycle broadcasts)
- `tests/test_opencode.py` (`FakeOpenCodeServer`'s reply-shape
  validation, `test_adapter_reply_permission_reject_sends_reject`)
- `tests/test_attention_manager.py`
  (`test_attention_created_broadcast_includes_status`)
- `tests/test_interaction_layer_step1.py`
- `android/app/src/main/java/com/jarvis/companion/conversation/`
  (`ConversationMessage`, `ConversationParser`, `ConversationRepository`,
  `ConversationViewMapper`)
- `android/app/src/main/java/com/jarvis/companion/ui/ConversationAdapter.kt`
- `android/app/src/main/res/layout/item_conversation_chat.xml`,
  `item_conversation_system.xml`, `activity_voice.xml`
- `android/app/src/test/java/com/jarvis/companion/attention/AttentionParserTest.kt`
  (status-required regression tests)
- `docs/protocols/websocket-protocol-v1.md`
- `docs/TECHNICAL_DEBT.md` (TD-027, TD-028, TD-029)
