# ADR-025: Groq Whisper for Speech-to-Text

## Status

Accepted

## Date

2026-07-29 (Month 1, Week 1 — post-F1 roadmap, STT upgrade)

## Context

Jarvis has two independent client-side speech recognizers today, both
converging on the same server-side handler
(`app/main.py:663` → `VoiceSessionManager.handle_transcript()`), documented
in `docs/STT_CURRENT_FLOW.md`:

- **Path A**, the browser PWA (`app/static/app.js`): the Web Speech API
  (`webkitSpeechRecognition`/`SpeechRecognition`), reachable only by a human
  manually opening the PWA and tapping the mic.
- **Path B**, the native Android companion: `android.speech.SpeechRecognizer`
  wrapped by `SpeechInputController.kt`, reachable both by manual mic-tap and
  by wake word (`WakeWordManager` → `PresenceService.handleWakeWordDetection()`
  → `VoiceActivity`) — the only path wake-word conversations ever use.

Both recognizers run entirely off-device today: the Web Speech API sends
audio to Google's servers for recognition (ADR-002 §Context notes it is
"cloud-backed even in the foreground"), and Android's `SpeechRecognizer`
likewise depends on Google's on-device/cloud speech services. Transcription
quality with both is noticeably degraded in noisy environments — the primary
real-world condition for hands-free, wake-word-triggered use, which is
exactly the scenario neither recognizer was chosen or tuned for.

## Problem

How should Jarvis get materially better transcription accuracy, particularly
in noisy environments, without violating the project's local-first
invariant (CLAUDE.md invariant #6, ADR-001) any more than the status quo
already does?

## Decision

**Transcription moves from the phone to the laptop, using Groq's hosted
Whisper API (`whisper-large-v3-turbo`) as the recognizer.** The client
(whichever client — Path A or Path B) stops running its own speech
recognizer and instead captures and streams raw audio to the laptop over
the existing WebSocket connection. The laptop-side `app/stt.py` module
sends that audio to Groq's endpoint
(`https://api.groq.com/openai/v1/audio/transcriptions`) using a
laptop-held `GROQ_API_KEY` (accessed via `app/config.py`, never present on
the phone), receives back a transcript, and feeds it into the same
`VoiceSessionManager.handle_transcript()` entry point both paths already
converge on. Cost (~$0.04/hour of audio) is logged per call for visibility.

This does not newly violate local-first: audio recognition was already
being sent off-device via Google's recognizers in both current paths. What
changes is *whose* cloud service receives it, and that the laptop — not the
phone — now owns the API call and holds the credential, consistent with
"laptop is the brain."

Client-side scope (which of Path A / Path B gets the raw-audio-capture
change) is decided separately per milestone step, not fixed by this ADR;
see `docs/STT_CURRENT_FLOW.md` and the Month 1 Week 1 task notes for the
current choice (native companion, Path B, since that is the only
wake-word-reachable path).

## Alternatives Considered

**Local Whisper (on-laptop inference, no external API).** Rejected for
now. Genuinely local-first, but the laptop's available GPU is too small to
run a Whisper model with acceptable latency for a conversational assistant;
CPU inference would add unacceptable turn-around time to every utterance.
Worth revisiting if laptop hardware changes or a sufficiently fast
small/quantized model becomes viable.

**Keep client-side recognition (Web Speech API / Android
`SpeechRecognizer`), tune parameters instead.** Rejected. Neither
recognizer exposes noise-robustness tuning to the caller; the accuracy
ceiling is fixed by the platform vendor, not by Jarvis's integration.

**Do the Groq call from the phone directly.** Rejected. Would require
distributing the API key to every device, contradicting "laptop is the
brain" and creating a second place a cloud credential must be secured and
rotated. Routing through the laptop keeps the key in one place and keeps
the phone a thin capture surface, consistent with ADR-002.

## Consequences

### Positive Outcomes

- Transcription accuracy, especially in noisy/hands-free conditions, is
  expected to improve materially — Whisper's noise robustness is a known
  strength relative to on-device mobile speech recognizers.
- The API key lives only on the laptop; the phone never holds a cloud
  credential for this purpose.
- One shared laptop-side implementation (`app/stt.py`) serves both client
  paths regardless of which is upgraded first.
- Per-call cost logging gives visibility into a new recurring expense
  before it accumulates unnoticed.

### Tradeoffs

- Introduces a new external network dependency and a per-hour cost that
  did not exist as an explicit line item before (though an equivalent
  off-device dependency already existed implicitly via the Web Speech API
  and Android's recognizer).
- Adds latency: audio now travels phone → laptop → Groq → laptop → phone,
  instead of phone → recognizer → phone directly. Step 4's end-to-end test
  must confirm this stays acceptable for a conversational assistant.
- Raw audio now transits the laptop and a third party (Groq) that did not
  see it before in the Path B case (Android's recognizer historically
  talked to Google directly from the device); this is a new data-flow to
  be aware of, though not a new *category* of off-device transmission.
- Whichever client path is upgraded first (native companion, Path B) still
  leaves the other path (PWA, Path A) on its old recognizer until/unless a
  follow-up step upgrades it too.

## Future Revisit Conditions

- Laptop hardware changes, or a fast enough local/quantized Whisper variant
  becomes available, making on-device transcription viable — would remove
  the external dependency and per-hour cost entirely.
- Real usage shows Groq's per-hour cost or latency is unacceptable at
  Jarvis's actual conversation volume.
- A decision is made to also upgrade Path A (the PWA) to raw-audio capture,
  extending this same laptop-side pipeline to the browser client.

## References

- `docs/STT_CURRENT_FLOW.md` (current two-path flow this ADR responds to).
- `docs/decisions/ADR-001-laptop-remains-the-brain.md` (local-first,
  laptop-is-the-brain).
- `docs/decisions/ADR-002-hybrid-android-architecture.md` (both client
  paths already cloud-backed; native companion's narrow justification).
- `CLAUDE.md` (invariant #6 — local-first; ADR-driven architecture rule).

## Related Milestones

Month 1, Week 1 of the post-F1 feature roadmap (STT upgrade), Steps 0–4.

## Related Source Files

- `app/stt.py` (new — Groq client)
- `app/config.py` (`GROQ_API_KEY` accessor)
- `app/voice_session_manager.py` (`handle_transcript()` entry point both
  paths converge on)
- `android/app/src/main/java/com/jarvis/companion/voice/SpeechInputController.kt`
  (Path B, native — first client to be upgraded)
