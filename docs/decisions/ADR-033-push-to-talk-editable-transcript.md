# ADR-033: Push-to-Talk with Editable Transcript (Interactive Mode)

## Status

Accepted

## Date

2026-08-02

## Context

ADR-031 established dual-mode voice capture: interactive mode (screen on,
`VoiceActivity` in foreground) uses `android.speech.SpeechRecognizer`
directly, with the platform's own trained endpoint detection deciding when
an utterance ends, and the final transcript sent to the Supervisor
immediately with no review step. That ADR's Tradeoffs section states
plainly: "SpeechRecognizer's own endpoint detection is not tunable by
Jarvis — observed mid-sentence cutoffs during device testing are an
inherent platform behavior, not a parameter this codebase controls." This
decision revisits and partially corrects that claim.

Two problems compounded in practice: SpeechRecognizer's aggressive
endpointing could cut off a sentence before the user finished speaking,
and misheard words (a homophone, a name, a domain term) were sent straight
to the Supervisor with no chance to fix them — the user only found out
something was wrong after Jarvis had already acted on the wrong text.

## Decision

**Interactive mode only** (screen-on, `VoiceActivity` foreground) gains a
manual review step between capture and send. Background/automatic mode
(the screen-off wake-word capture path described as a future gap in
ADR-031) is untouched by this decision.

### New state machine

A pure, unit-tested `VoiceScreenState` (`IDLE, LISTENING, REVIEWING,
PROCESSING, RESPONDING`) sits above the existing per-recognizer state
(`SpeechInputController.State`). Full transition table:

```
IDLE       + MicTapped              -> LISTENING
IDLE       + TextTyped              -> REVIEWING
LISTENING  + StopTapped             -> REVIEWING
LISTENING  + FinalTranscriptReceived -> REVIEWING   (auto-endpoint fallback -- same target)
LISTENING  + EmptyTranscriptReceived -> IDLE
LISTENING  + CancelledOrBackgrounded -> IDLE
REVIEWING  + SendTapped              -> PROCESSING
REVIEWING  + ReRecordTapped          -> LISTENING    (discard, start fresh)
REVIEWING  + ClearTapped             -> IDLE         (discard)
REVIEWING  + ReviewTimedOut          -> IDLE         (60s inactivity, auto-discard)
PROCESSING + ResponseReceived        -> RESPONDING
RESPONDING + TtsFinished             -> LISTENING (if continuous conversation active) or IDLE
RESPONDING + MicTapped               -> LISTENING    (barge-in)
```

Key properties, each a deliberate decision (Opus Checkpoint #1, before
implementation):

- **Auto-endpoint is not removed.** `SpeechRecognizer`'s own
  `onResults`/timeout path still fires and still lands in `REVIEWING` —
  it is a fallback, never a direct-to-`PROCESSING` shortcut. A user who
  never taps Stop still gets a review step.
- **Wake word stays paused for the entire conversation**, not just during
  capture — `LISTENING` through `REVIEWING` through `PROCESSING` through
  `RESPONDING` all hold audio focus continuously (`AudioFocusManager`,
  unchanged mechanism from ADR-016/017), which is what keeps
  `WakeWordManager` paused via its own focus-loss listener.
- **A mic tap while Jarvis is speaking (`RESPONDING`) interrupts TTS and
  starts a new capture** (barge-in) — `playbackManager.cancel()` before
  `audioFocusManager.requestFocus()`, so the request/abandon ordering
  doesn't fight itself.
- **60-second REVIEWING timeout**, reset on every edit keystroke, so an
  actively-editing user is never cut off — only genuine inactivity
  auto-discards back to `IDLE`.

### `stopListening()` — a second, real way to end capture

`SpeechRecognizer.stopListening()` flushes whatever was recognized so far
through the normal `onResults` callback, distinct from `cancel()` (which
discards with no result, unchanged behavior for true aborts). The mic
button becomes a Stop button while `LISTENING`; tapping it calls
`stopListening()` rather than waiting for auto-endpoint.

### Silence-length extras revisited (partial correction to ADR-031)

Real-device testing found that even after adding Stop, `SpeechRecognizer`
still auto-endpointed mid-sentence during normal speaking pauses using the
`EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS` /
`..._POSSIBLY_COMPLETE_..._MILLIS` values already set (3000ms/1500ms, from
the original TD-029 investigation) — undermining the entire point of a
manual Stop button. `SpeechInputController` is used exclusively by this
interactive path (and `DiagnosticsActivity`), never by background
auto-capture, so both values were pushed to 60000ms: auto-endpoint becomes
a last-resort safety net for a genuinely abandoned recognizer, not a
normal-thinking-pause trigger. **This does not fully correct ADR-031's
claim** — confirmed via logcat that `onEndOfSpeech` (the platform's own
voice-activity detector) fires independently of these extras and
consistently precedes `onResults` by only 100-200ms regardless of their
value. The extras tune the *ambiguous* "is this really the end" decision
window, not the underlying VAD event itself. Genuinely eliminating
auto-endpoint (capture continues until Stop, full stop) would require
chaining — restarting the recognizer under the hood whenever it
auto-ends while still `LISTENING`, appending transcripts, only truly
stopping on an explicit Stop tap — which is a structural change to the
capture model, not a tuning fix, and is explicitly out of scope for this
decision (see Future Revisit Conditions).

### Text-only fallback

A "type instead" affordance, visible only in `IDLE`, enters `REVIEWING`
via `TextTyped` and reuses the identical Send/Re-record/Clear flow voice
capture uses — deliberately not a persistent chat input bar, to keep voice
as the primary interaction and text as a subtle fallback.

## Real-device findings during Step 5 testing

Two bugs were found and fixed, and one new debt item was opened, all via a
diagnostic `Log.i("VoiceScreenState", ...)` trace added to every
`transition()` call and kept permanently — both bugs below were only
visible through this trace, not from code review:

1. **The mic button remained visible and tappable during `REVIEWING`**
   (labeled "Mic"), but no `REVIEWING + MicTapped` table entry existed, so
   tapping it silently restarted capture in the background — the old
   transcript stayed on screen with no "Stop" affordance until the new
   result overwrote it. Confirmed on-device: a live-transcript bubble
   appeared above the still-showing old REVIEWING text with no visual
   indication a new recording had started. Fixed by routing the mic
   button's `REVIEWING`-state tap through the identical discard-and-restart
   path the dedicated Re-record button already used correctly.
2. **`showError()` did not transition `screenState`.** A recognizer error
   (`NO_MATCH`, network) left `SpeechInputController` back at `IDLE` but
   `screenState` stuck at `LISTENING` — the button stayed showing "Stop,"
   and the next tap called `stopListening()` on an already-idle
   recognizer (a no-op), wedging the screen until the Activity was
   backgrounded. Fixed by firing `CancelledOrBackgrounded` from
   `showError()`.
3. **TD-038 (new)**: real-device testing surfaced that
   `AudioFocusManager.requestFocus()` rebuilds and resubmits a fresh
   `AudioFocusRequest` on every call with no check for focus already
   held, and since this app deliberately holds focus continuously across
   an entire conversation (the wake-word-pause invariant above), every
   mic tap after the first one in an ongoing conversation re-triggers a
   self-inflicted `AUDIOFOCUS_LOSS_TRANSIENT`. Separately,
   `com.google.android.tts` (confirmed via `adb shell ps -A`, not a
   third-party app) independently re-grabs audio focus ~1.5 seconds into
   subsequent capture attempts, but only after this app has played a TTS
   response at least once in that session. Both were confirmed via
   logcat correlated with real spoken-but-not-transcribed failures
   (`onError code=7`). Documented in `docs/TECHNICAL_DEBT.md`, not fixed
   here — needs its own investigation, not a same-session patch.

## Alternatives Considered

**Remove auto-endpoint entirely for interactive mode**, matching the
user's initial framing that a Stop button makes it redundant. Rejected as
this decision's scope — see the silence-length section above; doing this
properly requires recognizer chaining, a structural change, not a tuning
change, and risks compounding the TD-038 focus-acquisition race by
multiplying restart cycles. Deferred to a future revisit.

**Hold-to-talk** (press and hold to record, release to stop) instead of
tap-to-toggle. Explicitly excluded from scope by the original brief —
tap-to-toggle was the intended interaction from the start.

## Consequences

### Positive Outcomes

- The user, not an algorithm, decides when they're done speaking for the
  common case (manual Stop) — auto-endpoint remains a safety net rather
  than the primary mechanism.
- A misheard transcript is visible and editable before it reaches the
  Supervisor, closing the "Jarvis acted on the wrong words" gap that
  ADR-031's live-partials-only mitigation did not fully close.
- All 7 device-test scenarios (normal flow, edit-and-send, re-record,
  auto-endpoint fallback, text-only input, TTS barge-in, cancel) verified
  working on a real device (S20 FE), 2026-08-02.
- 401 unit tests passing (21 new from this decision's state machine),
  zero regressions.

### Tradeoffs

- SpeechRecognizer's endpoint detection is now *partially* tunable
  (the silence-length extras), correcting ADR-031's blanket claim that it
  is not tunable at all — but the underlying VAD event (`onEndOfSpeech`)
  remains outside this codebase's control, so mid-sentence cutoffs are
  reduced (1.1s pause tolerance -> 8.7s+ observed) but not eliminated.
- TD-038 means multi-turn voice conversations are at elevated risk of a
  silent capture failure on the second or later turn, particularly once
  TTS has spoken a response — a real, currently-open gap, not a
  theoretical one.
- The raw-audio fallback path (`startListeningRaw`, used when
  `SpeechInputController.isRecognitionAvailable()` is false) is untouched
  by this decision — no Stop button, no REVIEWING, no editable transcript
  in that fallback. An accepted, disclosed gap matching this decision's
  explicit scope (interactive mode's primary `SpeechRecognizer` path
  only).

## Future Revisit Conditions

- TD-038 is resolved — re-assess whether the remaining auto-endpoint
  mid-sentence-cutoff tradeoff is still worth accepting, or whether it's
  now safe to pursue recognizer chaining (true Stop-only capture) without
  compounding a focus race that no longer exists.
- A user reports the 8.7s+ observed silence tolerance is still
  insufficient in practice — would motivate either raising the extras
  further or moving to chaining sooner than otherwise planned.
- Background/screen-off capture (ADR-031's deferred gap) is eventually
  built — at that point, confirm this decision's wake-word-pause
  mechanism (continuous focus hold) doesn't need to change to accommodate
  a capture mode that isn't launched through `VoiceActivity`.

## References

- `docs/decisions/ADR-031-dual-mode-voice-capture.md` (the decision this
  extends; partially corrects its "endpoint detection is not tunable"
  claim for interactive mode).
- `docs/decisions/ADR-016-android-voice-infrastructure.md`,
  `ADR-017-production-wakeword-foundation.md` (existing audio-focus/
  wake-word-pause mechanism this reuses unchanged).
- `docs/TECHNICAL_DEBT.md` (TD-038, opened this milestone).
- `docs/STT_CURRENT_FLOW.md` (updated to reflect the REVIEWING step).

## Related Milestones

Push-to-talk with editable transcript (post-TD-029-v2, post-F1 feature
roadmap), 2026-08-02.

## Related Source Files

- `android/app/src/main/java/com/jarvis/companion/ui/VoiceActivity.kt`
  (`VoiceScreenState`, `VoiceScreenEvent`, `nextVoiceScreenState()`,
  `transition()`, `applyScreenState()`, barge-in and review-timeout
  wiring).
- `android/app/src/main/java/com/jarvis/companion/voice/SpeechInputController.kt`
  (`stopListening()`, silence-length extras).
- `android/app/src/main/res/layout/activity_voice.xml` (`reviewContainer`,
  `reviewEditText`, `sendButton`/`reRecordButton`/`clearReviewButton`,
  `typeInsteadButton`).
- `android/app/src/main/java/com/jarvis/companion/audio/AudioFocusManager.kt`
  (TD-038's `requestFocus()` idempotency gap).
