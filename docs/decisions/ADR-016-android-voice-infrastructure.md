# ADR-016: Android Voice Infrastructure

## Status

Accepted

## Date

2026-07-14 (Milestone 9B.4)

## Context

Milestone 9B.3 gave the Android companion its first user-facing capability
(a home-screen attention widget) but explicitly excluded voice — "Talk Now"
and "Open Jarvis" both just opened the app, not a voice session (ADR-015's
disclosed scope reduction). ADR-007 (VoiceSession Ownership) already
documented a standing precondition: **the multi-client ownership guard
(TD-002) must be implemented before a second voice-capable client connects
simultaneously with the PWA.** This milestone is exactly that trigger — the
Android companion gains real voice capability for the first time — so
closing TD-002 is required infrastructure, not optional cleanup.

This milestone is explicitly *not* about wake-word detection, Bluetooth
routing, or background microphone capture — those remain scoped to
Milestone 9B.5 and beyond. What real microphone capture belongs in this
milestone was a genuine open question in the brief (it excludes "microphone
processing" under the audio-focus requirement but doesn't otherwise specify
how a transcript reaches the server); the project owner explicitly
confirmed foreground, user-initiated (tap-to-talk) speech recognition is in
scope, mirroring the PWA's own tap-to-talk interaction model exactly —
continuous listening, background capture, and wake word all remain out of
scope.

## Problem

How does the Android companion become a real client of the existing,
server-owned `VoiceSession` architecture — session lifecycle, audio focus,
spoken-response playback, foreground speech input, and a production UI
screen — without introducing a second `VoiceSession` authority, without
duplicating any Supervisor/attention reasoning, and without the two
now-simultaneously-voice-capable clients (PWA + Android) racing to control
the same session?

## Decision

**Android becomes a thin client of the unchanged server-owned `VoiceSession`
protocol (`voice_session_open/transcript/close` ⇄
`voice_session_opened/response/error/closed/invitation`), mirroring the
exact `attention/` module pattern ADR-015 established: a data model + a
defensive JSON parser + a `StateFlow`-backed repository, fed by
`CompanionWebSocketClient`'s existing frame-sniffing dispatch.**

- **Server-side precondition — TD-002 lease** (`app/voice_session_manager.py`,
  `app/attention_manager.py`, `app/database.py`): a nullable
  `attention_requests.active_voice_session_id` column, claimed atomically
  (set-if-null, same conditional-UPDATE pattern as
  `transition_attention_status()`) by `open_session()` when binding to an
  `AttentionRequest`. A second client's attempt to bind the same
  `AttentionRequest` raises `VoiceSessionError` instead of silently
  creating an uncoordinated second session. Released on
  `close_session()`/`fail_session()`; `app/main.py`'s WebSocket handler now
  also closes any still-open session for a connection in a `finally` block,
  so an abrupt disconnect can never hold the lease forever.
- **`android/.../voice/VoiceSession.kt`, `VoiceSessionParser.kt`,
  `VoiceSessionRepository.kt`**: mirror `attention/`'s exact three-file
  shape. The repository resets `lastResponse` on every `applyOpened()` (a
  real gap found in review — without it, a previous session's leftover
  response text could appear to belong to a brand-new session) and applies
  the same stale-session-id discipline to `applyClosed()`/`applyError()`
  that `applyResponse()` already had (also found in review, not present in
  the original delegated implementation).
- **`android/.../audio/AudioFocusManager.kt`**: owns `AudioFocusRequest`
  (gain/loss/duck/transient), headset-plug observation, and current-route
  reporting. Explicitly observation/control only — no custom Bluetooth SCO
  logic, no microphone involvement whatsoever.
- **`android/.../voice/PlaybackManager.kt`**: wraps Android's standard
  `TextToSpeech` (no custom engine, per explicit milestone constraint) —
  queue, cancel, priority, volume, and focus request/abandon around
  playback. Depends on a small `AudioFocusOwner` interface, not the
  concrete `AudioFocusManager` class, so the two components (built in
  parallel by independent delegated Builders) never needed a hard file-level
  dependency on each other.
- **`android/.../voice/SpeechInputController.kt`**: wraps
  `android.speech.SpeechRecognizer` for foreground, tap-to-talk capture
  only. One tap = one recognition attempt; the recognizer is destroyed
  immediately on result, error, or explicit cancel — never left listening.
  No transcript interpretation of any kind.
- **`android/.../ui/VoiceActivity.kt`**: the production voice screen.
  Displays current session status (mapped from the server's more granular
  internal states to five user-facing labels: listening/speaking/waiting/
  finished/error — the server never actually reports "speaking", see
  Consequences), the latest response text (and a session's `greeting`
  before any turn response exists), and a mic button. `AudioFocusManager`/
  `PlaybackManager`/`SpeechInputController` are owned by this Activity's
  own lifecycle, not `PresenceService` — `PresenceService` is
  presence/transport only, per its own standing doc comment, and audio/UI
  resources have no business living in a background service. Rotation and
  process recreation are handled for free by `voiceSessionRepository`
  being Application-scoped, the same mechanism `attentionRepository` already
  used.
- **Diagnostics extension**: `DiagnosticsSnapshot.VoiceDiagnostics` +
  `DiagnosticsRepository.buildVoiceDiagnostics()` (a pure function, no
  dependency on the `voice`/`audio` packages) + a same-process static
  holder on `VoiceActivity` (`activeAudioFocusManager`/
  `activePlaybackManager`/`activeSpeechInputController`, mirroring
  `PresenceService.activeClient`'s exact rationale) so the Diagnostics
  screen can show live values while the voice screen is open and "n/a"
  when it isn't — real-device-confirmed for the "n/a" path; the live-value
  path is code-reviewed but not separately screenshotted (see Known
  Limitations).

## Alternatives Considered

**Let `PresenceService` own `AudioFocusManager`/`PlaybackManager` (a
background-service-owned singleton, like the WebSocket client).** Rejected:
audio focus and TTS are inherently foreground, UI-adjacent concerns tied to
an active voice screen, not to the persistent background presence
connection. `PresenceService`'s own doc comment already states it is
presence/transport only — giving it audio/UI resources would be exactly the
kind of scope creep that comment exists to prevent.

**Skip the TD-002 lease this milestone, since the milestone brief's six
numbered requirements don't mention it.** Rejected: ADR-007 already
documented this as a hard precondition ("must be revisited... before a
second client... is ever connected and voice-capable simultaneously with
the PWA. This is not a someday-maybe item"), and this milestone is exactly
that trigger event. Treating it as required infrastructure for Requirement
1 (VoiceSession client), not as new scope, is the correct reading —
confirmed against the STOP condition ("VoiceSession ownership changes"):
ownership stays server-side and unchanged; this is a concurrency guard
*within* that ownership, exactly as ADR-007 already designed.

**No real microphone capture this milestone — a typed-text placeholder
only, deferring all speech input to the wake-word milestone.** This was
Claude's own initial, more conservative reading of the brief (the brief
excludes "microphone processing" and doesn't list speech-to-text among the
six numbered requirements). Superseded by explicit project-owner
confirmation: foreground, user-initiated speech recognition is in scope;
only background/continuous/wake-word capture is excluded.

**Wire "Talk Now" (the M9B.3 widget/attention-card action) to actually open
a bound `VoiceActivity` now that voice capability exists — ADR-015's own
"Future Revisit Conditions" names this as the trigger.** Deliberately not
done this milestone: it would require touching `AttentionWidgetProvider.kt`
and/or `AttentionActivity.kt`, both explicitly out of scope ("Widget
redesign" is a listed non-goal in the milestone brief). `VoiceActivity`
already supports being opened bound to an `AttentionRequest`
(`EXTRA_ATTENTION_REQUEST_ID`) — the infrastructure ADR-015 was waiting on
now exists — but the actual "Talk Now" call site still just opens the app,
unchanged. This is a natural, low-risk follow-up for a future milestone,
not a gap in this one.

## Consequences

- The server's `VoiceSessionState.SPEAKING` value, defined in the legal-
  transition map since Milestone 8, is never actually reached by any real
  code path in `voice_session_manager.py::handle_transcript()` — the
  server only ever reports `listening`/`deferred` after a turn. Both the
  PWA (pre-existing) and `VoiceActivity` (this milestone) correctly treat
  "speaking" as a purely client-driven fact (speak whenever new response
  text arrives) rather than gating on a server-reported state that will
  never appear — but the constant remaining formally "reachable" in the
  transition map without being reachable in practice is a small, disclosed
  documentation/implementation gap, not something this milestone changes
  server-side.
- A voice session opened by a connection that then abruptly disconnects
  (app killed, network loss) is now unconditionally closed server-side
  (the new `finally` block in `app/main.py`) — this was already implicitly
  true in spirit (an abandoned session was harmless before the lease
  existed) but is now load-bearing: without it, an abandoned session would
  permanently block any future voice session for that `AttentionRequest`.
- Android's local voice-session mirror can never survive a reconnect
  either, and correctness does not even depend on the server sending
  anything — `CompanionWebSocketClient.onOpen()` unconditionally resets
  `voiceSessionRepository` before any frame for the new connection is
  processed, the same unconditional-reset pattern ADR-015 already needed
  for `attentionRepository` (there, conditional on the server's own
  `pending_attention` behavior; here, unconditionally correct by
  construction since the server-side session is categorically already
  gone).

## Tradeoffs

- No Bluetooth or wired-headset routing logic exists beyond what Android's
  `AudioManager` already provides by default — explicitly in scope only as
  "routing if already naturally supported... not custom logic." The real
  S20 FE test device has no 3.5mm jack, so wired-headset routing was not
  independently real-device-validated this milestone (disclosed, not a
  regression — Bluetooth-headset routing was also not independently
  validated for lack of an available paired device).
- `DiagnosticsSnapshot.VoiceDiagnostics`'s `lastVoiceEventAgoMs` field is
  wired to accept a timestamp but nothing currently supplies one (`null`/
  "n/a" always, for now) — the other eight diagnostic fields are fully
  live-wired; this one field is a disclosed, minor completeness gap.
- Rotating the device while a spoken response is mid-utterance will
  currently speak that same response again after recreation (the
  spoken-text dedup tracking is Activity-scoped, resetting on rotation,
  while the response text itself is Application-scoped and survives) — a
  real but low-severity UX rough edge (repeating the last sentence after a
  rotation), not a functional break, and not fixed this milestone given
  the added complexity a cross-rotation dedup key would require for a
  minor cosmetic repeat.

## Future Revisit Conditions

Revisit "Talk Now"/the widget's voice affordance now that the underlying
infrastructure exists (see Alternatives Considered) — a small, separately-
scoped follow-up. Revisit the `VoiceSessionState.SPEAKING`
defined-but-unreachable gap if a future milestone needs the server to
actually know when the client is speaking (e.g. for a future multi-device
routing decision). Wake word (Milestone 9B.5) will be the first real
consumer of `SpeechInputController`'s sibling capability (background
capture) — that milestone should reuse this milestone's `PlaybackManager`/
`AudioFocusManager` as-is, not rebuild them.

## References

- `ARCHITECTURE.md` §7 (Voice Architecture), §9 (Android Companion)
- `SESSION.md`, Milestone 8 (`VoiceSessionManager` introduced), Milestone
  8.1 (greeting/echo real-phone fixes), Milestone 9A (ownership-guard gap
  found by D2 review, TD-002 designed), Milestone 9B.3 (the `attention/`
  module pattern this ADR mirrors), Milestone 9B.4 (this decision)
- ADR-007 (VoiceSession Ownership — the standing precondition this
  milestone closes), ADR-015 (Android Attention Widget — the pattern and
  the "Talk Now" future-revisit condition this ADR references)

## Related Milestones

Milestone 7, Milestone 8, Milestone 8.1, Milestone 9A, Milestone 9B.3,
Milestone 9B.4

## Related Source Files

- `app/voice_session_manager.py`, `app/attention_manager.py`,
  `app/database.py` (TD-002 lease), `app/main.py` (error handling on
  `voice_session_open`, disconnect cleanup)
- `android/app/src/main/java/com/jarvis/companion/voice/` (`VoiceSession.kt`,
  `VoiceSessionParser.kt`, `VoiceSessionRepository.kt`, `PlaybackManager.kt`,
  `AudioFocusOwner.kt`, `SpeechInputController.kt`)
- `android/app/src/main/java/com/jarvis/companion/audio/AudioFocusManager.kt`
- `android/app/src/main/java/com/jarvis/companion/ui/VoiceActivity.kt`
- `android/app/src/main/java/com/jarvis/companion/diagnostics/`
  (`DiagnosticsSnapshot.kt`'s `VoiceDiagnostics`,
  `DiagnosticsRepository.kt`'s `buildVoiceDiagnostics`)
- `android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt`
  (`sendVoiceSessionOpen/Transcript/Close`, inbound frame dispatch)
- `android/app/src/main/java/com/jarvis/companion/JarvisCompanionApp.kt`
  (`voiceSessionRepository` singleton)
- `app/static/app.js` (the PWA's equivalent mechanism this ADR mirrors)
