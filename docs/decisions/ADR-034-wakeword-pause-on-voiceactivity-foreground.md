# ADR-034: WakeWordManager Also Pauses While VoiceActivity Is Foregrounded

## Status

Accepted — implemented and unit-tested 2026-08-03; **not yet device-verified**
(see Consequences).

## Date

2026-08-03

## Context

ADR-017 established that `WakeWordManager`'s pause/resume is driven
exclusively by `VoiceSessionRepository.current`: "Pause for the entire
lifetime of any active VoiceSession... Resume automatically once no
VoiceSession is active." A manual mic tap in `VoiceActivity` while no
`VoiceSession` is yet open (i.e. `IDLE` state, before the tap opens one)
therefore finds `WakeWordManager` still actively `LISTENING` — its own
`AudioRecord` competing for the same microphone `SpeechRecognizer` is
about to request.

TD-038 device-testing (2026-08-02/03, see `docs/TECHNICAL_DEBT.md`)
reproduced this concretely: a manual mic tap immediately after
backgrounding and re-foregrounding the app (or, by the same mechanism, any
manual tap before a `VoiceSession` exists yet) showed the same
`onReadyForSpeech` → ~5.4s silence → `onError code=7 (No match found)`
signature already fixed for the *mid-conversation* case
(`ADR-033`/TD-038 finding 1), user-visible as the expected "mic started"
earcon not playing. This is the "cold-launch race" TD-038 originally
flagged as a separate, larger investigation, out of scope for the
mid-conversation fix.

## Decision

**`WakeWordManager` now also pauses whenever `VoiceActivity` is
foregrounded, independent of whether a `VoiceSession` is open.** User's own
framing, adopted directly: *"when I am on top of Jarvis page... the mic
should be off by default. And by clicking the mic button, it should go
on."* If the user is already looking at the interactive screen, wake-word
detection has no purpose — the mic button is a strictly better affordance
that doesn't need to contend with a second capture client for the
microphone.

### Implementation — an OR-gate, not a direct call

`VoiceActivity` does **not** call `pauseForVoiceSession()`/
`resumeAfterVoiceSession()` directly. Doing so would let backgrounding
mid-conversation prematurely resume wake-word listening while a real
`VoiceSession` is still open (e.g. backgrounding during `PROCESSING`/
`RESPONDING`) — `WakeWordManager`'s own methods assume the caller only
invokes them when the session has genuinely ended; they do not
independently check `VoiceSessionRepository.current`.

Instead:
- `PresenceService.voiceActivityForegrounded: MutableStateFlow<Boolean>`
  (companion object, same in-process-only static-accessor pattern as
  `activeClient`/`activeWakeWordManager`) is set `true` in
  `VoiceActivity.onStart()` and `false` in `onStop()`.
- `onStop()`'s clear is guarded by `!isChangingConfigurations()` — a
  rotation must not clear the flag, or the recreated instance's
  `onStart()` would race a spurious resume-then-repause of
  `WakeWordManager` on every screen rotation. This mirrors the exact guard
  `onStop()` already used for its wake-word-session-close logic
  (Milestone 9B.9 Item 5).
- `PresenceService`'s existing pause/resume collector (previously keyed
  solely on `VoiceSessionRepository.current`) now combines that with
  `voiceActivityForegrounded` via `sessionActive || screenForegrounded`,
  `distinctUntilChanged`-ed on the combined boolean (not the raw pair) to
  preserve the existing anti-telemetry-spam behavior. `WakeWordManager`
  resumes only when **neither** reason to pause holds — the two triggers
  structurally cannot stomp each other, since there is exactly one
  decision point (this collector) rather than two independent callers.
  `WakeWordManager` itself is untouched; its pause/resume methods remain
  idempotent from any state, as already verified.

## Alternatives Considered

**Add a second explicit pause-reason state to `WakeWordManager` (e.g.
`PAUSED_FOREGROUND_SCREEN` alongside `PAUSED_VOICE_SESSION`).** Rejected —
a genuinely bigger, riskier change to an already real-device-hardened
state machine (see the extensive Milestone 9B.10 findings documented
inline in `WakeWordManager.kt`) for no behavioral gain over the OR-gate,
which achieves the same non-stomping guarantee with zero changes to that
class.

**Call `pauseForVoiceSession()`/`resumeAfterVoiceSession()` directly from
`VoiceActivity`.** Rejected per the Decision section above — this is the
premature-resume-mid-conversation bug the OR-gate design avoids
structurally rather than by convention.

## Consequences

### Positive Outcomes

- Removes the competing capture client entirely for the manual-tap-while-
  foregrounded case, rather than trying to win a hand-off race against it
  faster (the approach TD-038's mid-conversation fix took, and which only
  applies there).
- No changes to `WakeWordManager` itself — its already-hardened,
  extensively real-device-tested pause/resume state machine is untouched.
- 412 unit tests passing, zero regressions (this change adds no new unit
  tests — see below).

### Tradeoffs

- **"Hey Jarvis" no longer works while `VoiceActivity` is open**,
  including while Jarvis is speaking a response (`RESPONDING` state).
  Accepted, explicit user tradeoff: barge-in via mic tap still works in
  that state, and if the user is already looking at the screen, tapping
  the mic is a strictly available alternative.
- **Not device-verified as of this writing.** Unlike TD-038's
  mid-conversation fix (verified via 8 consecutive successful conversation
  turns on a real device, same session), this change was implemented and
  unit-tested but the user chose to stop for the night before device
  testing. The specific scenario this is meant to fix — manual mic tap
  immediately after backgrounding/re-foregrounding, or before any
  `VoiceSession` has opened — has not yet been re-run against this fix.
- **No dedicated regression test.** `PresenceService` has no existing unit
  test harness (an `android.app.Service` with `NotificationManager`/
  `ConnectivityManager` dependencies, consistent with this codebase's
  existing pattern of device-verifying Service-level lifecycle glue
  rather than unit-testing it — matching how `VoiceActivity`'s click
  listeners are handled). The OR-gate logic itself
  (`sessionActive || screenForegrounded`) is trivial enough that the risk
  is judged acceptable pending device verification, not exempted from
  scrutiny.

## Future Revisit Conditions

- Device testing (next session) confirms or refutes that this closes the
  remaining TD-038 failure mode — update TD-038's status accordingly.
- If a future requirement wants wake-word to work even while
  `VoiceActivity` is open (e.g. hands-free re-engagement while looking at
  a long response), this ADR's core tradeoff would need revisiting —
  likely via a settings toggle rather than reverting the default.

## References

- `docs/decisions/ADR-017-production-wakeword-foundation.md` (the pause/
  resume semantics this amends — that ADR's stated "driven by
  VoiceSessionRepository.current" is no longer the complete picture).
- `docs/decisions/ADR-033-push-to-talk-editable-transcript.md` (TD-038's
  mid-conversation fix, the other half of the same debt item).
- `docs/TECHNICAL_DEBT.md` (TD-038, updated with this fix's status).

## Related Milestones

TD-038 follow-up (push-to-talk milestone), 2026-08-03.

## Related Source Files

- `android/app/src/main/java/com/jarvis/companion/service/PresenceService.kt`
  (`voiceActivityForegrounded`, the combined pause/resume collector).
- `android/app/src/main/java/com/jarvis/companion/ui/VoiceActivity.kt`
  (`onStart()`/`onStop()`).
