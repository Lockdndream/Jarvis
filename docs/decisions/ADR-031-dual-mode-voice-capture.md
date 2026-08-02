# ADR-031: Dual-Mode Voice Capture (Screen-On SpeechRecognizer / Screen-Off AudioRecord)

## Status

Accepted

## Date

2026-08-01 (TD-029 v2, post-F1 feature roadmap)

## Context

TD-029 (`docs/TECHNICAL_DEBT.md`) documented a Critical defect: the native
Android companion's raw-audio capture path
(`AndroidAudioCaptureEngine.kt`) used a flat RMS amplitude threshold
(`SILENCE_RMS_THRESHOLD = 328.0`) to detect end-of-utterance. Any steady
ambient noise above that threshold (a room fan, in the reproduction that
triggered this entry) never registers as silence, so the capture loop
never terminates — the entire utterance, including safety-relevant
commands like "cancel it," is silently lost with no error shown.

The original fix brief called for replacing the flat threshold with a
proper VAD inside the same raw-capture path, alongside running Android's
`SpeechRecognizer` in parallel (for live partial transcripts and trained
endpoint detection) while `AudioRecord` kept capturing raw audio for Groq
transcription (ADR-025) in the background.

**That parallel-capture premise was disproven on-device before
implementation began.** Five controlled trials on the target device
(Samsung S20 FE) showed `SpeechRecognizer` and `AudioRecord` cannot share
the microphone: whichever party starts second (or loses Android's
concurrent-capture arbitration) is silently muted — `SpeechRecognizer`
received zero audio and timed out with `NO_MATCH` in every trial where an
`AudioRecord` capture was concurrently active, including a control trial
where the production `AndroidAudioCaptureEngine` simultaneously logged
real, non-zero RMS values proving it held the microphone. Android's own
documentation (`developer.android.com/media/platform/sharing-audio-input`,
`source.android.com/docs/core/audio/concurrent`) describes a policy of
muting the losing party rather than blocking capture start, but does not
specify which party loses for a given pairing — this had to be determined
empirically per device, and the answer for this hardware ruled out running
both simultaneously.

## Problem

How do we fix TD-029's actual defect (indefinite capture hangs under
ambient noise) given that the two available capture strategies —
`SpeechRecognizer`'s trained endpoint detection with live partials, and
`AudioRecord`'s raw-audio access needed for Groq transcription — cannot
run at the same time on the same microphone?

## Decision

**Split capture by mode instead of running both engines at once.**

- **Screen-on / interactive mode** (the mic button, or a wake-word launch
  that opens `VoiceActivity`): use Android's on-device `SpeechRecognizer`
  exclusively. No `AudioRecord`, no raw audio, no Groq call for this mode.
  The user is looking at the screen, so live partial transcripts
  (`onPartialResults`) render directly in the conversation view as the
  user speaks, and the recognizer's own trained endpoint detection (not a
  threshold Jarvis controls) decides when the utterance ends.
- **Screen-off / background mode** (a hypothetical future wake-word
  capture path with no screen open) would use `AudioRecord` with the new
  adaptive, rolling-noise-floor silence detection (see below) and send
  raw audio to Groq, exactly as the original TD-029 brief specified. **This
  mode does not exist today** — `PresenceService` (the only thing alive
  when the screen is off) owns no capture engine of its own; today's
  wake-word path always launches `VoiceActivity`, which is screen-on by
  definition the moment it's visible. Building background capture is
  deferred to a separate future milestone (explicit user decision,
  2026-08-01); this ADR documents the intended design for that mode so it
  doesn't have to be re-derived later, but no code implements it yet.
- **The adaptive silence-detection fix itself** — replacing
  `SILENCE_RMS_THRESHOLD = 328.0` with a rolling noise-floor window
  (lowest-20th-percentile average of recent RMS chunks, `NOISE_FLOOR_MIN
  = 50.0`), a relative speech threshold (`noiseFloor * SPEECH_MULTIPLIER`,
  `SPEECH_MULTIPLIER = 2.5`), a priming period before detection starts, and
  safety backstops (`MAX_CAPTURE_DURATION_MS = 60_000`,
  `MIN_CAPTURE_DURATION_MS = 1000`, `IDLE_TIMEOUT_MS = 10_000` with a new
  `onIdleTimeout` callback) — was implemented in full in
  `AndroidAudioCaptureEngine.kt` and is unit-tested (30/30). It is wired as
  the fallback path used only when
  `SpeechInputController.isRecognitionAvailable()` returns false. On the
  S20 FE (and any device with Google's speech services installed) that
  fallback never triggers, so **this code has not executed under real
  ambient noise on a real device** — see TD-029's updated status in
  `docs/TECHNICAL_DEBT.md` for why this keeps TD-029 open at reduced
  severity rather than closed.

### ADR-025 amendment (accuracy trade-off, interactive mode only)

ADR-025 moved transcription from on-device recognizers to Groq Whisper for
better noise-robust accuracy. **This decision knowingly reverses that
choice for screen-on/interactive mode specifically**: interactive mode now
uses on-device `SpeechRecognizer` again, not Groq, because running both
concurrently for live-partials-plus-Groq-accuracy is impossible given the
mic-sharing finding above. This was an explicit, informed trade-off
(user decision, 2026-08-01, given both options):

> "Option 2 can't work — we already proved it. SpeechRecognizer and
> AudioRecord can't share the mic on this device... Proceed with option 1.
> Accept the regression, note it in the ADR amendment as deliberate. The
> live feedback compensates — the user sees the transcript and can repeat
> if it's wrong. That's a better interactive experience than a more
> accurate transcript they can't see until after Jarvis has already
> processed it."

Confirmed on-device (2026-08-01): under moderate TV background noise, one
short word ("Endgame") was misheard by the on-device recognizer, while
capture and endpoint detection completed correctly (no hang, no lost
turn). ADR-025 itself is not reverted — Groq Whisper remains the
transcription path for any future screen-off/background mode, where no
live-feedback UI exists to compensate for accuracy loss.

## Alternatives Considered

**Force parallel capture via a lower-level audio API (e.g.
`AudioPlaybackCapture`, a custom `AudioRecord` session shared with
`SpeechRecognizer` via a shared `AudioRecord` handle).** Not attempted.
Android does not expose an API for `SpeechRecognizer` to consume an
application-supplied `AudioRecord` stream — it owns its own capture
internally. Sharing would require bypassing `SpeechRecognizer` entirely
and using a raw on-device model, which is a different, larger project.

**Keep the parallel-capture design, accept SpeechRecognizer silence in
interactive mode, extract Groq's audio from `AudioRecord` only.** Rejected
per the empirical finding — this is functionally identical to "AudioRecord
only, no SpeechRecognizer," since `SpeechRecognizer` was never receiving
audio in that configuration. The live-partials UX benefit the whole
redesign was chasing would not exist.

**Ship background mode now, alongside interactive mode.** Rejected by
explicit user decision — it requires new architecture
(`PresenceService` owning a capture engine) that doesn't exist, is
untested, and would expand this milestone's scope significantly. Deferred
to a separate future milestone; documented here as a known gap.

## Consequences

### Positive Outcomes

- TD-029's failure mode (indefinite capture hang under ambient noise)
  does not reproduce in interactive mode — confirmed on a real device
  under quiet-room conditions (repeatedly) and moderate TV background
  noise (once).
- Live partial transcripts give the user real-time feedback and a chance
  to catch and repeat a misheard utterance, which the previous
  no-transcript-until-response Groq flow did not offer at all.
- The adaptive silence-detection algorithm is implemented and unit-tested
  for whenever a background-capture mode is eventually built, rather than
  needing to be designed from scratch then.

### Tradeoffs

- Interactive-mode transcription accuracy is measurably lower than the
  Groq path it replaces (ADR-025's stated rationale) — a deliberate,
  accepted regression, not an oversight.
- The adaptive silence-detection fix has zero real-device verification
  under ambient noise, because it only executes in a fallback path that
  never triggers on hardware with Google's speech services installed.
  TD-029 stays open (downgraded to Medium) until a real execution path
  exercises it.
- Background/screen-off wake-word capture remains unimplemented; this is
  not a regression (it was never reliable before this work either), but
  it is a known, documented gap this ADR does not close.
- SpeechRecognizer's own endpoint detection is not tunable by Jarvis —
  observed mid-sentence cutoffs during device testing are an inherent
  platform behavior, not a parameter this codebase controls. **Partially
  corrected by ADR-033** (2026-08-02): the silence-length extras are a
  real, usable lever for the ambiguous "is this really the end" window
  (1.1s tolerance raised to 8.7s+ observed), even though the underlying
  `onEndOfSpeech` VAD event remains outside this codebase's control.

## Future Revisit Conditions

- Background-mode capture is built: at that point, exercise the existing
  adaptive-threshold code under real ambient noise for the first time,
  and re-assess whether TD-029 can close.
- Google's on-device recognizer accuracy proves unacceptable in practice
  (beyond the single observed mis-transcription) — would motivate
  revisiting whether a third mode (e.g. streaming raw audio to Groq
  *and* showing a live local partial from a lightweight on-device model)
  is worth the added complexity.
- A future device or Android version changes the concurrent-capture
  arbitration outcome found here — the empirical finding is specific to
  the S20 FE tested, not guaranteed to hold on all hardware.

## References

- `docs/TECHNICAL_DEBT.md` (TD-029, updated status and this milestone's
  findings).
- `docs/decisions/ADR-025-groq-whisper-stt.md` (the accuracy trade-off
  this decision knowingly reverses for interactive mode only).
- `docs/decisions/ADR-002-hybrid-android-architecture.md`,
  `ADR-016-android-voice-infrastructure.md` (existing voice architecture
  this extends).
- `docs/STT_CURRENT_FLOW.md` (updated to reflect dual-mode routing).

## Related Milestones

TD-029 v2 (post-F1 feature roadmap), 2026-08-01. Extended by
`ADR-033-push-to-talk-editable-transcript.md` (2026-08-02), which adds a
manual review step and partially revisits the endpoint-detection claim
above for interactive mode.

## Related Source Files

- `android/app/src/main/java/com/jarvis/companion/voice/AndroidAudioCaptureEngine.kt`
  (adaptive silence detection — fallback path only).
- `android/app/src/main/java/com/jarvis/companion/voice/SpeechInputController.kt`
  (`isRecognitionAvailable()`, partial/beginning/empty-result callbacks).
- `android/app/src/main/java/com/jarvis/companion/ui/VoiceActivity.kt`
  (mode selection in `startListening()`, live-transcript wiring).
- `android/app/src/main/java/com/jarvis/companion/conversation/ConversationRepository.kt`
  (`updateMessage`/`removeMessage` for in-place live-transcript bubbles).
- `app/main.py` (`voice_session_transcript` handler — `conversation_turn`
  broadcast parity fix, found during this milestone's integration step).
