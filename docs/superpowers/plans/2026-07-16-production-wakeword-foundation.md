# Production Wake-Word Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific delegation note:** this repository's own established
> practice (ADR-013, "Chief Engineer / DeepSeek delegation workforce") is
> Builder/Reviewer/Chief-Engineer separation via the isolated OpenCode
> runtime (`scripts/delegate_opencode_task.py`), not the generic Claude
> Code subagent tool. The "Delegation Guidance" line on each task below
> reflects that model. Use whichever mechanism the operator directs at
> execution time — the task boundaries and acceptance criteria are
> mechanism-independent.

**Goal:** Convert the D5 wake-word feasibility spike (`spikes/android-wakeword/`, disposable) into a production capability of the `android/` companion app — continuous, backgrounding-resistant "Hey Jarvis" detection that hands off to the existing `VoiceActivity` conversation flow via a positively-correlated `VoiceSession` open, per ADR-017.

**Architecture:** `WakeWordManager` (new, in a new `wakeword/` package) owns model/audio/detection lifecycle and is hosted by `PresenceService` (the app's long-lived-background-infrastructure owner). On detection, `PresenceService` requests a `VoiceSession` open carrying a new generic `client_request_id` correlation token, waits for the *matching* confirmation (not just "any session became active"), and only then launches `VoiceActivity`. `PlaybackManager`/`AudioFocusManager`/`VoiceSession` ownership (all `VoiceActivity`-scoped, established in ADR-016) do not change.

**Tech Stack:** Kotlin, Android NDK/CMake (native layer vendored from the D5 spike, itself vendored from `home-assistant/android`'s microWakeWord reference), TensorFlow Lite Micro (JNI-only), Python/FastAPI (`app/main.py`), JUnit + Mockito (Android unit tests), pytest + FastAPI `TestClient` (server tests).

## Global Constraints

- Wake-word capability defaults to **disabled** (opt-in) — TD-022 (background/Doze survival, multi-hour battery, adverse-acoustic recall) is not closed by this milestone.
- `client_request_id` is a **generic, protocol-level correlation primitive** — optional on every message that uses it, echoed back unchanged, never renamed to something wake-word-specific. Existing callers (the PWA, today's direct mic-tap open) that don't send it are completely unaffected.
- No change to `VoiceSession` server-side ownership/lease semantics (TD-002/ADR-007) — this milestone adds request/response correlation on top of the existing unbound-open path, nothing more.
- `WakeWordManager` must not depend on `VoiceActivity`, the WebSocket client, or the `VoiceSession` protocol. `PresenceService` orchestrates the handoff; `WakeWordManager` only exposes state/events.
- `WakeWordManager` must not reuse `AudioFocusManager` — that class is explicitly scoped away from microphone/wake-word involvement (its own doc comment). `WakeWordManager` requests its own lightweight focus.
- No raw audio, confidence history, or transcripts in diagnostics — only the fields specified in ADR-017.
- Out of scope, unchanged from the milestone brief: Bluetooth routing, ESP integration, remote connectivity, conversation redesign, reasoning on Android, business logic, attention redesign.
- Stop conditions (per the milestone brief): if `VoiceSession` ownership changes, if attention architecture changes, or if reasoning moves onto Android — halt and escalate rather than proceeding.

---

## Delegation Guidance (project-specific)

This project's own incident history (`SESSION.md` Architecture Decision #31) found real cross-contamination even between Builders assigned to non-overlapping directories. Default to **one Builder task at a time**. The one safe parallel opportunity is marked below (Tasks 5 and 11 — genuinely disjoint packages, and Task 11 only needs the field *names* already fixed by ADR-017, not Task 7's implementation).

| Task | Owner | Reason |
|---|---|---|
| 1 | Claude-direct | Server-side protocol/correctness-sensitive (`app/main.py`) |
| 2, 3 | Claude-direct | Existing, already-tested files; small precise diffs |
| 4 | Claude-direct | `CompanionWebSocketClient` wiring — no dedicated test file exists for this class today (matches existing convention); low risk of delegation adding value |
| 5 | Delegatable (Builder A) | New, self-contained, pure-logic files |
| 6 | Claude-direct | Native/NDK build wiring — mechanical but easy to silently break the whole app's build if delegated |
| 7 | Claude-direct | Core state machine; depends on 5 and 6 |
| 8 | Claude-direct | One-line manifest change, bundle with 9 |
| 9 | Claude-direct | `PresenceService` is security/lifecycle-sensitive (existing project convention — see M9B.2's rationale for why WS-auth-handshake work was never delegated) |
| 10 | Claude-direct | `VoiceActivity` is the other lifecycle-sensitive file this milestone touches |
| 11 | Delegatable (Builder B, parallel with 5) | Disjoint package, fixed field-name contract from ADR-017 |
| 12 | Delegatable | Self-contained UI addition to an existing screen |
| 13 | Claude-direct | Documentation of the protocol Claude implemented in Task 1 |
| 14 | Claude-direct (Chief Engineer) | Real-device validation — this project's standing practice never trusts a Builder's or Reviewer's self-report for real-device claims |

---

### Task 1: Server-side `client_request_id` correlation echo

**Files:**
- Modify: `app/main.py:449-473` (the `voice_session_open` handler)
- Test: `tests/test_voice_session_correlation.py` (new)

**Interfaces:**
- Consumes: nothing new — `voice_session_manager.open_session()` is unchanged.
- Produces: `voice_session_open` requests may now include an optional `client_request_id` (any string); `voice_session_opened` and `voice_session_error` replies echo it back verbatim under the same key, or omit/null it if the request didn't send one.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_voice_session_correlation.py
"""Milestone 9B.6 (ADR-017): voice_session_open/opened/error carry an
optional, generic client_request_id correlation token, echoed back
unchanged. Existing callers that never send it (the PWA) are unaffected.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db

_ENV_BEFORE_IMPORT = dict(os.environ)
import app.main as _main_module
for _leaked_var in set(os.environ) - set(_ENV_BEFORE_IMPORT):
    if _leaked_var.startswith("JARVIS_LLM") or _leaked_var.startswith("OPENCODE_"):
        del os.environ[_leaked_var]


@pytest.fixture
def client(monkeypatch):
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    main_module = _main_module

    async def noop_start(self):
        pass

    async def noop_stop(self):
        pass

    monkeypatch.setattr(main_module.opencode_supervisor, "start", noop_start.__get__(main_module.opencode_supervisor))
    monkeypatch.setattr(main_module.opencode_supervisor, "stop", noop_stop.__get__(main_module.opencode_supervisor))

    async def fake_get_status():
        return {"server_alive": False, "server_url": "", "running_tasks": [], "pending_questions": []}
    monkeypatch.setattr(main_module.opencode_supervisor, "get_status", fake_get_status)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        yield c

    if os.path.exists(path):
        os.unlink(path)


def _drain_initial(ws):
    seen_types = set()
    for _ in range(4):
        try:
            msg = ws.receive_json()
        except Exception:
            break
        seen_types.add(msg.get("type"))
        if "opencode_status" in seen_types:
            break
    return seen_types


def test_voice_session_opened_echoes_client_request_id(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({
            "type": "voice_session_open",
            "conversation_id": None,
            "attention_request_id": None,
            "client_request_id": "req-abc-123",
        })
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        assert opened["client_request_id"] == "req-abc-123"


def test_voice_session_opened_omits_client_request_id_when_absent(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({
            "type": "voice_session_open",
            "conversation_id": None,
            "attention_request_id": None,
        })
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        assert opened.get("client_request_id") is None


def test_voice_session_error_echoes_client_request_id(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        _drain_initial(ws1)
        _drain_initial(ws2)
        # Bind the first session to a real attention_request_id so the
        # second open against the same id hits VoiceSessionError (TD-002
        # lease contention) — the actual, real rejection path this
        # correlation mechanism exists to make identifiable.
        db.create_attention_request(
            attention_request_id="ar_test1",
            conversation_id=None,
            task_id=None,
            source_type="test",
            source_id="s1",
            attention_type="question",
            urgency="normal",
            summary="test",
            context_json=None,
            contact_policy=None,
            dedup_key="dedup_test1",
        )
        row_id = "ar_test1"
        ws1.send_json({
            "type": "voice_session_open",
            "conversation_id": None,
            "attention_request_id": row_id,
            "client_request_id": "req-first",
        })
        first_opened = ws1.receive_json()
        assert first_opened["type"] == "voice_session_opened"

        ws2.send_json({
            "type": "voice_session_open",
            "conversation_id": None,
            "attention_request_id": row_id,
            "client_request_id": "req-second",
        })
        error = ws2.receive_json()
        assert error["type"] == "voice_session_error"
        assert error["client_request_id"] == "req-second"


def test_two_concurrent_opens_are_independently_correlated(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        _drain_initial(ws1)
        _drain_initial(ws2)
        ws1.send_json({
            "type": "voice_session_open", "conversation_id": None,
            "attention_request_id": None, "client_request_id": "req-1",
        })
        ws2.send_json({
            "type": "voice_session_open", "conversation_id": None,
            "attention_request_id": None, "client_request_id": "req-2",
        })
        opened1 = ws1.receive_json()
        opened2 = ws2.receive_json()
        assert opened1["client_request_id"] == "req-1"
        assert opened2["client_request_id"] == "req-2"
        assert opened1["voice_session_id"] != opened2["voice_session_id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_voice_session_correlation.py -v`
Expected: FAIL — `KeyError: 'client_request_id'` (the field doesn't exist in the reply yet).

- [ ] **Step 3: Implement the minimal server-side change**

In `app/main.py`, replace the `voice_session_open` handler block (currently lines 449-473) with:

```python
            if data.get("type") == "voice_session_open":
                client_request_id = data.get("client_request_id")
                vs_conv_id = data.get("conversation_id")
                if vs_conv_id and is_valid_conversation_id(vs_conv_id):
                    conversation_id = vs_conv_id
                elif conversation_id is None:
                    conversation_id = new_conversation_id()
                try:
                    session = voice_session_manager.open_session(conversation_id, data.get("attention_request_id"))
                except VoiceSessionError as e:
                    # TD-002: most commonly, another device already holds
                    # the lease on the requested attention_request_id.
                    # client_request_id (ADR-017): echoed back unchanged so
                    # the specific request that failed can be identified
                    # even though no session was ever created.
                    await ws.send_text(json.dumps({
                        "type": "voice_session_error", "voice_session_id": None, "error": str(e),
                        "client_request_id": client_request_id,
                    }))
                    continue
                open_voice_session_id = session["voice_session_id"]
                await ws.send_text(json.dumps({
                    "type": "voice_session_opened",
                    "voice_session_id": session["voice_session_id"],
                    "state": session["state"],
                    "conversation_id": conversation_id,
                    "attention_request_id": session.get("attention_request_id"),
                    "greeting": session.get("greeting"),
                    "client_request_id": client_request_id,
                }))
                continue
```

(Only change: read `client_request_id` from the incoming payload and include it, unchanged, in both possible replies. `json.dumps` naturally omits keys whose value is Python `None`... actually it does **not** omit `None` keys — `json.dumps({"a": None})` produces `'{"a": null}'`, which is what `test_voice_session_opened_omits_client_request_id_when_absent` expects via `.get("client_request_id") is None`. No special-casing needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_voice_session_correlation.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `pytest tests/ -v --tb=short`
Expected: all tests that passed before still pass (441+ baseline, +4 new).

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_voice_session_correlation.py
git commit -m "Add optional client_request_id correlation to voice_session_open protocol (ADR-017)"
```

---

### Task 2: Android — `client_request_id` on `VoiceSession`/`VoiceSessionError` + parser support

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/companion/voice/VoiceSession.kt`
- Modify: `android/app/src/main/java/com/jarvis/companion/voice/VoiceSessionParser.kt`
- Test: `android/app/src/test/java/com/jarvis/companion/voice/VoiceSessionParserTest.kt` (extend existing file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `VoiceSession.clientRequestId: String?`, `VoiceSessionError.clientRequestId: String?`, `VoiceSessionParser.parseOpened`/`parseError` populate them from the new wire field.

- [ ] **Step 1: Write the failing tests**

Add to `VoiceSessionParserTest.kt`:

```kotlin
    @Test
    fun `parseOpened extracts clientRequestId when present`() {
        val json = """{"type":"voice_session_opened","voice_session_id":"vs_1","state":"listening","client_request_id":"req-1"}"""
        val session = VoiceSessionParser.parseOpened(json)
        assertEquals("req-1", session?.clientRequestId)
    }

    @Test
    fun `parseOpened yields null clientRequestId when absent`() {
        val json = """{"type":"voice_session_opened","voice_session_id":"vs_1","state":"listening"}"""
        val session = VoiceSessionParser.parseOpened(json)
        assertNull(session?.clientRequestId)
    }

    @Test
    fun `parseError extracts clientRequestId when present`() {
        val json = """{"type":"voice_session_error","error":"lease held","client_request_id":"req-2"}"""
        val error = VoiceSessionParser.parseError(json)
        assertEquals("req-2", error?.clientRequestId)
    }
```

(Add `import org.junit.Assert.assertNull` if not already imported in this file — check first.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.voice.VoiceSessionParserTest"`
Expected: FAIL — "clientRequestId is not a member of VoiceSession" (compile error).

- [ ] **Step 3: Implement the minimal change**

In `VoiceSession.kt`, add the field to both data classes:

```kotlin
data class VoiceSession(
    val voiceSessionId: String,
    val state: String,
    val conversationId: String?,
    val attentionRequestId: String?,
    val greeting: String?,
    val clientRequestId: String? = null,
)
```

```kotlin
data class VoiceSessionError(
    val voiceSessionId: String?,
    val error: String,
    val clientRequestId: String? = null,
)
```

(Default `null` so every existing call site that constructs these positionally/by-name without the new field — including every existing test — keeps compiling unchanged.)

In `VoiceSessionParser.kt`, extend `parseOpened` and `parseError`:

```kotlin
            VoiceSession(
                voiceSessionId = id,
                state = state,
                conversationId = root.optString("conversation_id", null)?.ifEmpty { null },
                attentionRequestId = root.optString("attention_request_id", null)?.ifEmpty { null },
                greeting = root.optString("greeting", null)?.ifEmpty { null },
                clientRequestId = root.optString("client_request_id", null)?.ifEmpty { null },
            )
```

```kotlin
            VoiceSessionError(
                voiceSessionId = root.optString("voice_session_id", null)?.ifEmpty { null },
                error = error,
                clientRequestId = root.optString("client_request_id", null)?.ifEmpty { null },
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.voice.VoiceSessionParserTest"`
Expected: all tests in this file pass, including the 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/voice/VoiceSession.kt \
        android/app/src/main/java/com/jarvis/companion/voice/VoiceSessionParser.kt \
        android/app/src/test/java/com/jarvis/companion/voice/VoiceSessionParserTest.kt
git commit -m "Parse client_request_id on VoiceSession/VoiceSessionError (ADR-017)"
```

---

### Task 3: Android — `VoiceSessionRepository.openOutcomes` correlation stream

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/companion/voice/VoiceSessionRepository.kt`
- Test: `android/app/src/test/java/com/jarvis/companion/voice/VoiceSessionRepositoryTest.kt` (extend existing file)

**Interfaces:**
- Consumes: `VoiceSession.clientRequestId`, `VoiceSessionError.clientRequestId` (Task 2).
- Produces:
  ```kotlin
  sealed class VoiceSessionOpenOutcome {
      data class Opened(val clientRequestId: String, val session: VoiceSession) : VoiceSessionOpenOutcome()
      data class Failed(val clientRequestId: String?, val error: String) : VoiceSessionOpenOutcome()
  }
  val VoiceSessionRepository.openOutcomes: SharedFlow<VoiceSessionOpenOutcome>
  fun VoiceSessionRepository.applyOpened(session: VoiceSession)   // existing, now also emits an Opened outcome when session.clientRequestId != null
  fun VoiceSessionRepository.applyOpenError(error: VoiceSessionError)   // NEW — separate from applyError(voiceSessionId), used specifically for the immediate open-rejection reply
  ```

This task adds a **new** method, `applyOpenError`, rather than overloading the existing `applyError(voiceSessionId: String?)` — that existing method is called from `CompanionWebSocketClient`'s general error dispatch and must keep its current signature/behavior for the non-open-related error paths untouched (Task 4 will call `applyOpenError` specifically for the `voice_session_error` case, in addition to — not instead of — the existing `applyError` call, since a rejected open should still mean "no current session," which `applyError` already guarantees via `voiceSessionId == null` being treated as unconditional).

- [ ] **Step 1: Write the failing tests**

Add to `VoiceSessionRepositoryTest.kt`:

```kotlin
    @Test
    fun `applyOpened with clientRequestId emits Opened outcome`() = runTest {
        val session = VoiceSession("vs_1", "listening", "conv_1", null, null, clientRequestId = "req-1")
        val outcomes = mutableListOf<VoiceSessionOpenOutcome>()
        val job = launch { repository.openOutcomes.collect { outcomes.add(it) } }
        advanceUntilIdle()
        repository.applyOpened(session)
        advanceUntilIdle()
        job.cancel()

        assertEquals(1, outcomes.size)
        val outcome = outcomes[0] as VoiceSessionOpenOutcome.Opened
        assertEquals("req-1", outcome.clientRequestId)
        assertEquals(session, outcome.session)
    }

    @Test
    fun `applyOpened without clientRequestId emits no outcome`() = runTest {
        val session = VoiceSession("vs_1", "listening", "conv_1", null, null, clientRequestId = null)
        val outcomes = mutableListOf<VoiceSessionOpenOutcome>()
        val job = launch { repository.openOutcomes.collect { outcomes.add(it) } }
        advanceUntilIdle()
        repository.applyOpened(session)
        advanceUntilIdle()
        job.cancel()

        assertEquals(0, outcomes.size)
    }

    @Test
    fun `applyOpenError emits Failed outcome with matching clientRequestId`() = runTest {
        val error = VoiceSessionError(voiceSessionId = null, error = "lease held", clientRequestId = "req-2")
        val outcomes = mutableListOf<VoiceSessionOpenOutcome>()
        val job = launch { repository.openOutcomes.collect { outcomes.add(it) } }
        advanceUntilIdle()
        repository.applyOpenError(error)
        advanceUntilIdle()
        job.cancel()

        assertEquals(1, outcomes.size)
        val outcome = outcomes[0] as VoiceSessionOpenOutcome.Failed
        assertEquals("req-2", outcome.clientRequestId)
        assertEquals("lease held", outcome.error)
    }
```

(This test file will need `kotlinx-coroutines-test`'s `runTest`/`advanceUntilIdle` — already a `testImplementation` dependency per `android/app/build.gradle.kts:74`. Add `import kotlinx.coroutines.launch`, `import kotlinx.coroutines.test.advanceUntilIdle`, `import kotlinx.coroutines.test.runTest` if not already present in this file — check first.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.voice.VoiceSessionRepositoryTest"`
Expected: FAIL — compile error, `openOutcomes`/`VoiceSessionOpenOutcome`/`applyOpenError` don't exist yet.

- [ ] **Step 3: Implement the minimal change**

Add to `VoiceSession.kt` (same file as the data classes from Task 2, so this concept lives alongside what it correlates):

```kotlin
sealed class VoiceSessionOpenOutcome {
    data class Opened(val clientRequestId: String, val session: VoiceSession) : VoiceSessionOpenOutcome()
    data class Failed(val clientRequestId: String?, val error: String) : VoiceSessionOpenOutcome()
}
```

In `VoiceSessionRepository.kt`:

```kotlin
package com.jarvis.companion.voice

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

class VoiceSessionRepository {
    private val _current = MutableStateFlow<VoiceSession?>(null)
    val current: StateFlow<VoiceSession?> = _current.asStateFlow()

    private val _lastResponse = MutableStateFlow<String?>(null)
    val lastResponse: StateFlow<String?> = _lastResponse.asStateFlow()

    // extraBufferCapacity so a slow/late collector (e.g. PresenceService
    // mid-timeout-wait) doesn't cause emit() to suspend or drop under
    // normal single-digit concurrent-open scenarios.
    private val _openOutcomes = MutableSharedFlow<VoiceSessionOpenOutcome>(extraBufferCapacity = 8)
    val openOutcomes: SharedFlow<VoiceSessionOpenOutcome> = _openOutcomes.asSharedFlow()

    fun applyOpened(session: VoiceSession) {
        _current.value = session
        _lastResponse.value = null
        if (session.clientRequestId != null) {
            _openOutcomes.tryEmit(VoiceSessionOpenOutcome.Opened(session.clientRequestId, session))
        }
    }

    fun applyOpenError(error: VoiceSessionError) {
        _openOutcomes.tryEmit(VoiceSessionOpenOutcome.Failed(error.clientRequestId, error.error))
    }

    fun applyResponse(response: VoiceSessionResponse) {
        _current.update { current ->
            if (current?.voiceSessionId != response.voiceSessionId) {
                current
            } else {
                _lastResponse.value = response.response
                current.copy(state = response.voiceSessionState ?: current.state)
            }
        }
    }

    fun applyClosed(voiceSessionId: String?) {
        if (voiceSessionId == null || voiceSessionId == _current.value?.voiceSessionId) {
            _current.value = null
        }
    }

    fun applyError(voiceSessionId: String?) {
        if (voiceSessionId == null || voiceSessionId == _current.value?.voiceSessionId) {
            _current.value = null
        }
    }

    fun applyInvitation() {
        // Intentionally a no-op — matches app.js line 285-291.
    }

    fun clear() {
        _current.value = null
        _lastResponse.value = null
    }
}
```

(Only additions: `_openOutcomes`/`openOutcomes`, the `applyOpened` body gaining its `if (session.clientRequestId != null)` emit, and the new `applyOpenError`. Every other method is byte-for-byte unchanged from what's already in the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.voice.VoiceSessionRepositoryTest"`
Expected: all tests in this file pass, including the 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/voice/VoiceSession.kt \
        android/app/src/main/java/com/jarvis/companion/voice/VoiceSessionRepository.kt \
        android/app/src/test/java/com/jarvis/companion/voice/VoiceSessionRepositoryTest.kt
git commit -m "Add VoiceSessionRepository.openOutcomes correlation stream (ADR-017)"
```

---

### Task 4: Android — wire `client_request_id` through `CompanionWebSocketClient`

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt:162-169` (`sendVoiceSessionOpen`)
- Modify: `android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt:376-382` (inbound dispatch)

**Interfaces:**
- Consumes: `VoiceSessionParser.parseOpened/parseError` (Task 2), `VoiceSessionRepository.applyOpenError` (Task 3).
- Produces: `sendVoiceSessionOpen(conversationId: String?, attentionRequestId: String?, clientRequestId: String? = null): Boolean` — the `= null` default keeps every existing call site (today's mic-tap flow) compiling unchanged.

No dedicated unit test for this class exists today (matches the existing convention — `CompanionWebSocketClient` has no test file; its OkHttp wiring is validated by real-device testing per this project's standing practice). Correctness of the pieces this task wires together is already covered by Tasks 1-3's tests; this task itself is a small, mechanical connection between them.

- [ ] **Step 1: Implement the change**

In `CompanionWebSocketClient.kt`, replace `sendVoiceSessionOpen`:

```kotlin
    /** Milestone 9B.4: mirrors sendAttentionCommand()'s shape/no-op-when-
     * disconnected style for the three voice_session_* client-originated
     * message types (see docs/protocols/websocket-protocol-v1.md and
     * app/main.py's voice_session_open/transcript/close handlers).
     * clientRequestId (Milestone 9B.6, ADR-017): optional generic
     * correlation token, echoed back unchanged on both
     * voice_session_opened and voice_session_error — lets a caller (e.g.
     * WakeWordManager's detection handoff) positively identify the
     * response to *this* specific open request, not just "a session
     * became active." */
    fun sendVoiceSessionOpen(
        conversationId: String?,
        attentionRequestId: String?,
        clientRequestId: String? = null,
    ): Boolean {
        val payload = JSONObject().apply {
            put("type", "voice_session_open")
            if (conversationId != null) put("conversation_id", conversationId)
            if (attentionRequestId != null) put("attention_request_id", attentionRequestId)
            if (clientRequestId != null) put("client_request_id", clientRequestId)
        }
        return webSocket?.send(payload.toString()) ?: false
    }
```

Replace the inbound dispatch block:

```kotlin
        when (type) {
            "voice_session_opened" -> VoiceSessionParser.parseOpened(text)?.let { voiceSessionRepository.applyOpened(it) }
            "voice_session_response" -> VoiceSessionParser.parseResponse(text)?.let { voiceSessionRepository.applyResponse(it) }
            "voice_session_error" -> VoiceSessionParser.parseError(text)?.let {
                voiceSessionRepository.applyError(it.voiceSessionId)
                voiceSessionRepository.applyOpenError(it)
            }
            "voice_session_closed" -> VoiceSessionParser.parseClosed(text)?.let { voiceSessionRepository.applyClosed(it.voiceSessionId) }
```

(`applyError` still runs unconditionally as before — this is additive, not a replacement. `applyOpenError` only has an observable effect when something is actually collecting `openOutcomes` looking for that specific `clientRequestId`; for every existing caller that never sets one, the emitted `Failed(clientRequestId = null, ...)` outcome is simply never matched by anything, per Task 3's `applyOpened`-mirrors-this reasoning.)

- [ ] **Step 2: Compile-check**

Run: `./gradlew compileDebugKotlin`
Expected: BUILD SUCCESSFUL — confirms the default-parameter change didn't break any existing call site (`VoiceActivity.kt`'s two existing `sendVoiceSessionOpen` calls, which don't pass `clientRequestId`).

- [ ] **Step 3: Run the full existing Android test suite to confirm no regression**

Run: `./gradlew testDebugUnitTest`
Expected: all 173+ pre-existing tests still pass, plus Tasks 2-3's new tests.

- [ ] **Step 4: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt
git commit -m "Wire client_request_id through CompanionWebSocketClient (ADR-017)"
```

---

### Task 5: Android — `WakeWordDetection` + `WakeWordConfigRepository`

**Delegation:** safe to delegate to a Builder in parallel with Task 11 (disjoint packages: `wakeword/` vs `diagnostics/`).

**Files:**
- Create: `android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordDetection.kt`
- Create: `android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordConfigRepository.kt`
- Test: `android/app/src/test/java/com/jarvis/companion/wakeword/WakeWordConfigRepositoryTest.kt`

**Interfaces:**
- Consumes: `SecureConfigStore` (`android/.../core/SecureConfigStore.kt`, existing — `getString(key): String?`, `putString(key, value)`, `remove(key)`, `getBoolean`/`putBoolean` if present — **check the actual method set on `SecureConfigStore` before writing this task's implementation**; `PairingRepository` only uses `getString`/`putString`/`remove`, so if boolean/float accessors don't exist, store everything as strings and parse, matching `PairingRepository`'s own `toIntOrNull()`/`toLongOrNull()` pattern for `KEY_PORT`/`KEY_PAIRED_AT`).
- Produces:
  ```kotlin
  data class WakeWordDetection(val detectionId: UUID, val timestampMs: Long, val confidence: Float?, val modelVersion: String)

  class WakeWordConfigRepository(store: SecureConfigStore) {
      fun isEnabled(): Boolean
      fun setEnabled(value: Boolean)
      fun confidenceThreshold(): Float   // default 0.97f
      fun setConfidenceThreshold(value: Float)
      fun modelVersion(): String         // constant "hey_jarvis-v1" for this milestone
      fun diagnosticModeEnabled(): Boolean
      fun setDiagnosticModeEnabled(value: Boolean)
  }
  ```

- [ ] **Step 1: Write the failing tests**

```kotlin
// android/app/src/test/java/com/jarvis/companion/wakeword/WakeWordConfigRepositoryTest.kt
package com.jarvis.companion.wakeword

import com.jarvis.companion.core.SecureConfigStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class WakeWordConfigRepositoryTest {

    private val backing = mutableMapOf<String, String>()
    private lateinit var store: SecureConfigStore
    private lateinit var repository: WakeWordConfigRepository

    @Before
    fun setUp() {
        backing.clear()
        store = mock(SecureConfigStore::class.java)
        `when`(store.getString(org.mockito.ArgumentMatchers.anyString())).thenAnswer { invocation ->
            backing[invocation.getArgument(0)]
        }
        org.mockito.Mockito.doAnswer { invocation ->
            backing[invocation.getArgument(0)] = invocation.getArgument(1)
            null
        }.`when`(store).putString(org.mockito.ArgumentMatchers.anyString(), org.mockito.ArgumentMatchers.anyString())
        repository = WakeWordConfigRepository(store)
    }

    @Test
    fun `isEnabled defaults to false`() {
        assertFalse(repository.isEnabled())
    }

    @Test
    fun `setEnabled persists and reads back`() {
        repository.setEnabled(true)
        assertTrue(repository.isEnabled())
        repository.setEnabled(false)
        assertFalse(repository.isEnabled())
    }

    @Test
    fun `confidenceThreshold defaults to 0_97`() {
        assertEquals(0.97f, repository.confidenceThreshold(), 0.0001f)
    }

    @Test
    fun `setConfidenceThreshold persists and reads back`() {
        repository.setConfidenceThreshold(0.85f)
        assertEquals(0.85f, repository.confidenceThreshold(), 0.0001f)
    }

    @Test
    fun `modelVersion defaults to hey_jarvis-v1`() {
        assertEquals("hey_jarvis-v1", repository.modelVersion())
    }

    @Test
    fun `diagnosticModeEnabled defaults to false`() {
        assertFalse(repository.diagnosticModeEnabled())
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.wakeword.WakeWordConfigRepositoryTest"`
Expected: FAIL — compile error, the classes don't exist yet.

- [ ] **Step 3: Write minimal implementation**

```kotlin
// android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordDetection.kt
package com.jarvis.companion.wakeword

import java.util.UUID

/**
 * One wake-word detection event. [detectionId] is also used, unmodified,
 * as the [com.jarvis.companion.voice] layer's client_request_id when
 * PresenceService requests the resulting VoiceSession open (ADR-017) —
 * one identifier serves both local diagnostics/telemetry correlation and
 * wire-protocol correlation.
 *
 * [confidence] is null until a future native/JNI change exposes the
 * model's raw probability score — today's engine only returns the
 * already-thresholded boolean (disclosed gap, ADR-017).
 */
data class WakeWordDetection(
    val detectionId: UUID,
    val timestampMs: Long,
    val confidence: Float?,
    val modelVersion: String,
)
```

```kotlin
// android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordConfigRepository.kt
package com.jarvis.companion.wakeword

import com.jarvis.companion.core.SecureConfigStore

private const val KEY_ENABLED = "wakeword_enabled"
private const val KEY_THRESHOLD = "wakeword_confidence_threshold"
private const val KEY_DIAGNOSTIC_MODE = "wakeword_diagnostic_mode"

private const val DEFAULT_THRESHOLD = 0.97f

// Bumped manually whenever the bundled hey_jarvis.tflite asset changes —
// no metadata file for a single bundled model, per YAGNI (ADR-017).
private const val MODEL_VERSION = "hey_jarvis-v1"

/** Persists wake-word settings via the app's one SecureConfigStore
 * (PairingRepository's exact pattern) — this app has no data needing a
 * separate encryption domain. Defaults are opt-in-safe: disabled, the
 * real-evidence-backed D5 threshold, diagnostics off. */
class WakeWordConfigRepository(private val store: SecureConfigStore) {
    fun isEnabled(): Boolean = store.getString(KEY_ENABLED)?.toBooleanStrictOrNull() ?: false

    fun setEnabled(value: Boolean) {
        store.putString(KEY_ENABLED, value.toString())
    }

    fun confidenceThreshold(): Float = store.getString(KEY_THRESHOLD)?.toFloatOrNull() ?: DEFAULT_THRESHOLD

    fun setConfidenceThreshold(value: Float) {
        store.putString(KEY_THRESHOLD, value.toString())
    }

    fun modelVersion(): String = MODEL_VERSION

    fun diagnosticModeEnabled(): Boolean = store.getString(KEY_DIAGNOSTIC_MODE)?.toBooleanStrictOrNull() ?: false

    fun setDiagnosticModeEnabled(value: Boolean) {
        store.putString(KEY_DIAGNOSTIC_MODE, value.toString())
    }
}
```

**Before writing this step for real**, read `android/app/src/main/java/com/jarvis/companion/core/SecureConfigStore.kt` in full and confirm `getString`/`putString` are non-`final` (mockable) with exactly this signature — `PairingRepository` already depends on this exact contract, so it should match, but confirm rather than assume.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.wakeword.WakeWordConfigRepositoryTest"`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordDetection.kt \
        android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordConfigRepository.kt \
        android/app/src/test/java/com/jarvis/companion/wakeword/WakeWordConfigRepositoryTest.kt
git commit -m "Add WakeWordDetection and WakeWordConfigRepository (ADR-017)"
```

---

### Task 6: Port the vendored native/JNI layer into the production module

**Files:**
- Create: `android/app/src/main/cpp/CMakeLists.txt`, `Logging.h`, `MicroFrontendWrapper.cpp/.h`, `MicroWakeWordEngine.cpp/.h`, `MicroWakeWord_jni.cpp`, `NOTICE.md` (ported from `spikes/android-wakeword/app/src/main/cpp/`)
- Create: `android/app/src/main/java/com/jarvis/companion/wakeword/MicroWakeWord.kt` (ported from `spikes/android-wakeword/app/src/main/java/com/jarvis/wakewordspike/MicroWakeWord.kt`)
- Create: `android/app/src/main/assets/hey_jarvis.tflite` (copied from the spike)
- Modify: `android/app/build.gradle.kts` (add `ndkVersion`, `externalNativeBuild`, ABI filter)

**Interfaces:**
- Produces: `WakeWordEngine` interface + `MicroWakeWord : WakeWordEngine` (the vendored class, made to conform to a small interface so Task 7 can inject a fake for unit testing — the JNI layer itself cannot run in a JVM unit test).
  ```kotlin
  interface WakeWordEngine : Closeable {
      fun processAudio(samples: ShortArray): Boolean
      fun reset()
  }
  ```

- [ ] **Step 1: Copy the native layer verbatim, minus the HWASan block**

```bash
mkdir -p "D:/Projects/Jarvis/android/app/src/main/cpp"
cp spikes/android-wakeword/app/src/main/cpp/Logging.h android/app/src/main/cpp/
cp spikes/android-wakeword/app/src/main/cpp/MicroFrontendWrapper.cpp android/app/src/main/cpp/
cp spikes/android-wakeword/app/src/main/cpp/MicroFrontendWrapper.h android/app/src/main/cpp/
cp spikes/android-wakeword/app/src/main/cpp/MicroWakeWordEngine.cpp android/app/src/main/cpp/
cp spikes/android-wakeword/app/src/main/cpp/MicroWakeWordEngine.h android/app/src/main/cpp/
cp spikes/android-wakeword/app/src/main/cpp/MicroWakeWord_jni.cpp android/app/src/main/cpp/
cp spikes/android-wakeword/app/src/main/cpp/NOTICE.md android/app/src/main/cpp/
cp spikes/android-wakeword/app/src/main/cpp/CMakeLists.txt android/app/src/main/cpp/
mkdir -p "D:/Projects/Jarvis/android/app/src/main/assets"
cp spikes/android-wakeword/app/src/main/assets/hey_jarvis.tflite android/app/src/main/assets/
```

The copied `CMakeLists.txt` already has the HWASan block gated behind `JARVIS_ENABLE_HWASAN` (fixed in Milestone 9B.5, real-device-confirmed) — off by default, no edit needed.

- [ ] **Step 2: Port the Kotlin JNI wrapper with the package rename**

```bash
mkdir -p "D:/Projects/Jarvis/android/app/src/main/java/com/jarvis/companion/wakeword"
sed 's/^package com\.jarvis\.wakewordspike$/package com.jarvis.companion.wakeword/' \
    spikes/android-wakeword/app/src/main/java/com/jarvis/wakewordspike/MicroWakeWord.kt \
    > android/app/src/main/java/com/jarvis/companion/wakeword/MicroWakeWord.kt
```

- [ ] **Step 3: Add the `WakeWordEngine` interface and conform `MicroWakeWord` to it**

Read the ported `android/app/src/main/java/com/jarvis/companion/wakeword/MicroWakeWord.kt` and change its class declaration line from:

```kotlin
class MicroWakeWord(
```

to:

```kotlin
interface WakeWordEngine : Closeable {
    fun processAudio(samples: ShortArray): Boolean
    fun reset()
}

class MicroWakeWord(
```

and change:

```kotlin
) : Closeable {
```

to:

```kotlin
) : WakeWordEngine {
```

(No other change to the file — `processAudio`/`reset`/`close` already match the interface exactly.)

- [ ] **Step 4: Wire NDK/CMake into the production Gradle module**

In `android/app/build.gradle.kts`, inside the `android { }` block, add (matching the spike's own `build.gradle.kts` values — same target device, same toolchain already installed):

```kotlin
    ndkVersion = "28.2.13676358"
```

right after `compileSdk = 34`, and inside `defaultConfig { }`, add:

```kotlin
        ndk {
            abiFilters += "arm64-v8a"
        }

        externalNativeBuild {
            cmake {
                cppFlags += ""
            }
        }
```

and, as a sibling of `defaultConfig { }`/`buildTypes { }` inside `android { }`:

```kotlin
    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }
```

- [ ] **Step 5: Build to verify the native layer compiles inside the production module**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL — this is the real test for this task (there is no meaningful JVM unit test for "does the NDK/CMake build wire up correctly"; Milestone 9B.5 already real-device-proved the native code itself is correct).

- [ ] **Step 6: Commit**

```bash
git add android/app/src/main/cpp/ android/app/src/main/assets/hey_jarvis.tflite \
        android/app/src/main/java/com/jarvis/companion/wakeword/MicroWakeWord.kt \
        android/app/build.gradle.kts
git commit -m "Port D5 spike's native wake-word engine into production module (ADR-017)"
```

---

### Task 7: `WakeWordManager` — the core state machine

**Files:**
- Create: `android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordManager.kt`
- Create: `android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordAudioFocus.kt`
- Test: `android/app/src/test/java/com/jarvis/companion/wakeword/WakeWordManagerTest.kt`

**Interfaces:**
- Consumes: `WakeWordEngine` (Task 6, injectable), `WakeWordConfigRepository` (Task 5), `WakeWordDetection` (Task 5).
- Produces:
  ```kotlin
  interface WakeWordAudioFocus {
      fun request(onFocusChange: (gained: Boolean) -> Unit): Boolean
      fun abandon()
  }

  class WakeWordManager(
      private val config: WakeWordConfigRepository,
      private val engineFactory: () -> WakeWordEngine,
      private val audioFocus: WakeWordAudioFocus = NoOpWakeWordAudioFocus,
  ) {
      enum class State { STOPPED, LOADING, LISTENING, PAUSED_VOICE_SESSION, PAUSED_AUDIO_FOCUS, ERROR }
      val state: StateFlow<State>
      val detectionCount: StateFlow<Int>
      val lastDetectionAtMs: StateFlow<Long?>
      val avgLatencyMs: StateFlow<Double>
      val maxLatencyMs: StateFlow<Double>
      val onDetected: SharedFlow<WakeWordDetection>
      fun start()
      fun stop()
      fun pauseForVoiceSession()
      fun resumeAfterVoiceSession()
  }
  ```
  `WakeWordAudioFocus` is `WakeWordManager`'s own lightweight, self-contained audio-focus request (ADR-017 Section D) — deliberately **not** `AudioFocusManager` (`com.jarvis.companion.audio`), whose own doc comment already scopes it away from microphone/wake-word involvement. `NoOpWakeWordAudioFocus` is the default for the primary (test-facing) constructor so Step 1's existing-shaped tests don't need an audio-focus double unless they're specifically testing that behavior. Real `AudioRecord` capture and the real `AudioManager` focus request are deliberately **not** covered by most of this task's unit tests — they require a real Android runtime; real capture is exercised in Task 14 (real-device validation). The audio-focus *state transition logic* (as opposed to the real `AudioManager` call) is unit-tested via an injected fake, same as the engine.

- [ ] **Step 1: Write the failing tests**

```kotlin
// android/app/src/test/java/com/jarvis/companion/wakeword/WakeWordManagerTest.kt
package com.jarvis.companion.wakeword

import com.jarvis.companion.core.SecureConfigStore
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

private class FakeWakeWordEngine(private val detectOnCall: Int?) : WakeWordEngine {
    var callCount = 0
    var closed = false
    override fun processAudio(samples: ShortArray): Boolean {
        callCount++
        return detectOnCall == callCount
    }
    override fun reset() {}
    override fun close() { closed = true }
}

private class FakeWakeWordAudioFocus : WakeWordAudioFocus {
    var requested = false
    var abandoned = false
    private var callback: ((Boolean) -> Unit)? = null
    override fun request(onFocusChange: (Boolean) -> Unit): Boolean {
        requested = true
        callback = onFocusChange
        return true
    }
    override fun abandon() { abandoned = true }
    fun simulateFocusLost() { callback?.invoke(false) }
    fun simulateFocusRegained() { callback?.invoke(true) }
}

@OptIn(ExperimentalCoroutinesApi::class)
class WakeWordManagerTest {

    private lateinit var config: WakeWordConfigRepository
    private lateinit var store: SecureConfigStore

    @Before
    fun setUp() {
        store = mock(SecureConfigStore::class.java)
        `when`(store.getString(org.mockito.ArgumentMatchers.eq("wakeword_enabled"))).thenReturn("true")
        config = WakeWordConfigRepository(store)
    }

    @Test
    fun `initial state is STOPPED`() {
        val manager = WakeWordManager(config) { FakeWakeWordEngine(null) }
        assertEquals(WakeWordManager.State.STOPPED, manager.state.value)
    }

    @Test
    fun `start is a no-op when config is disabled`() = runTest {
        `when`(store.getString(org.mockito.ArgumentMatchers.eq("wakeword_enabled"))).thenReturn("false")
        val manager = WakeWordManager(config) { FakeWakeWordEngine(null) }
        manager.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.STOPPED, manager.state.value)
    }

    @Test
    fun `pauseForVoiceSession transitions from LISTENING to PAUSED_VOICE_SESSION`() = runTest {
        val manager = WakeWordManager(config) { FakeWakeWordEngine(null) }
        manager.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, manager.state.value)

        manager.pauseForVoiceSession()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.PAUSED_VOICE_SESSION, manager.state.value)
    }

    @Test
    fun `resumeAfterVoiceSession transitions back to LISTENING`() = runTest {
        val manager = WakeWordManager(config) { FakeWakeWordEngine(null) }
        manager.start()
        advanceUntilIdle()
        manager.pauseForVoiceSession()
        advanceUntilIdle()

        manager.resumeAfterVoiceSession()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, manager.state.value)
    }

    @Test
    fun `audio focus loss pauses and regain resumes listening`() = runTest {
        val audioFocus = FakeWakeWordAudioFocus()
        val manager = WakeWordManager(config, { FakeWakeWordEngine(null) }, audioFocus)
        manager.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, manager.state.value)
        assertEquals(true, audioFocus.requested)

        audioFocus.simulateFocusLost()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.PAUSED_AUDIO_FOCUS, manager.state.value)

        audioFocus.simulateFocusRegained()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, manager.state.value)
    }

    @Test
    fun `stop abandons audio focus`() = runTest {
        val audioFocus = FakeWakeWordAudioFocus()
        val manager = WakeWordManager(config, { FakeWakeWordEngine(null) }, audioFocus)
        manager.start()
        advanceUntilIdle()
        manager.stop()
        advanceUntilIdle()
        assertEquals(true, audioFocus.abandoned)
    }

    @Test
    fun `detection self-pauses and increments detectionCount`() = runTest {
        val manager = WakeWordManager(config) { FakeWakeWordEngine(detectOnCall = 1) }
        val detections = mutableListOf<WakeWordDetection>()
        val job = launch { manager.onDetected.collect { detections.add(it) } }

        manager.start()
        manager.feedAudioForTest(ShortArray(160))
        advanceUntilIdle()

        assertEquals(1, detections.size)
        assertEquals(1, manager.detectionCount.value)
        assertEquals(WakeWordManager.State.PAUSED_VOICE_SESSION, manager.state.value)
        job.cancel()
    }

    @Test
    fun `stop tears down cleanly and can be started again`() = runTest {
        val manager = WakeWordManager(config) { FakeWakeWordEngine(null) }
        manager.start()
        advanceUntilIdle()
        manager.stop()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.STOPPED, manager.state.value)

        manager.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, manager.state.value)
    }
}
```

Note: `feedAudioForTest` is a package-private (or `@VisibleForTesting internal`) seam — the real capture loop reads from `AudioRecord`, which doesn't exist in a JVM unit test, so the manager needs an injectable way to feed a sample chunk directly for the "does a detection self-pause and emit correctly" test. Define it as `internal fun feedAudioForTest(samples: ShortArray)` that calls the same private `onAudioChunk(samples)` the real capture loop calls internally — not a parallel code path.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.wakeword.WakeWordManagerTest"`
Expected: FAIL — `WakeWordManager` doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

```kotlin
// android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordManager.kt
package com.jarvis.companion.wakeword

import android.content.Context
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.util.UUID
import kotlin.math.max
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val SAMPLE_RATE = 16000
private const val FEATURE_STEP_SIZE_MS = 10
private const val SLIDING_WINDOW_SIZE = 5
private const val CHUNK_SAMPLES = SAMPLE_RATE / 1000 * FEATURE_STEP_SIZE_MS

/**
 * Single owner of wake-word model lifecycle, AudioRecord lifecycle, the
 * inference loop, detection state, and diagnostics (ADR-017). Knows
 * nothing about networking, VoiceActivity, the WebSocket protocol, the
 * VoiceSession protocol, or UI — PresenceService orchestrates all of
 * that by observing [state]/[onDetected].
 *
 * [engineFactory] is injected (rather than constructing [MicroWakeWord]
 * directly) so unit tests can substitute a fake — real inference requires
 * the native library and cannot run in a JVM unit test.
 */
class WakeWordManager(
    private val config: WakeWordConfigRepository,
    private val engineFactory: () -> WakeWordEngine,
    private val audioFocus: WakeWordAudioFocus = NoOpWakeWordAudioFocus,
) {
    enum class State { STOPPED, LOADING, LISTENING, PAUSED_VOICE_SESSION, PAUSED_AUDIO_FOCUS, ERROR }

    constructor(context: Context, config: WakeWordConfigRepository) : this(
        config = config,
        engineFactory = {
            val afd = context.assets.openFd("hey_jarvis.tflite")
            val modelBuffer: ByteBuffer = FileInputStream(afd.fileDescriptor).use { input ->
                input.channel.map(FileChannel.MapMode.READ_ONLY, afd.startOffset, afd.declaredLength)
            }
            MicroWakeWord(
                modelBuffer = modelBuffer,
                featureStepSizeMs = FEATURE_STEP_SIZE_MS,
                probabilityCutoff = config.confidenceThreshold(),
                slidingWindowSize = SLIDING_WINDOW_SIZE,
            )
        },
        audioFocus = AndroidWakeWordAudioFocus(context),
    )

    private val _state = MutableStateFlow(State.STOPPED)
    val state: StateFlow<State> = _state.asStateFlow()

    private val _detectionCount = MutableStateFlow(0)
    val detectionCount: StateFlow<Int> = _detectionCount.asStateFlow()

    private val _lastDetectionAtMs = MutableStateFlow<Long?>(null)
    val lastDetectionAtMs: StateFlow<Long?> = _lastDetectionAtMs.asStateFlow()

    private val _avgLatencyMs = MutableStateFlow(0.0)
    val avgLatencyMs: StateFlow<Double> = _avgLatencyMs.asStateFlow()

    private val _maxLatencyMs = MutableStateFlow(0.0)
    val maxLatencyMs: StateFlow<Double> = _maxLatencyMs.asStateFlow()

    private val _onDetected = MutableSharedFlow<WakeWordDetection>(extraBufferCapacity = 4)
    val onDetected: SharedFlow<WakeWordDetection> = _onDetected.asSharedFlow()

    private var scope: CoroutineScope? = null
    private var captureJob: Job? = null
    private var engine: WakeWordEngine? = null
    private var audioRecord: AudioRecord? = null
    private var callCount = 0L
    private var totalLatencyMs = 0.0

    fun start() {
        if (_state.value != State.STOPPED) return
        if (!config.isEnabled()) return
        _state.value = State.LOADING
        val activeScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        scope = activeScope
        engine = engineFactory()
        callCount = 0
        totalLatencyMs = 0.0
        _detectionCount.value = 0
        _lastDetectionAtMs.value = null
        _avgLatencyMs.value = 0.0
        _maxLatencyMs.value = 0.0
        _state.value = State.LISTENING
        audioFocus.request { gained -> if (gained) resumeFromAudioFocusLoss() else pauseForAudioFocusLoss() }
        captureJob = activeScope.launch { runCaptureLoop(activeScope) }
    }

    fun stop() {
        captureJob?.cancel()
        captureJob = null
        scope?.let { if (it.isActive) { /* SupervisorJob cancelled via captureJob above; nothing else to do */ } }
        audioFocus.abandon()
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
        engine?.close()
        engine = null
        _state.value = State.STOPPED
    }

    // Reacts to a real phone call or another app taking exclusive audio
    // focus — independent of AudioFocusManager (com.jarvis.companion.audio),
    // which is explicitly scoped away from microphone/wake-word
    // involvement (ADR-016, ADR-017 Section D). Private: driven by the OS
    // callback registered in start(), not called externally like
    // pauseForVoiceSession()/resumeAfterVoiceSession() are.
    private fun pauseForAudioFocusLoss() {
        if (_state.value == State.LISTENING) _state.value = State.PAUSED_AUDIO_FOCUS
    }

    private fun resumeFromAudioFocusLoss() {
        if (_state.value == State.PAUSED_AUDIO_FOCUS) _state.value = State.LISTENING
    }

    fun pauseForVoiceSession() {
        if (_state.value == State.LISTENING) _state.value = State.PAUSED_VOICE_SESSION
    }

    fun resumeAfterVoiceSession() {
        if (_state.value == State.PAUSED_VOICE_SESSION) _state.value = State.LISTENING
    }

    /** Test-only seam: feeds one chunk directly to [onAudioChunk], bypassing
     * AudioRecord (which doesn't exist in a JVM unit test). Production
     * capture calls the exact same function. */
    internal fun feedAudioForTest(samples: ShortArray) {
        onAudioChunk(samples)
    }

    private fun onAudioChunk(samples: ShortArray) {
        if (_state.value != State.LISTENING) return
        val currentEngine = engine ?: return
        val t0 = System.nanoTime()
        val detected = currentEngine.processAudio(samples)
        val elapsedMs = (System.nanoTime() - t0) / 1_000_000.0

        callCount++
        totalLatencyMs += elapsedMs
        _avgLatencyMs.value = totalLatencyMs / callCount
        if (elapsedMs > _maxLatencyMs.value) _maxLatencyMs.value = elapsedMs

        if (detected) {
            currentEngine.reset()
            _detectionCount.value = _detectionCount.value + 1
            val now = System.currentTimeMillis()
            _lastDetectionAtMs.value = now
            _state.value = State.PAUSED_VOICE_SESSION
            _onDetected.tryEmit(
                WakeWordDetection(
                    detectionId = UUID.randomUUID(),
                    timestampMs = now,
                    confidence = null,
                    modelVersion = config.modelVersion(),
                ),
            )
        }
    }

    private suspend fun runCaptureLoop(activeScope: CoroutineScope) {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = max(minBuf, CHUNK_SAMPLES * 2 * 4)
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferSize,
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            _state.value = State.ERROR
            record.release()
            return
        }
        audioRecord = record
        record.startRecording()
        val buf = ShortArray(CHUNK_SAMPLES)
        while (activeScope.isActive) {
            val read = record.read(buf, 0, buf.size)
            if (read <= 0) continue
            val samples = if (read == buf.size) buf else buf.copyOf(read)
            onAudioChunk(samples)
        }
    }
}
```

Create `WakeWordAudioFocus.kt` alongside it:

```kotlin
// android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordAudioFocus.kt
package com.jarvis.companion.wakeword

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager

/**
 * WakeWordManager's own lightweight audio-focus request (ADR-017 Section
 * D). Deliberately independent of com.jarvis.companion.audio.AudioFocusManager
 * — that class's own doc comment scopes it away from microphone/wake-word
 * involvement. AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK, not the exclusive GAIN
 * VoiceActivity's conversation flow uses: a real incoming phone call
 * correctly preempts wake-word listening without either component needing
 * to know about the other.
 */
interface WakeWordAudioFocus {
    fun request(onFocusChange: (gained: Boolean) -> Unit): Boolean
    fun abandon()
}

object NoOpWakeWordAudioFocus : WakeWordAudioFocus {
    override fun request(onFocusChange: (Boolean) -> Unit): Boolean = true
    override fun abandon() {}
}

class AndroidWakeWordAudioFocus(context: Context) : WakeWordAudioFocus {
    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var focusRequest: AudioFocusRequest? = null

    override fun request(onFocusChange: (Boolean) -> Unit): Boolean {
        val listener = AudioManager.OnAudioFocusChangeListener { change ->
            val gained = change == AudioManager.AUDIOFOCUS_GAIN
            onFocusChange(gained)
        }
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setOnAudioFocusChangeListener(listener)
            .build()
        focusRequest = request
        return audioManager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }

    override fun abandon() {
        focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
        focusRequest = null
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.wakeword.WakeWordManagerTest"`
Expected: all 8 tests pass (6 from Step 1's original block + the 2 audio-focus tests added during this plan's self-review). (`testOptions.unitTests.isReturnDefaultValues = true` in `build.gradle.kts:58` means any accidental real-`AudioRecord`-constructing code path returns defaults rather than crashing — but this test suite never exercises `runCaptureLoop` at all, only `feedAudioForTest`, so this shouldn't matter; confirm it doesn't during Step 4.)

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordManager.kt \
        android/app/src/main/java/com/jarvis/companion/wakeword/WakeWordAudioFocus.kt \
        android/app/src/test/java/com/jarvis/companion/wakeword/WakeWordManagerTest.kt
git commit -m "Add WakeWordManager state machine with its own audio-focus handling (ADR-017)"
```

---

### Task 8: Manifest — foreground-service microphone typing

**Files:**
- Modify: `android/app/src/main/AndroidManifest.xml`

**Interfaces:** none (declarative only).

- [ ] **Step 1: Add the permission**

Add alongside the existing `FOREGROUND_SERVICE_DATA_SYNC` permission (manifest line 8):

```xml
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MICROPHONE" />
```

- [ ] **Step 2: Extend the service's declared type**

Change (manifest line 48):

```xml
            android:foregroundServiceType="dataSync" />
```

to:

```xml
            android:foregroundServiceType="dataSync|microphone" />
```

- [ ] **Step 3: Build to verify the manifest is well-formed**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add android/app/src/main/AndroidManifest.xml
git commit -m "Add FOREGROUND_SERVICE_MICROPHONE for PresenceService (ADR-017)"
```

(This task is deliberately small and bundled ahead of Task 9 rather than folded into it — a reviewer can verify the manifest change in isolation before the larger `PresenceService` wiring lands.)

---

### Task 9: `PresenceService` — host `WakeWordManager` and the confirmation-gated handoff

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/companion/service/PresenceService.kt`
- Modify: `android/app/src/main/java/com/jarvis/companion/JarvisCompanionApp.kt` (add the `WakeWordConfigRepository` singleton, mirroring `pairingRepository`)

**Interfaces:**
- Consumes: `WakeWordManager` (Task 7), `VoiceSessionRepository.openOutcomes` (Task 3), `CompanionWebSocketClient.sendVoiceSessionOpen` (Task 4).
- Produces: `PresenceService.activeWakeWordManager: WakeWordManager?` (companion object, mirroring the existing `activeClient` pattern) — consumed by Task 11 (Diagnostics) and Task 12 (Settings toggle).

No dedicated unit test — matches this project's existing, explicit precedent: `PresenceService` itself has no unit test file today (it's a `Service`, validated by real-device testing, same as its existing WebSocket-client wiring). Task 14 covers this task's actual correctness with real evidence.

- [ ] **Step 1: Add the `wakeWordConfigRepository` singleton**

In `JarvisCompanionApp.kt`, find where `pairingRepository` is constructed and add a sibling:

```kotlin
    val wakeWordConfigRepository: WakeWordConfigRepository by lazy { WakeWordConfigRepository(secureConfigStore) }
```

(Match whatever the existing `pairingRepository`/`secureConfigStore` construction pattern actually is in this file — read it first; don't guess the exact surrounding property names.)

- [ ] **Step 2: Implement `PresenceService`'s wake-word hosting**

Add to `PresenceService.kt`. In `onCreate()`, after the existing widget-refresh `serviceScope.launch { ... }` block:

```kotlin
        wakeWordManager = WakeWordManager(applicationContext, app.wakeWordConfigRepository)
        activeWakeWordManager = wakeWordManager

        serviceScope.launch {
            app.voiceSessionRepository.current.collect { session ->
                if (session != null) wakeWordManager.pauseForVoiceSession() else wakeWordManager.resumeAfterVoiceSession()
            }
        }

        serviceScope.launch {
            wakeWordManager.onDetected.collect { detection -> handleWakeWordDetection(detection) }
        }
```

Add the field declaration alongside the other `private var`/`private lateinit var` fields:

```kotlin
    private lateinit var wakeWordManager: WakeWordManager
```

Add the handoff function as a private method:

```kotlin
    private suspend fun handleWakeWordDetection(detection: WakeWordDetection) {
        telemetry.record(TelemetryRecorder.WAKEWORD_DETECTED, "detectionId=${detection.detectionId}")
        val requestId = detection.detectionId.toString()
        val sent = connectionClient?.sendVoiceSessionOpen(
            conversationId = null,
            attentionRequestId = null,
            clientRequestId = requestId,
        ) ?: false
        if (!sent) {
            telemetry.record(TelemetryRecorder.WAKEWORD_OPEN_NOT_SENT, "detectionId=$requestId")
            wakeWordManager.resumeAfterVoiceSession()
            return
        }

        val outcome = kotlinx.coroutines.withTimeoutOrNull(WAKEWORD_OPEN_CONFIRM_TIMEOUT_MS) {
            app.voiceSessionRepository.openOutcomes
                .filter { it.matchesRequestId(requestId) }
                .first()
        }

        when (outcome) {
            is VoiceSessionOpenOutcome.Opened -> {
                telemetry.record(TelemetryRecorder.WAKEWORD_HANDOFF_LAUNCHED, "detectionId=$requestId voiceSessionId=${outcome.session.voiceSessionId}")
                startActivity(
                    Intent(this, VoiceActivity::class.java)
                        .putExtra(VoiceActivity.EXTRA_LAUNCHED_BY_WAKEWORD, true)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }
            is VoiceSessionOpenOutcome.Failed -> {
                telemetry.record(TelemetryRecorder.WAKEWORD_OPEN_REJECTED, "detectionId=$requestId error=${outcome.error}")
                wakeWordManager.resumeAfterVoiceSession()
            }
            null -> {
                telemetry.record(TelemetryRecorder.WAKEWORD_OPEN_TIMED_OUT, "detectionId=$requestId")
                wakeWordManager.resumeAfterVoiceSession()
            }
        }
    }
```

Add the small correlation-matching helper and imports. In `VoiceSession.kt` (alongside `VoiceSessionOpenOutcome` from Task 3), add:

```kotlin
fun VoiceSessionOpenOutcome.matchesRequestId(requestId: String): Boolean = when (this) {
    is VoiceSessionOpenOutcome.Opened -> clientRequestId == requestId
    is VoiceSessionOpenOutcome.Failed -> clientRequestId == requestId
}
```

Add imports to `PresenceService.kt`: `com.jarvis.companion.wakeword.WakeWordManager`, `com.jarvis.companion.wakeword.WakeWordDetection`, `com.jarvis.companion.voice.VoiceSessionOpenOutcome`, `com.jarvis.companion.voice.matchesRequestId`, `com.jarvis.companion.ui.VoiceActivity`, `kotlinx.coroutines.flow.filter`, `kotlinx.coroutines.flow.first`.

Add a module-level constant near the top of the file: `private const val WAKEWORD_OPEN_CONFIRM_TIMEOUT_MS = 5000L`.

Add `PRESENCE`-companion-object field, mirroring `activeClient`:

```kotlin
        @Volatile
        var activeWakeWordManager: WakeWordManager? = null
            private set
```

In `onStartCommand()`, after the existing `client.start(pairingConfig)` call, add:

```kotlin
        wakeWordManager.start()
```

In `onDestroy()`, alongside the existing `connectionClient?.stop()`:

```kotlin
        wakeWordManager.stop()
        activeWakeWordManager = null
```

- [ ] **Step 2: Add the four new telemetry event constants**

In `TelemetryRecorder.kt`, add alongside the existing event-name constants (match the existing naming convention exactly — read the file first to confirm the constant style, e.g. `const val WS_CONNECTED = "WS_CONNECTED"`):

```kotlin
    const val WAKEWORD_DETECTED = "WAKEWORD_DETECTED"
    const val WAKEWORD_OPEN_NOT_SENT = "WAKEWORD_OPEN_NOT_SENT"
    const val WAKEWORD_HANDOFF_LAUNCHED = "WAKEWORD_HANDOFF_LAUNCHED"
    const val WAKEWORD_OPEN_REJECTED = "WAKEWORD_OPEN_REJECTED"
    const val WAKEWORD_OPEN_TIMED_OUT = "WAKEWORD_OPEN_TIMED_OUT"
```

- [ ] **Step 3: Build**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Run the full Android unit test suite to confirm no regression**

Run: `./gradlew testDebugUnitTest`
Expected: all pre-existing tests still pass (this task adds no new unit tests of its own, per this task's stated rationale — real evidence comes from Task 14).

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/service/PresenceService.kt \
        android/app/src/main/java/com/jarvis/companion/JarvisCompanionApp.kt \
        android/app/src/main/java/com/jarvis/companion/voice/VoiceSession.kt \
        android/app/src/main/java/com/jarvis/companion/telemetry/TelemetryRecorder.kt
git commit -m "PresenceService hosts WakeWordManager with confirmation-gated handoff (ADR-017)"
```

---

### Task 10: `VoiceActivity` — auto-start listening when launched by wake word

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/companion/ui/VoiceActivity.kt`
- Test: `android/app/src/test/java/com/jarvis/companion/ui/VoiceActivityTest.kt` (extend existing file)

**Interfaces:**
- Produces: `VoiceActivity.EXTRA_LAUNCHED_BY_WAKEWORD: String` (companion constant, consumed by Task 9's `Intent` construction).

- [ ] **Step 1: Read the existing `VoiceActivityTest.kt` first**

This file already exists (110 new tests were added for it in M9B.4) — read its current fakes/setup before adding to it, to match its existing test-double pattern exactly rather than inventing a second one.

- [ ] **Step 2: Write the failing test**

Add a test following this file's existing pattern (exact shape depends on what's read in Step 1, but the assertion is):

```kotlin
    @Test
    fun `EXTRA_LAUNCHED_BY_WAKEWORD triggers auto-listen on create`() {
        // Arrange: launch the activity with the intent extra set to true,
        // RECORD_AUDIO already granted (matches this feature's precondition
        // — wake-word detection itself cannot run without the permission
        // already being granted).
        // Assert: speechInputController (or its test double) received a
        // startListening() call without any simulated mic-button tap.
    }
```

(Written as a real test matching the existing file's fake-`SpeechInputController`/Robolectric-free JVM-testable pattern once Step 1's read confirms the exact mechanism — this task's implementer must not skip Step 1.)

- [ ] **Step 3: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.ui.VoiceActivityTest"`
Expected: FAIL — the new test's assertion doesn't hold yet (`EXTRA_LAUNCHED_BY_WAKEWORD` doesn't exist / auto-listen doesn't happen).

- [ ] **Step 4: Implement the minimal change**

Add the companion constant alongside the existing `EXTRA_ATTENTION_REQUEST_ID`:

```kotlin
        const val EXTRA_LAUNCHED_BY_WAKEWORD = "com.jarvis.companion.EXTRA_LAUNCHED_BY_WAKEWORD"
```

In `onCreate()`, after the existing bound-session-open block (lines 88-94 in the current file):

```kotlin
        val launchedByWakeWord = intent.getBooleanExtra(EXTRA_LAUNCHED_BY_WAKEWORD, false)
        if (launchedByWakeWord && attentionRequestId == null) {
            // The wake word itself already required RECORD_AUDIO to be
            // granted (WakeWordManager cannot run otherwise) — no
            // permission check needed here, unlike onMicTap(). The
            // VoiceSession was already opened and confirmed by
            // PresenceService before this Activity was launched
            // (ADR-017's "a VoiceActivity only exists when a real
            // VoiceSession exists" invariant), so this goes straight to
            // listening rather than through startListening()'s normal
            // lazy-open-on-transcript path.
            startListening()
        }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.ui.VoiceActivityTest"`
Expected: PASS, including all pre-existing tests in this file (110+ from M9B.4).

- [ ] **Step 6: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/ui/VoiceActivity.kt \
        android/app/src/test/java/com/jarvis/companion/ui/VoiceActivityTest.kt
git commit -m "VoiceActivity auto-starts listening when launched by wake word (ADR-017)"
```

---

### Task 11: Diagnostics — `WakeWordDiagnostics`

**Delegation:** safe to delegate to a Builder in parallel with Task 5 (disjoint packages). This task only needs the field *names* on `WakeWordManager`/`WakeWordConfigRepository` already fixed by ADR-017 and Task 7's interface — assign this task only after Task 7's public interface (not necessarily its full implementation) is fixed, matching M9B.4 Wave 2's "fixed pre-specified method-name contract" pattern for parallel-safe delegation.

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/companion/diagnostics/DiagnosticsSnapshot.kt`
- Modify: `android/app/src/main/java/com/jarvis/companion/diagnostics/DiagnosticsRepository.kt`
- Modify: `android/app/src/main/java/com/jarvis/companion/ui/DiagnosticsActivity.kt`
- Test: `android/app/src/test/java/com/jarvis/companion/diagnostics/WakeWordDiagnosticsTest.kt` (new, mirrors the existing `VoiceDiagnosticsTest.kt`)

**Interfaces:**
- Consumes: `PresenceService.activeWakeWordManager` (Task 9), `WakeWordManager.state/detectionCount/lastDetectionAtMs/avgLatencyMs/maxLatencyMs` (Task 7), `WakeWordConfigRepository.isEnabled/modelVersion` (Task 5).
- Produces: `WakeWordDiagnostics` data class, `buildWakeWordDiagnostics(...)` pure function.

- [ ] **Step 1: Read `VoiceDiagnosticsTest.kt` first** to match its exact test style.

- [ ] **Step 2: Write the failing test**

```kotlin
// android/app/src/test/java/com/jarvis/companion/diagnostics/WakeWordDiagnosticsTest.kt
package com.jarvis.companion.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Test

class WakeWordDiagnosticsTest {

    @Test
    fun `buildWakeWordDiagnostics assembles all fields`() {
        val result = buildWakeWordDiagnostics(
            enabled = true,
            modelLoaded = true,
            modelVersion = "hey_jarvis-v1",
            state = "LISTENING",
            lastDetectionAtMs = 1_000_000L,
            detectionCount = 3,
            avgLatencyMs = 0.12,
            maxLatencyMs = 9.04,
        )
        assertEquals(true, result.enabled)
        assertEquals(true, result.modelLoaded)
        assertEquals("hey_jarvis-v1", result.modelVersion)
        assertEquals("LISTENING", result.state)
        assertEquals(3, result.detectionCount)
        assertEquals(0.12, result.avgLatencyMs!!, 0.0001)
        assertEquals(9.04, result.maxLatencyMs!!, 0.0001)
    }

    @Test
    fun `buildWakeWordDiagnostics handles absent-safe nulls when nothing is active`() {
        val result = buildWakeWordDiagnostics(
            enabled = false,
            modelLoaded = null,
            modelVersion = null,
            state = null,
            lastDetectionAtMs = null,
            detectionCount = null,
            avgLatencyMs = null,
            maxLatencyMs = null,
        )
        assertEquals(false, result.enabled)
        assertEquals(null, result.modelLoaded)
        assertEquals(null, result.lastDetectionAgoMs)
    }
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.diagnostics.WakeWordDiagnosticsTest"`
Expected: FAIL — `WakeWordDiagnostics`/`buildWakeWordDiagnostics` don't exist yet.

- [ ] **Step 4: Implement the minimal change**

Add to `DiagnosticsSnapshot.kt`, mirroring `VoiceDiagnostics`'s exact shape:

```kotlin
/**
 * Wake-word feature diagnostics snapshot (Milestone 9B.6, ADR-017).
 * Every field is nullable/absent-safe. No raw audio, no confidence
 * history, no transcripts.
 */
data class WakeWordDiagnostics(
    val enabled: Boolean,
    val modelLoaded: Boolean?,
    val modelVersion: String?,
    val state: String?,
    val lastDetectionAgoMs: Long?,
    val detectionCount: Int?,
    val avgLatencyMs: Double?,
    val maxLatencyMs: Double?,
) {
    fun formatted(): String {
        fun ms(value: Long?): String = if (value == null) "n/a" else "${value / 1000}s"
        return buildString {
            appendLine("wakeword_enabled=$enabled")
            appendLine("wakeword_model_loaded=${modelLoaded ?: "n/a"}")
            appendLine("wakeword_model_version=${modelVersion ?: "n/a"}")
            appendLine("wakeword_state=${state ?: "n/a"}")
            appendLine("wakeword_last_detection_ago=${ms(lastDetectionAgoMs)}")
            appendLine("wakeword_detection_count=${detectionCount ?: "n/a"}")
            appendLine("wakeword_avg_latency_ms=${avgLatencyMs?.let { "%.2f".format(it) } ?: "n/a"}")
            append("wakeword_max_latency_ms=${maxLatencyMs?.let { "%.2f".format(it) } ?: "n/a"}")
        }
    }
}
```

Add to `DiagnosticsRepository.kt`, mirroring `buildVoiceDiagnostics`'s exact shape:

```kotlin
fun buildWakeWordDiagnostics(
    enabled: Boolean,
    modelLoaded: Boolean?,
    modelVersion: String?,
    state: String?,
    lastDetectionAtMs: Long?,
    detectionCount: Int?,
    avgLatencyMs: Double?,
    maxLatencyMs: Double?,
): WakeWordDiagnostics {
    val lastDetectionAgoMs = lastDetectionAtMs?.let { System.currentTimeMillis() - it }
    return WakeWordDiagnostics(
        enabled = enabled,
        modelLoaded = modelLoaded,
        modelVersion = modelVersion,
        state = state,
        lastDetectionAgoMs = lastDetectionAgoMs,
        detectionCount = detectionCount,
        avgLatencyMs = avgLatencyMs,
        maxLatencyMs = maxLatencyMs,
    )
}
```

In `DiagnosticsActivity.kt`, alongside the existing `buildVoiceDiagnostics(...)` call site, add:

```kotlin
        val wakeWord = buildWakeWordDiagnostics(
            enabled = app.wakeWordConfigRepository.isEnabled(),
            modelLoaded = PresenceService.activeWakeWordManager?.let { it.state.value != WakeWordManager.State.STOPPED },
            modelVersion = app.wakeWordConfigRepository.modelVersion(),
            state = PresenceService.activeWakeWordManager?.state?.value?.name,
            lastDetectionAtMs = PresenceService.activeWakeWordManager?.lastDetectionAtMs?.value,
            detectionCount = PresenceService.activeWakeWordManager?.detectionCount?.value,
            avgLatencyMs = PresenceService.activeWakeWordManager?.avgLatencyMs?.value,
            maxLatencyMs = PresenceService.activeWakeWordManager?.maxLatencyMs?.value,
        )
```

and render `wakeWord.formatted()` into the screen the same way the existing `voice.formatted()` result is rendered (match the exact existing rendering call — read it first).

- [ ] **Step 5: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "com.jarvis.companion.diagnostics.WakeWordDiagnosticsTest"`
Expected: both tests pass.

- [ ] **Step 6: Build the full app**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 7: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/diagnostics/DiagnosticsSnapshot.kt \
        android/app/src/main/java/com/jarvis/companion/diagnostics/DiagnosticsRepository.kt \
        android/app/src/main/java/com/jarvis/companion/ui/DiagnosticsActivity.kt \
        android/app/src/test/java/com/jarvis/companion/diagnostics/WakeWordDiagnosticsTest.kt
git commit -m "Add WakeWordDiagnostics to the Diagnostics screen (ADR-017)"
```

---

### Task 12: `SettingsActivity` — enable/disable toggle

**Delegation:** safe to delegate — self-contained addition to an existing screen, depends only on already-fixed interfaces (`WakeWordConfigRepository`, `PresenceService.activeWakeWordManager`, `PermissionsHelper`).

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/companion/ui/SettingsActivity.kt`
- Modify: `android/app/src/main/res/layout/activity_settings.xml` (add a toggle control + status text, matching the existing button/status-text pairs in this layout)
- Modify: `android/app/src/main/res/values/strings.xml` (new strings)

**Interfaces:**
- Consumes: `WakeWordConfigRepository.isEnabled/setEnabled` (Task 5), `PresenceService.activeWakeWordManager` (Task 9), `PermissionsHelper.hasRecordAudioPermission` (existing, used by `VoiceActivity`).

No unit test — `SettingsActivity` itself has no existing test file (matches `PresenceService`'s precedent: real-device/manual-verification for Activity click-wiring, not JVM unit tests, is this project's existing convention for this specific screen).

- [ ] **Step 1: Add the layout control**

In `activity_settings.xml`, add a `Switch`/`SwitchCompat` (`wakeWordToggle`) and a status `TextView` (`wakeWordStatusText`), following the exact styling/margin conventions of the existing button+status-text pairs in this file (e.g. `requestBatteryExemptionButton`/`batteryStatusText`) — read the file first and match its patterns exactly rather than inventing new styling.

- [ ] **Step 2: Add string resources**

In `strings.xml`, add:

```xml
    <string name="wakeword_toggle_label">Hey Jarvis (wake word)</string>
    <string name="wakeword_status_enabled">Listening for \"Hey Jarvis\"</string>
    <string name="wakeword_status_disabled">Off</string>
    <string name="wakeword_status_needs_permission">Microphone permission required</string>
```

- [ ] **Step 3: Wire the toggle**

In `SettingsActivity.kt`, add a permission launcher alongside the existing `notificationPermissionLauncher`:

```kotlin
    private val recordAudioPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                app.wakeWordConfigRepository.setEnabled(true)
                PresenceService.activeWakeWordManager?.start()
            } else {
                binding.wakeWordToggle.isChecked = false
            }
            refreshStatus()
        }
```

In `onCreate()`, alongside the other `binding.xButton.setOnClickListener` wiring:

```kotlin
        binding.wakeWordToggle.setOnCheckedChangeListener { _, isChecked ->
            if (isChecked) {
                if (PermissionsHelper.hasRecordAudioPermission(this)) {
                    app.wakeWordConfigRepository.setEnabled(true)
                    PresenceService.activeWakeWordManager?.start()
                } else {
                    recordAudioPermissionLauncher.launch(android.Manifest.permission.RECORD_AUDIO)
                }
            } else {
                app.wakeWordConfigRepository.setEnabled(false)
                PresenceService.activeWakeWordManager?.stop()
            }
            refreshStatus()
        }
```

In `refreshStatus()`, add:

```kotlin
        val wakeWordEnabled = app.wakeWordConfigRepository.isEnabled()
        binding.wakeWordToggle.isChecked = wakeWordEnabled
        binding.wakeWordStatusText.text = getString(
            when {
                !PermissionsHelper.hasRecordAudioPermission(this) -> R.string.wakeword_status_needs_permission
                wakeWordEnabled -> R.string.wakeword_status_enabled
                else -> R.string.wakeword_status_disabled
            },
        )
```

- [ ] **Step 4: Build**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/jarvis/companion/ui/SettingsActivity.kt \
        android/app/src/main/res/layout/activity_settings.xml \
        android/app/src/main/res/values/strings.xml
git commit -m "Add wake-word enable/disable toggle to Settings (ADR-017)"
```

---

### Task 13: Document `client_request_id` in the protocol reference

**Files:**
- Modify: `docs/protocols/websocket-protocol-v1.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Read the existing `voice_session_open`/`voice_session_opened`/`voice_session_error` sections of this document** to match its exact existing format (field tables, example payloads — whichever style the file already uses).

- [ ] **Step 2: Add the new field**

Extend the `voice_session_open` (client→server), `voice_session_opened` (server→client), and `voice_session_error` (server→client) message specifications with the new optional `client_request_id` field, using this document's existing per-field format. Include the general principle from ADR-017 as a short note near the first of these three sections: this is a generic correlation primitive, not specific to any one feature, and any future asynchronous client-initiated operation should reuse it rather than introduce a feature-specific identifier. Cross-reference ADR-017 by name.

- [ ] **Step 3: Commit**

```bash
git add docs/protocols/websocket-protocol-v1.md
git commit -m "Document client_request_id correlation primitive (ADR-017)"
```

---

### Task 14: Real-device validation (Phase 6)

**Owner:** Chief Engineer directly — this project's standing practice never trusts a Builder's or Reviewer's self-report for real-device claims (see `SESSION.md`'s repeated "independently re-verified... not the Builder's own transcript" pattern).

**Files:** none (validation only — any bug found here becomes a new task, not silently patched in place).

Real-device scenarios to run on the Samsung Galaxy S20 FE, through the **production build** (not the spike), enabling wake-word via the new Settings toggle:

- [ ] **A — Foreground, screen on**: enable wake-word, say "Hey Jarvis," confirm `VoiceActivity` launches and auto-starts listening (no manual mic tap needed), confirm the Diagnostics screen's new `wakeword_*` fields populate.
- [ ] **B — Screen off / locked, charging**: repeat A with the screen off. This is the scenario the D5 spike's TD-022 explicitly left untested — first real evidence either way.
- [ ] **C — Screen off / locked, not charging, default battery mode**: repeat B without the Unrestricted battery exemption. If this fails, that is itself a real, expected-possible finding (matches the M9B.0 Samsung-battery precedent) — record it, do not treat it as a blocker to fix silently.
- [ ] **D — Concurrent PWA-initiated `VoiceSession`**: open a voice session from the PWA first, then say "Hey Jarvis" on the Android device. Confirm `WakeWordManager` is already paused (per the `voiceSessionRepository.current` subscription) and does not fire, or if it does fire, confirm the resulting open attempt is correctly rejected (`VoiceSessionOpenOutcome.Failed`) and no `VoiceActivity` launches. **This is the scenario that directly validates TD-002's lease** — the ADR called this out explicitly.
- [ ] **E — Pending `AttentionRequest` call-style card showing**: confirm wake-word detection and the existing attention-card UI do not interfere with each other.
- [ ] **F — Kill the app mid-turn after a wake-word-triggered open**: force-stop the app immediately after `VoiceActivity` launches. Confirm (via server-side logs) the abrupt-disconnect `finally` block (ADR-016) still closes the `VoiceSession` — this path is unchanged by this milestone, but wake-word is a new *trigger* for it, worth one real confirmation.
- [ ] **G — Repeat D5's Test A/B/C protocol** (controlled recall, false-positive, resource) through the production path, to confirm the production wiring didn't regress anything Milestone 9B.5 already proved.

For each scenario: record exact observed behavior, cross-check against logs/telemetry (not just what the screen showed), and update `docs/TECHNICAL_DEBT.md`'s TD-022 afterward — close it only for the sub-items real evidence actually closes; leave the rest open and explicitly disclosed, exactly as this project's testing discipline requires everywhere else.

- [ ] **Final step: Update `SESSION.md`**

Add a "Milestone 9B.6 — Production Wake-Word Foundation" section following the exact structure of the Milestone 9B.5 section already in the file (Goal, what was built, real bugs found, real-device validation results, test counts, closing status) — do not claim this closes TD-022 unless Task 14's real evidence actually supports that for each specific sub-item.
