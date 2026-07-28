package com.jarvis.companion.voice

data class VoiceSession(
    val voiceSessionId: String,
    val state: String,
    val conversationId: String?,
    val attentionRequestId: String?,
    val greeting: String?,
    val clientRequestId: String? = null,
)

data class VoiceSessionResponse(
    val voiceSessionId: String,
    val response: String,
    val conversationId: String?,
    val attentionRequestId: String?,
    val voiceSessionState: String?,
    val traceId: String? = null,
)

data class VoiceSessionError(
    val voiceSessionId: String?,
    val error: String,
    val clientRequestId: String? = null,
)

data class VoiceSessionClosed(
    val voiceSessionId: String?,
    val reason: String? = null,
)

data class VoiceSessionInvitation(
    val attentionRequestId: String,
    val summary: String?,
    val taskId: String?,
    val attentionType: String,
)

sealed class VoiceSessionOpenOutcome {
    data class Opened(val clientRequestId: String, val session: VoiceSession) : VoiceSessionOpenOutcome()
    data class Failed(val clientRequestId: String?, val error: String) : VoiceSessionOpenOutcome()
}

fun VoiceSessionOpenOutcome.matchesRequestId(requestId: String): Boolean = when (this) {
    is VoiceSessionOpenOutcome.Opened -> clientRequestId == requestId
    is VoiceSessionOpenOutcome.Failed -> clientRequestId == requestId
}

object VoiceSessionState {
    const val OPENING = "opening"
    const val LISTENING = "listening"
    const val PROCESSING = "processing"
    const val SPEAKING = "speaking"
    const val WAITING = "waiting"
    const val DEFERRED = "deferred"
    // Interaction Layer v1 (Goal 5): awaiting a yes/no reply to a
    // technical-command read-back confirmation — see
    // app/voice_session_manager.py's STATE_CONFIRMING.
    const val CONFIRMING = "confirming"
    const val CLOSING = "closing"
    const val CLOSED = "closed"
    const val FAILED = "failed"
}