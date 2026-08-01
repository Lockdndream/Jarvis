# Current Speech-to-Text Flow

Originally written as Step 0 of the Groq Whisper STT upgrade (Month 1,
Week 1 of the post-F1 roadmap), documenting both client-side recognition
paths as they existed **before** that upgrade. The body below this notice
is that original, pre-Groq snapshot — left intact as history. **See the
"Update (2026-08-01)" section at the end for the current state of Path B**
after the Groq Whisper upgrade (ADR-025) and the TD-029 v2 dual-mode
redesign (ADR-031). Path A (the browser PWA) was not touched or
re-verified during TD-029 v2 — treat its section below as unconfirmed for
the current state until someone checks `app/static/app.js` directly.

## The architecture fork this document exists to surface

There are **two independent client-side speech recognizers**, both feeding
the same server-side entry point. This is not a bug — it is exactly what
[ADR-002](decisions/ADR-002-hybrid-android-architecture.md) designed: the
backend doesn't know or care which client a transcript came from. But it
means "upgrade the Android companion's STT" is ambiguous until one or both
paths are named explicitly, because the two paths are implemented in
completely different languages/APIs and only one of them is reachable by
wake word.

## Path A — Browser PWA (`app/static/app.js`)

- Recognizer: `webkitSpeechRecognition` / `SpeechRecognition` (Web Speech
  API), instantiated at `app.js:714`, `:970`, `:1059` via
  `SpeechRecognitionCtor` (`app.js:924`).
- Reachable only by a human opening the PWA (installed via
  `app/static/manifest.json`, `display: "standalone"`) and manually tapping
  the mic — there is no wake word in the browser (ADR-002 §Context, point 3:
  "the Web Speech API is cloud-backed even in the foreground" and cannot
  survive backgrounding).
- On a final recognition result, sends over the PWA's own WebSocket
  connection:
  ```js
  ws.send(JSON.stringify({
    type: "voice_session_transcript",
    voice_session_id: currentVoiceSessionId,
    transcript: transcript,
  }));  // app.js:734-738
  ```

## Path B — Native Android companion (Kotlin)

- Recognizer: `android.speech.SpeechRecognizer`, wrapped by
  `SpeechInputController.kt` ("Foreground, user-initiated speech recognition
  wrapper mirroring the PWA's interaction model... Uses
  `android.speech.SpeechRecognizer` (in-app, no separate recognition UI)").
- **This is the wake-word-reachable path.** Flow:
  ```
  Wake word detected (WakeWordManager, on-device microWakeWord model)
    -> PresenceService.handleWakeWordDetection()  (service/PresenceService.kt:360)
    -> launches VoiceActivity with EXTRA_LAUNCHED_BY_WAKEWORD=true
    -> VoiceActivity.onCreate(): launchedByWakeWord && savedInstanceState==null
         -> onMicTap() -> startListening()
    -> speechInputController.startListening(onResult, onError)
    -> onResult: PresenceService.activeClient.sendVoiceSessionTranscript(
                   session.voiceSessionId, transcript)   (VoiceActivity.kt:302)
    -> CompanionWebSocketClient.sendVoiceSessionTranscript()  (:193)
       sends the same JSON shape as Path A over the native app's own
       WebSocket connection.
  ```
- Also reachable by manually tapping the mic button in `VoiceActivity`
  (`binding.micButton` -> `onMicTap()`), independent of wake word.
- Interaction Layer v1 (Goal 1) adds auto-resume-listening: after Jarvis
  finishes speaking, if the session is in `LISTENING` or `CONFIRMING` state,
  `VoiceActivity` calls `onMicTap()` again automatically — so a real
  wake-word conversation is typically several turns of native
  `SpeechRecognizer`, not just one.

## Where the two paths converge — the server

Both paths send **identical** message shape over WebSocket:
```json
{"type": "voice_session_transcript", "voice_session_id": "...", "transcript": "..."}
```
Server-side, this is handled at a single location:
```python
# app/main.py:663
if data.get("type") == "voice_session_transcript":
    ...
```
which routes into `VoiceSessionManager.handle_transcript()`
(`app/voice_session_manager.py`), the same function regardless of origin.
Documented in `docs/protocols/websocket-protocol-v1.md:108`:
`voice_session_transcript` | `voice_session_id`, `transcript` | *A recognized
utterance within an open voice session.*

**The laptop only ever receives finished text today, from either client.**
Neither path currently sends raw audio.

## Where wake word fits

Wake word detection (microWakeWord, on-device, native-only per ADR-002 point
3) lives entirely in the native companion (`WakeWordManager.kt`) and has no
PWA equivalent — it cannot exist in a browser tab that Android suspends on
backgrounding. Wake word can only ever launch Path B. This is not a gap to
fix; it is the reason the native companion exists at all.

## Why this matters for the Groq Whisper upgrade

- The laptop-side work (Groq API client, config, wiring into
  `VoiceSessionManager.handle_transcript()`) is **shared infrastructure** —
  valuable regardless of which client path is upgraded, since both funnel
  into the same server entry point.
- The client-side audio-capture work is **not shared** — Path A needs a
  browser `MediaRecorder`/`AudioContext` change to `app.js` (JavaScript);
  Path B needs a Kotlin `MediaRecorder` change to
  `SpeechInputController.kt`/`VoiceActivity.kt`/`CompanionWebSocketClient.kt`
  (Android, plus a new binary WebSocket message type on the native client).
  These are different languages, different files, different effort.
- The task's stated goal is **"dramatically better transcription accuracy,
  especially in noisy environments."** Noisy-environment, hands-free use is
  the wake-word scenario — Path B. If only Path A (the PWA) is upgraded,
  wake-word-triggered conversations see **zero improvement**, because they
  never touch `app.js` at all.

## Update (2026-08-01) — Path B is now dual-mode (TD-029 v2, ADR-031)

After the Groq Whisper upgrade above shipped, Path B (native Android
companion) was found to have a Critical defect (TD-029): its raw-audio
capture used a flat RMS silence threshold that never fires under ambient
noise (a fan, a TV), hanging the capture indefinitely. The fix required
reversing part of the Groq upgrade for this path — see
`docs/decisions/ADR-031-dual-mode-voice-capture.md` for the full
rationale (mic-sharing constraints ruled out running both engines at
once). **Current Path B behavior:**

- **Screen-on / interactive mode** (mic tap, or a wake-word launch that
  opens `VoiceActivity`, which is always screen-on): uses
  `android.speech.SpeechRecognizer` directly, same as the original
  pre-Groq flow described above — live partial transcripts render in the
  conversation UI as the user speaks
  (`SpeechInputController.startListening(onPartialResult = ...)`), and
  Android's own trained endpoint detection decides when the utterance
  ends. The final transcript is sent via the same
  `voice_session_transcript` message shown above — **no raw audio, no
  Groq call, for this mode.**
- **Raw-audio + Groq capture** (`AndroidAudioCaptureEngine.kt`, with the
  adaptive noise-floor silence detection that actually fixes TD-029's
  described defect) still exists in code, but only runs as a fallback
  when `SpeechInputController.isRecognitionAvailable()` returns false —
  i.e., on a device without Google's on-device speech recognition
  installed. On the current test device (S20 FE) this is never true, so
  this path is currently dormant / unverified on real hardware. See
  TD-029's updated entry in `docs/TECHNICAL_DEBT.md` for why this keeps
  TD-029 open at reduced severity.
- **Screen-off / background wake-word capture** (fully hands-free, no
  screen ever opened) does not exist as a capability — `PresenceService`
  owns no capture engine, and every wake-word detection today launches
  `VoiceActivity`, which is screen-on the moment it's visible. This is a
  documented, deferred gap (ADR-031), not a regression from before.

Net effect: **the accuracy improvement this Groq Whisper upgrade was
built to deliver applies to Path B only in the currently-dormant
fallback case.** The primary, real-world Path B experience (interactive
mode) is back on Google's on-device recognizer, by deliberate, accepted
trade-off (see ADR-031's ADR-025 amendment) — live partial feedback in
exchange for the accuracy Groq would have given.
