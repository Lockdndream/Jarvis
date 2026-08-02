# ADR-017: Production Wake-Word Foundation

## Status

Accepted

## Date

2026-07-16 (Milestone 9B.6)

## Context

Milestone 9B.5 executed the D5 build spike that had been gated on approval
since M9A/M9B.0: a disposable NDK/CMake spike (`spikes/android-wakeword/`)
vendoring `home-assistant/android`'s real microWakeWord reference (TFLite
Micro JNI, `hey_jarvis.tflite`). Real-device evidence on the S20 FE proved
the architecture actually builds, loads, infers, and detects: controlled
recall 8/10 (non-quiet room) → 10/10 (quiet room), zero false positives
over 22.7 minutes of continuous listening, stable latency (avg 0.12ms) and
memory (14.7–17.9MB native heap), zero crashes. The feasibility gate closed
with that evidence. TD-022 recorded what remained explicitly unproven:
screen-lock/Doze survival, multi-hour battery, and adverse-acoustic recall
— the spike has no foreground service or wake lock, so it does not survive
ordinary backgrounding, which is exactly what any real wake-word capability
requires to be meaningful.

This milestone converts the proven architecture into a production
subsystem. It is explicitly **not** a continuation of the disposable spike
— the spike remains disposable, retained as historical reference only, not
built upon directly (same posture as `spikes/android-presence/` after
M9B.0). Wake-word code is being written into the production `android/`
module for the first time.

## Problem

How does continuous, always-on wake-word detection become a real
capability of the production Android companion — owned cleanly, without
duplicating `PresenceService`/`PlaybackManager`/`AudioFocusManager`/
`VoiceSession` responsibilities that Milestone 9B.4 already established,
respecting the existing `VoiceSession` multi-client lease (TD-002/ADR-007),
and without silently reusing an async confirmation pattern that doesn't
actually verify request/response correlation?

## Decision

**`WakeWordManager` becomes the single owner of wake-word infrastructure —
model lifecycle, `AudioRecord` lifecycle, the inference loop, detection
state, diagnostics, and configuration. It knows nothing about networking,
`VoiceActivity`, the WebSocket protocol, the `VoiceSession` protocol, or
UI.** `PresenceService` is the application's host for long-lived
background infrastructure — already true of the WebSocket transport it has
owned since M9B.1, and now also true of wake-word detection (gaining
`FOREGROUND_SERVICE_MICROPHONE` and `foregroundServiceType="dataSync|
microphone"`). Framing it this way, rather than as "transport plus
wake-word," keeps the description accurate as future background
subsystems are added. `VoiceActivity` remains the sole owner of user
interaction, microphone-driven conversation, and playback; `VoiceSession`
remains entirely server-owned. None of the ownership boundaries Milestone
9B.4 established change.

- **`WakeWordManager`** (`android/.../wakeword/`): coroutine-based (not
  the spike's raw `Thread`, matching `AudioFocusManager`/`PlaybackManager`'s
  existing convention), wrapping the vendored, unchanged `MicroWakeWord`
  JNI class. Exposes `StateFlow<State>` (`STOPPED`/`LOADING`/`LISTENING`/
  `PAUSED_VOICE_SESSION`/`PAUSED_AUDIO_FOCUS`/`ERROR`) and a
  `SharedFlow<WakeWordDetection>` (`detectionId: UUID`, `timestampMs`,
  `confidence: Float?`, `modelVersion: String`). `confidence` stays `null`
  until a future native/JNI change exposes the model's raw probability
  score — today's `processAudio(): Boolean` only returns the
  already-thresholded result (disclosed gap, not this milestone's scope).
- **Pause/resume driven by `VoiceSessionRepository.current`, not
  `PlaybackManager`.** A `VoiceSession` represents the entire interaction
  (listening, processing, speaking), so `WakeWordManager` needs no
  reference to TTS state at all — `PresenceService` (which already holds
  `app.voiceSessionRepository`) subscribes and calls
  `pauseForVoiceSession()`/`resumeAfterVoiceSession()` on transitions. This
  is also what prevents two simultaneous microphone consumers: whenever a
  `VoiceSession` is active (opened by *any* client — wake-word, widget,
  direct mic tap, or the PWA), `WakeWordManager` is paused.
- **`WakeWordManager` owns its own lightweight `AUDIOFOCUS_GAIN_TRANSIENT_
  MAY_DUCK` request**, independent of `AudioFocusManager` — that class's
  own doc comment already scopes it to "no wake word, no microphone... per
  Milestone 9B.4 scope boundary." A real incoming phone call correctly
  pauses/resumes wake-word capture through this path without either class
  needing to know about the other.
- **Confirmation-gated handoff — the hard invariant is "a `VoiceActivity`
  only exists when a real `VoiceSession` exists."** Detection does not
  optimistically launch `VoiceActivity` and hope; it requests a session
  open, waits for positive confirmation of *that specific request*, and
  only then launches. On rejection or timeout, no UI is shown — the app
  silently resumes listening.
- **Architectural principle, generalized beyond wake-word specifically:
  interactive client-initiated operations that complete asynchronously
  must support positive correlation between the initiating request and
  the corresponding completion or failure response.** A collapsed state
  check (e.g. "some `VoiceSession` is now active") is not sufficient — it
  cannot distinguish the response to *this* request from a different
  session opened concurrently by another trigger (e.g. the attention
  widget's "Talk now"). `client_request_id` is the concrete protocol-level
  correlation primitive this principle is implemented with — deliberately
  generic, not a wake-word-specific field, because any future asynchronous
  client operation (a future ESP companion, a future "Talk now" wiring,
  etc.) has the identical correlation problem and should reuse this same
  primitive rather than inventing its own feature-specific identifier. The
  concrete wire mechanics (optional, echoed back unchanged) belong in
  `docs/protocols/websocket-protocol-v1.md`, not in this ADR.
- **Configuration defaults to disabled (opt-in).** TD-022's Doze/battery/
  adverse-acoustic risk is still open; shipping always-on detection
  enabled by default would present REQUIRES-EXPERIMENT-classified
  behavior as a settled feature. A `SettingsActivity` toggle, gated behind
  the same `RECORD_AUDIO` flow `VoiceActivity` already uses, plus the
  existing battery-unrestricted guidance from the M9B.0 Samsung-battery
  finding.
- **Diagnostics** (`WakeWordDiagnostics`, alongside the existing
  `VoiceDiagnostics`): enabled/model-loaded/model-version/state/last-
  detection/detection-count/avg-and-max-latency. No raw audio, no
  confidence history, no transcripts.

## Alternatives Considered

**`PresenceService` conducts the entire conversation turn itself**
(promoting `PlaybackManager`/`AudioFocusManager`/`VoiceSession` ownership
from `VoiceActivity` to the service, so a detection could complete a full
spoken exchange without ever bringing the UI to the foreground). Rejected:
a materially bigger ownership change touching already-working, real-
device-validated M9B.4 code, for a "no duplicated ownership" violation
larger than the chosen approach — launching `VoiceActivity` for the actual
turn is simpler, safer, and leaves Milestone 9B.4's boundaries untouched.

**Reuse `AudioFocusManager` for wake-word's own focus needs.** Rejected —
that class's own doc comment explicitly scopes it away from microphone/
wake-word involvement; extending it would silently violate a boundary
that class was deliberately given.

**Confirm detection success via `VoiceSessionRepository.current.
filterNotNull().first()`** (the original proposal). Rejected: this only
proves *some* `VoiceSession` became active, not that it is the one this
specific request opened. Real, not hypothetical — a wake-word-triggered
open racing a concurrent widget-triggered "Talk now" open could resolve to
the wrong session being treated as confirmation.

**Ship wake-word enabled by default.** Rejected — TD-022 remains open;
defaulting to on would overstate current evidence and adds a permanently-
visible system mic indicator (AOSP's privacy-indicator UI has no
legitimate suppression method) the user has not opted into.

## Consequences

- `PresenceService`'s role formally expands from "owns the WebSocket
  transport" to "host for the app's long-lived background infrastructure,"
  of which the transport and wake-word detection are now both instances —
  a real responsibility change, which is the reason this ADR exists rather
  than being folded silently into implementation.
- Any voice-session opener can now optionally supply `client_request_id`;
  existing callers that don't (the PWA, today's direct mic-tap flow) are
  completely unaffected — the server echoes `null` back, exactly as if
  the field didn't exist.
- Wake-word detection pauses for *any* active `VoiceSession`, not only
  ones this device's wake-word triggered — correct per the single-listener
  invariant, but worth stating explicitly: a PWA-initiated session also
  pauses Android's wake-word detection for its duration.
- `WakeWordDetection.confidence` stays `null` until a follow-up native
  change exposes the model's raw probability score.

## Tradeoffs

- This milestone architects and implements the production subsystem; it
  does not by itself close TD-022. Phase 6 (real-device validation) is a
  required part of this milestone precisely because "the architecture is
  right" and "it survives Doze/backgrounding/battery in practice" are
  different claims — the second one still needs its own real evidence
  before this can be considered production-hardened rather than merely
  production-architected.
- The `client_request_id` wire addition, while small and additive, is a
  real (if backward-compatible) change to a documented protocol
  (`docs/protocols/websocket-protocol-v1.md`) — every future client of
  that protocol now has one more optional field to be aware of.

## Future Revisit Conditions

Wire "Talk Now" (the M9B.3 widget action) to pass `client_request_id` once
it exists — ADR-016 already flagged "Talk Now" itself as a natural future
revisit, and this ADR's correlation primitive is exactly what it would
need. Expose real detection confidence via a native/JNI change if a future
need arises (threshold tuning, telemetry). Any future client of the
`client_request_id` primitive (a possible future ESP companion, etc.)
reuses it as specified in the protocol document, not by inventing a
parallel mechanism. Bluetooth routing, ESP integration, remote
connectivity, conversation redesign, reasoning on Android, and business
logic remain explicitly out of scope per this milestone's own stop
conditions, unchanged from the brief.

## References

- **Amended by `ADR-034-wakeword-pause-on-voiceactivity-foreground.md`
  (2026-08-03)**: pause/resume is no longer driven solely by
  `VoiceSessionRepository.current` — `WakeWordManager` also pauses
  whenever `VoiceActivity` is foregrounded, to close a real-device-found
  microphone hand-off race (TD-038).
- `ARCHITECTURE.md` §7 (Voice Architecture), §9 (Android Companion)
- `SESSION.md`, Milestone 9B.5 (D5 spike, closed 2026-07-16), Milestone
  9B.6 (this decision)
- `docs/TECHNICAL_DEBT.md`, TD-022 (wake-word background/Doze survival,
  battery, adverse-acoustic recall unproven — this milestone's real-device
  validation phase is required before that item can close)
- ADR-007 (VoiceSession Ownership), ADR-016 (Android Voice Infrastructure
  — this ADR's `PlaybackManager`/`AudioFocusManager` are reused as-is, not
  rebuilt, per ADR-016's own Future Revisit Conditions)
- `docs/protocols/websocket-protocol-v1.md` (to be extended with
  `client_request_id`, per this ADR's correlation principle)

## Related Milestones

Milestone 9A, Milestone 9B.0 (D1 candidate research), Milestone 9B.4
(`PlaybackManager`/`AudioFocusManager`/`VoiceSession` infrastructure
reused as-is), Milestone 9B.5 (D5 feasibility spike, closed), Milestone
9B.6 (this decision)

## Related Source Files

- `android/app/src/main/java/com/jarvis/companion/wakeword/` (new —
  `WakeWordManager.kt`, `WakeWordDetection.kt`, `WakeWordConfigRepository.kt`)
- `android/app/src/main/java/com/jarvis/companion/service/PresenceService.kt`
  (hosts `WakeWordManager`, subscribes to `voiceSessionRepository.current`,
  the confirmation-gated handoff)
- `android/app/src/main/java/com/jarvis/companion/ui/VoiceActivity.kt`
  (`EXTRA_LAUNCHED_BY_WAKEWORD`, auto-start listening)
- `android/app/src/main/java/com/jarvis/companion/voice/VoiceSessionRepository.kt`
  (new `openOutcomes: SharedFlow<VoiceSessionOpenOutcome>`)
- `android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt`
  (`sendVoiceSessionOpen`'s new optional `clientRequestId` parameter)
- `android/app/src/main/AndroidManifest.xml` (`FOREGROUND_SERVICE_MICROPHONE`,
  `foregroundServiceType="dataSync|microphone"`)
- `android/app/src/main/java/com/jarvis/companion/diagnostics/` (new
  `WakeWordDiagnostics`)
- `android/app/src/main/java/com/jarvis/companion/ui/SettingsActivity.kt`
  (wake-word enable/disable toggle)
- `app/main.py` (`voice_session_open`/`voice_session_opened`/
  `voice_session_error` gain optional `client_request_id`)
- `docs/protocols/websocket-protocol-v1.md` (documents the new field)
- `spikes/android-wakeword/` (reused: `MicroWakeWord.kt`, vendored
  native layer minus the HWASan block; not reused: `WakeWordSpikeActivity`)
