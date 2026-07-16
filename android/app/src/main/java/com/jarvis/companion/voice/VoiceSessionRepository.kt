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
    // mid-timeout-wait, ADR-017) doesn't cause emit() to suspend or drop
    // under normal single-digit concurrent-open scenarios.
    private val _openOutcomes = MutableSharedFlow<VoiceSessionOpenOutcome>(extraBufferCapacity = 8)
    val openOutcomes: SharedFlow<VoiceSessionOpenOutcome> = _openOutcomes.asSharedFlow()

    fun applyOpened(session: VoiceSession) {
        _current.value = session
        // A new session starts with no turn response yet — without this, a
        // leftover lastResponse from a just-closed previous session (this
        // field is never cleared by applyClosed()/applyError(), only by
        // clear()) would appear to belong to the brand-new session, and a
        // caller that speaks on any lastResponse change could speak stale
        // content immediately alongside the new session's greeting.
        _lastResponse.value = null
        if (session.clientRequestId != null) {
            _openOutcomes.tryEmit(VoiceSessionOpenOutcome.Opened(session.clientRequestId, session))
        }
    }

    /** ADR-017: separate from applyError(voiceSessionId) — this is
     * specifically for the immediate voice_session_error reply to a
     * voice_session_open request, correlating on clientRequestId rather
     * than voiceSessionId (which doesn't exist yet for a rejected open). */
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
        // Same stale-session-id discipline as applyResponse(): a closed
        // frame for a session that has already been superseded by a newer
        // one (e.g. a delayed close round-trip arriving after a fresh open)
        // must never wipe the newer session. A null id is treated as
        // unconditional — the server only omits it when there was nothing
        // to correlate to in the first place (see VoiceSessionParser).
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
        // Intentionally a no-op — matches app.js line 285-291:
        // attention_* broadcasts already drive the call-style card.
    }

    fun clear() {
        _current.value = null
        _lastResponse.value = null
    }
}