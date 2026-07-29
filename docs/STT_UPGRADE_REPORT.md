# Groq Whisper STT Upgrade — Final Report

Month 1, Week 1 of the post-F1 feature roadmap. Replaces on-device speech
recognition with laptop-side Groq Whisper transcription for the native
Android companion (the wake-word-reachable path — see
`docs/STT_CURRENT_FLOW.md` for why the browser PWA was explicitly out of
scope for this milestone).

## Current flow (before → after)

Full detail in `docs/STT_CURRENT_FLOW.md` (written as Step 0, still
accurate for the PWA path; the native path section is now superseded by
this report — the native companion no longer uses `SpeechRecognizer` when
`useRawAudioCapture = true`).

**Before**: phone captures audio → on-device `SpeechRecognizer` transcribes
→ phone sends text only.

**After**: phone captures raw audio (`AudioRecord`, 16kHz mono 16-bit PCM,
on-device silence detection decides the utterance boundary) → sends a WAV
file over the existing WebSocket connection → laptop calls Groq
Whisper (`whisper-large-v3-turbo`) → transcript feeds into the same
`VoiceSessionManager.handle_transcript()` pipeline the text path already
used → response comes back exactly as before.

The on-device `SpeechRecognizer` path still exists, byte-for-byte
unchanged, selected by a local flag (`VoiceActivity.useRawAudioCapture`).
It is currently `true` on the test device (S20 FE) to enable this
milestone's device testing.

## Files created/changed (16 files, +1666/-44 across 4 commits)

| File | What changed |
|---|---|
| `docs/STT_CURRENT_FLOW.md` (new) | Step 0 flow-mapping; found the PWA-vs-native fork before any code was written |
| `docs/decisions/ADR-025-groq-whisper-stt.md` (new) | Architecture decision: Groq Whisper, laptop-held key, alternatives considered |
| `docs/decisions/README.md` | ADR-025 index row |
| `app/stt.py` (new) | Standalone Groq Whisper client — `transcribe()`, `TranscriptResult`, `GroqTranscriptionError`, per-call duration/cost logging |
| `app/config.py` | `groq_api_key()` accessor (call-time env read, matching the F1.12 config pattern) |
| `.env.example` | `GROQ_API_KEY` documented |
| `tests/test_stt.py` (new) | 3 tests: success, missing key, API error |
| `android/.../voice/AndroidAudioCaptureEngine.kt` (new) | `AudioRecord`-based capture, on-device silence detection (thresholds ported from the existing `SpeechRecognizer` engine), WAV encoding, main-thread callback dispatch |
| `android/.../voice/SpeechInputController.kt` | New `AudioCaptureEngine` interface + `startListeningRaw()`, parallel to the existing text path, `cancel()` extended |
| `android/.../network/CompanionWebSocketClient.kt` | `sendVoiceSessionAudio()` — header text frame + binary frame |
| `android/.../ui/VoiceActivity.kt` | `useRawAudioCapture` flag, `pendingAudioBytes` (mirrors `pendingTranscript`), branching `startListening()` |
| `android/.../voice/AndroidAudioCaptureEngineTest.kt` (new) | 16 tests: RMS/WAV pure functions, completion-decision function, engine integration, thread-dispatch regression, release-on-completion regression, cancel-suppression regression |
| `android/.../voice/SpeechInputControllerTest.kt` | +10 tests for `startListeningRaw` |
| `app/main.py` | WS receive loop accepts binary frames; `voice_session_audio` header tracking; `_process_transcript()` helper shared by both the text and audio paths; empty-transcript guard; transcript-preview diagnostic log |
| `docs/protocols/websocket-protocol-v1.md` | `voice_session_audio` + binary-frame convention documented |
| `tests/test_voice_session_audio.py` (new) | 5 tests: successful transcription, Groq error, empty transcript, bare-frame warning, text-path regression |

## Test output

- **Python**: 690 passed (was 685 before this work), `ruff check` clean,
  `mypy` clean, across all four commits, each independently verified (not
  just the delegate's self-report — re-run myself in every case, plus
  negative controls on every new guard).
- **Android**: 290 passed (was 288 before the bugfix round), `assembleDebug`
  builds clean.
- Every new regression test was verified non-vacuous: reverted the fix,
  confirmed the test fails, restored the fix, confirmed it passes again.
  This caught one test-only bug of its own (below).

## End-to-end device test (S20 FE, real Groq API key, real network)

Six real conversational turns, one live session:

| # | Duration | RMS (min/max/mean) | Outcome |
|---|---|---|---|
| 1 | 29.1s | not logged (pre-instrumentation) | Transcribed, but session had already been manually closed — response never reached the phone (see Issue 5) |
| 2 | 33.5s | 93/18609/1088 | Manually closed after 33s; correctly suppressed after the fix (no wasted Groq call) |
| 3 | 10.1s | 104/1844/406 | **Clean auto-completion.** Transcript: *"You know I've been wondering have you ever heard of the sho..."* → LLM responded → session returned to `listening` |
| 4 | 10.2s | 92/1125/273 | **Clean auto-completion**, second turn in the same session. Transcript: *"That's sick information. Thanks..."* |
| 5 | 4.3s | 94/688/143 | **Clean auto-completion**, third turn. Transcript: *"I'm going to go to the next video."* |
| 6 | 26.0s | 90/1090/159 | **Clean auto-completion**, fourth turn. Transcript: *"All right, let's go to that one. So can you tell me like wh..."* |
| 7 | 5.8s | 86/956/241 | **Clean auto-completion**, fifth turn. Transcript: *"Alright man, you can close this call now."* |
| 8 | 14.2s | 85/214/136, `hadLoud=false` | Ambient-only capture after manual close — correctly suppressed, no Groq call |

Four consecutive multi-turn exchanges (turns 3–7) completed correctly
end-to-end: real speech → correct transcript → Supervisor/LLM turn →
spoken response → auto-resume back to `listening`. This is the first
real-device confirmation the full Groq pipeline works, including
Interaction Layer v1's auto-resume across multiple turns in one session.

An earlier attempt (before the bugfix round below) with a TV playing
5–10 feet away never completed automatically at all (Issue 7).

**Fallback path**: not re-tested live this session — `useRawAudioCapture`
was flipped to `true` for device testing and left there; the `false`
branch (`SpeechRecognizer`) is unchanged code, still covered by its
original unit tests, but was not exercised on-device in this round.

## Issues found and judgment calls

1. **Scope fork (PWA vs. native)** — the task's literal instructions
   targeted the browser PWA; direct code reading showed the wake-word
   path is native-only. Surfaced to the user before writing any client
   code; user chose native. Building the PWA as literally instructed
   would have shipped zero improvement for the actual target scenario.
2. **ADR-025 written before implementation** (per CLAUDE.md's rule) —
   documents the Groq dependency, alternatives considered (local Whisper,
   ruled out on hardware grounds), and that this doesn't newly violate
   local-first since the prior `SpeechRecognizer`/Web Speech API paths
   were already cloud-backed.
3. **Key mismatch caught before it wasted device-test time** — the first
   key pasted was an xAI key (`xai-` prefix), not Groq (`gsk_`) — easy
   confusion given the similar names. Caught and corrected before Step 4,
   verified live against the real endpoint before touching the phone.
4. **Real bug: capture callbacks fired off the main thread.**
   `AndroidAudioCaptureEngine` ran its capture loop on a background
   `Thread` and called `onAudioCaptured`/`onError` directly from it — but
   that callback reaches `VoiceActivity.render()`, which mutates Views.
   Would have crashed with `CalledFromWrongThreadException` the first
   time the flag was enabled. Found via code review before any device
   testing, fixed with a `Handler`/main-looper post, verified with a
   regression test (and confirmed non-vacuous).
5. **Real bug: `AudioRecord` never released except via explicit
   `cancel()`.** Every *normal* completion left the microphone open
   indefinitely — combined with Interaction Layer v1's auto-resume, this
   would have broken every second turn in a real conversation. Found via
   code review, fixed by moving cleanup into a `finally` block, verified
   with a regression test.
6. **Real bug: `cancel()` didn't suppress the pending callback.** Setting
   `isCapturing = false` only stopped the capture loop — the loop then
   proceeded to send whatever audio it had anyway, producing a Groq call
   for an already-closed voice session (this is exactly what happened in
   E2E turn #1, before the fix). Fixed with a `cancelled` flag checked
   before every callback; confirmed correct in live testing afterward
   (E2E turns #2 and #8 both show the suppression working).
7. **Real, confirmed (not just hypothesized) silence-detection gap**:
   completion requires **3 unbroken seconds** below the RMS threshold —
   any single brief noise blip resets the counter to zero. Real-device
   RMS logs confirm real speech was captured in every case (`hadLoud=true`
   with genuine high-RMS peaks, not a hallucination-on-silence artifact) —
   the two long turns (29s, 33s) simply never had a perfectly clean
   3-second tail. **Not fixed per explicit user instruction** — logged as
   a follow-up (Issue 9 below has the full list the user asked to record).
8. **Test-only bug found during my own verification, not the delegate's**:
   a new regression test asserted `audioSource.stop()`/`release()` had
   been called after checking a `CountDownLatch` tied to the *callback*
   firing — but the `finally` block (which does the actual stop/release)
   runs *after* the callback in program order on the capture thread, and
   `CountDownLatch.countDown()` only establishes happens-before for
   actions *before* it, not after. Reproduced deterministically (5/5
   runs), diagnosed as a test race rather than a production bug (the
   production fix itself is correct — `finally` always runs immediately
   after `try` on the same thread), fixed by giving `release()` its own
   latch.
9. **Self-inflicted test contamination**: ran a full-suite background
   test alongside a manual negative-control edit to the same file;
   the run correctly caught the file being mid-edit at that instant.
   Recognized it as self-inflicted (log timestamp matched the edit
   window exactly), reran clean, and stopped running negative controls
   concurrently with full-suite background runs for the rest of the
   session.

## Follow-ups requested by the user — explicitly not implemented

Per direct instruction ("do not make any changes, add this to the
report"), the following three real, user-identified gaps are recorded
here and not yet acted on:

1. **No idle-silence timeout.** If nothing is said at all after the mic
   opens (loudness threshold never crossed, `hadLoud` stays `false`),
   capture currently has no time bound — it listens indefinitely. Wanted
   behavior: after a reasonable quiet period, prompt ("I didn't hear
   anything — still there?") and close the session if silence continues.
2. **No mechanism to isolate the user's voice from background audio.**
   With a TV playing 5–10 feet away, its dialogue kept the loudness
   signal alive enough that the silence detector never completed at all
   during that test. There is currently no directionality, speaker
   isolation, or smarter-than-flat-RMS voice activity detection.
3. **No on-screen transcript feedback.** The user wants to see what was
   heard (validation that Jarvis transcribed correctly) without having to
   infer it from the response alone — ideally as a running chat-style
   transcript, rather than the current behavior where each new response
   replaces whatever text was previously shown on screen.

## Did the checkpoints change anything?

Yes, materially, at every gate:

- **Before Step 1**: direct code reading (not the delegate's assumption)
  found the PWA/native fork, which changed the entire shape of the
  client-side work before any was written.
- **Before Step 2**: a second-opinion review flagged the exact concrete
  design questions (end-of-utterance detection mechanism, engine
  interface boundary, protocol framing) that the implementation brief
  then had to answer explicitly rather than leaving to a delegate's
  guess — and flagged the Whisper-hallucinates-on-silence risk that later
  directly shaped the Step 3 empty-transcript guard.
- **After Step 2, before Step 4**: independent verification (not the
  delegate's "tests pass" claim) found the main-thread-dispatch bug via
  code reading alone, before any device was touched — this would have
  been an immediate crash on first real use.
- **During Step 4**: real-device testing found two further bugs
  (AudioRecord leak, cancel-doesn't-suppress) that no amount of unit
  testing on synthetic fakes could have caught, plus the real,
  evidence-backed (not guessed) silence-detection brittleness and the
  two additional UX gaps the user identified from actually using it.

The pattern held throughout: every gate caught something real that the
previous stage's tests had not and could not.
