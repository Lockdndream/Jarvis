# ADR-015: Android Attention Widget

## Status

Accepted

## Date

2026-07-13 (Milestone 9B.3)

## Context

Milestones 9B.1/9B.2 built the Android companion's presence/transport
foundation (pairing, reconnect, telemetry, diagnostics) but deliberately
excluded every user-facing capability. Milestone 9B.3 is the first to
build one: a home-screen widget. ADR-002 (Hybrid Android Architecture)
already named a home-screen widget as one of exactly four capabilities
categorically unavailable to the PWA — this ADR is that decision's first
concrete implementation, not a new justification for it.

The server already broadcasts everything a client needs to render
outstanding attention state: `pending_attention` (sent once per connect,
full snapshot) and `attention_created`/`attention_contacting`/
`attention_pending`/`attention_deferred`/`attention_resolving`/
`attention_resolved`/`attention_cancelled`/`attention_expired` (live,
per-transition). The PWA already consumes exactly these messages for its
"Jarvis is calling" card (Milestone 8) — Talk now / snooze / dismiss.
`user_message` already accepts an optional `bound_attention_request_id`
field, and the PWA's own "dismiss" button already just sends
`{"type":"user_message","content":"later","bound_attention_request_id":id}`
through the natural-language deferral parser — no dedicated "dismiss"
message type exists or is needed.

## Problem

How does an Android home-screen widget show outstanding attention state
and offer real (not decorative) quick actions, without inventing a
second server-state-synchronization mechanism, without performing any
reasoning on-device, and without requiring voice infrastructure that
doesn't exist yet on this client?

## Decision

**The widget is a read-only rendering of a client-side mirror
(`AttentionRepository`) fed exclusively by the already-open WebSocket
connection's existing message types — no new protocol, no polling beyond
Android's own minimum periodic widget refresh as a relative-timestamp
safety net.**

- `android/.../attention/AttentionRequest.kt`: data class mirroring the
  server's shape (`attentionRequestId`, `attentionType`, `status`,
  `summary`, `taskId`, `deferredUntil`, plus nullable `urgency`/
  `conversationId` — present in `pending_attention`'s initial snapshot,
  absent from the live `attention_*` broadcast events; a real,
  pre-existing shape asymmetry disclosed here, not one this ADR
  introduces or needs to fix).
- `android/.../attention/AttentionParser.kt`: parses `pending_attention`
  (array) and individual `attention_*` frames into `AttentionRequest`
  values or update events. Uses `org.json` (safe at runtime on a real
  device; the well-known Android-stub-in-local-JVM-tests problem is
  worked around with a real `org.json:json` test dependency, not by
  avoiding the library — unlike `DeviceStatus`/`ws_tokens` payloads,
  these messages have genuine nested/array structure where hand-rolled
  string parsing would be a real correctness risk, not a reasonable
  simplification).
- `android/.../attention/AttentionRepository.kt`: in-memory, `StateFlow`-backed
  mirror of currently-outstanding (non-terminal-status) requests plus the
  most recent event's timestamp ("last contact time"). Fed exclusively by
  `CompanionWebSocketClient`'s message parsing — this is a rendering
  cache, not a second source of truth; the server remains sole authority
  and this mirror is rebuilt from scratch on every reconnect (via
  `pending_attention`), never assumed durable across a connection gap.
- `CompanionWebSocketClient` gains `sendAttentionCommand(attentionRequestId, phrase)`
  — sends exactly the same `user_message`/`bound_attention_request_id`
  shape the PWA's dismiss button already sends. No new server-side
  message type. "Dismiss" in the widget/app calls this with `"later"`,
  identical to the PWA's own dismiss phrase.
- **"Talk Now" does not open a voice session.** Real voice
  infrastructure doesn't exist on this client (explicitly out of scope
  for this milestone and several before it). It opens the app to
  `AttentionActivity`, focused on the relevant item — the same
  destination "Open Jarvis" and tapping the widget body reach. This is a
  deliberate, disclosed scope reduction from what "Talk Now" implies on
  the PWA, not an oversight.
- `AttentionWidgetProvider` (`AppWidgetProvider`) renders a summary (
  connection state, outstanding count + the single most urgent/most
  recent item's text, last-contact relative time) via `RemoteViews` —
  not a scrollable list (would require a `RemoteViewsService` adapter,
  materially more machinery than a first widget needs; tapping the
  summary opens `AttentionActivity` for the full list).
- **Update triggering**: `PresenceService`, which already owns the live
  `CompanionWebSocketClient` and already parses every inbound frame,
  pushes a widget update (`AppWidgetManager.updateAppWidget`) the moment
  `AttentionRepository` changes — no separate polling loop. The widget's
  own `updatePeriodMillis` (Android's OS-enforced minimum: 30 minutes) is
  kept only as a relative-timestamp freshness safety net, not the primary
  update path.

## Alternatives Considered

**A `RemoteViewsService`-backed scrollable list widget.** Rejected for
this first widget: real, standard Android machinery, but meaningfully
more complex than a single-item summary + "open the app for the rest,"
and the milestone's own framing ("first production widget," "avoid
unnecessary abstraction" as this project's standing practice) doesn't
justify it yet. Revisit if outstanding-item volume in practice makes a
single-item summary insufficient.

**A second REST/WS polling mechanism dedicated to the widget.** Rejected,
explicitly, by the milestone's own requirement ("do not invent a second
synchronization mechanism") — the existing `/ws` broadcast stream already
carries everything needed.

**"Talk Now" opens the PWA in a browser (which does have real voice).**
Considered, since it would give "Talk Now" real functionality today.
Rejected: cross-app handoff from a widget to a browser tab is a
confusing, inconsistent UX for a "first widget" milestone, and blurs the
"Android companion remains an interaction client, not a second reasoning
surface" boundary this project has held since ADR-001/ADR-002 in a way
that's hard to reason about later. Revisit once the Android companion has
its own voice capability (a distinct, separately-gated future milestone)
— "Talk Now" should then open a real bound voice session, matching the
PWA exactly.

**Store `AttentionRepository`'s state in `SharedPreferences`/a local DB
for durability across process death.** Rejected: the repository is
explicitly a rendering cache of live server state, not durable
client-owned data — rebuilding it from `pending_attention` on every
reconnect is correct and sufficient, and persisting a stale mirror across
a process restart risks showing outdated attention state as if current.

## Consequences

The widget can only show what the current, live WebSocket connection has
actually told it — if the companion is disconnected, the widget shows
that fact rather than stale attention data pretending to be current. This
is a deliberate consequence of "no second synchronization mechanism," not
a gap: the connection-state field is always shown alongside attention
data specifically so a stale/disconnected widget is never mistaken for a
live one.

## Positive Outcomes

Filled in after implementation and real-device validation (S20 FE) — see
Milestone 9B.3's SESSION.md entry for the full narrative.

- The "client-side mirror, server remains sole authority" design held up
  under real testing: a real `AttentionRequest` inserted directly into the
  server's database, a real WebSocket reconnect, and the widget rendered
  the exact real summary text — confirmed by screenshot, not just
  telemetry.
- Reusing the PWA's exact `bound_attention_request_id`/`user_message`
  dismiss mechanism required zero server-side protocol changes, as
  planned — `ATTENTION_COMMAND_SENT` telemetry confirmed the widget sends
  byte-identical intent to what the PWA's own dismiss button sends.
- The "never assume a message will arrive" caution this ADR's Consequences
  section already flagged turned out to be load-bearing, not
  theoretical: `app/main.py` only sends `pending_attention` when at least
  one item is unresolved (`if pending_attention:`) — real-device testing
  caught a genuine staleness gap this caused (a resolved item's stale
  widget display never clearing after a reconnect with nothing left
  pending) before it could reach production. Fixed entirely client-side
  (`CompanionWebSocketClient.onOpen()` unconditionally resets the mirror
  before any frame is processed), honoring this ADR's commitment that
  `app/main.py` stays unchanged.
- Push-driven updates via `PresenceService` worked as designed with one
  real ordering bug: the widget-refresh trigger fired from the same
  `connectionState` transition that establishes `lastContactAtMs`,
  and the two were briefly in the wrong order, causing an occasional
  stale "never" render on first reconnect — fixed by reordering, not by
  adding synchronization machinery.
- The independent-Reviewer pipeline (ADR-013) caught a real,
  well-documented Android bug in the delegated widget code
  (`PendingIntent` missing `FLAG_UPDATE_CURRENT`, which would have made
  Dismiss silently act on a stale item after the widget's first render)
  before it reached a device — verified against real `PendingIntent`
  semantics, then re-confirmed on real hardware with two outstanding
  items.

## Tradeoffs

- No attention data survives a full app-process restart until the next
  successful reconnect's `pending_attention` snapshot arrives — a widget
  briefly shows stale-but-labeled-disconnected state after, e.g., a
  device reboot (consistent with the already-accepted no-`BOOT_COMPLETED`-receiver
  limitation from Milestone 9B.1).
- "Talk Now"'s reduced scope (opens the app, doesn't start voice) may
  read as a missing feature rather than a deliberate one without this
  ADR's context — the in-app screen and widget copy should make clear
  what tapping it actually does.

## Future Revisit Conditions

Revisit the single-item-summary widget layout if real usage shows
multiple simultaneous outstanding items are common enough that a
scrollable `RemoteViewsService` widget becomes worth the added
complexity. Revisit "Talk Now" the moment Android-native voice
infrastructure is built (a separate, explicitly-gated future milestone) —
at that point it should open a real bound voice session exactly like the
PWA's equivalent button.

## References

- `ARCHITECTURE.md` §9 (Android Companion)
- `SESSION.md`, Milestone 8 (`AttentionRequest`/call-style PWA UI, the
  mechanism this ADR reuses), Milestone 9B.1/9B.2 (companion foundation),
  Milestone 9B.3 (this decision)
- ADR-002 (Hybrid Android Architecture — the widget's original
  justification), ADR-012 (Device State-Sync Protocol — the same
  "client-side mirror, server remains sole authority" pattern this ADR
  follows)

## Related Milestones

Milestone 8, Milestone 9B.1, Milestone 9B.2, Milestone 9B.3

## Related Source Files

- `android/app/src/main/java/com/jarvis/companion/attention/`
  (`AttentionRequest.kt`, `AttentionParser.kt`, `AttentionRepository.kt`)
- `android/app/src/main/java/com/jarvis/companion/widget/AttentionWidgetProvider.kt`
- `android/app/src/main/java/com/jarvis/companion/ui/AttentionActivity.kt`
- `android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt`
  (`sendAttentionCommand`)
- `android/app/src/main/java/com/jarvis/companion/service/PresenceService.kt`
- `app/attention_manager.py`, `app/main.py` (unchanged — reused as-is)
- `app/static/app.js` (the PWA's equivalent mechanism this ADR mirrors)
