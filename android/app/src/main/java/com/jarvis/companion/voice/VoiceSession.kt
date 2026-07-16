package com.jarvis.companion.voice

data class VoiceSession(
    val voiceSessionId: String,
    val state: String,
    val conversationId: String?,
    val attentionRequestId: String?,
    val greeting: String?,
)

data class VoiceSessionResponse(
    val voiceSessionId: String,
    val response: String,
    val conversationId: String?,
    val attentionRequestId: String?,
    val voiceSessionState: String?,
)

data class VoiceSessionError(
    val voiceSessionId: String?,
    val error: String,
)

data class VoiceSessionClosed(
    val voiceSessionId: String?,
)

data class VoiceSessionInvitation(
    val attentionRequestId: String,
    val summary: String?,
    val taskId: String?,
    val attentionType: String,
)

object VoiceSessionState {
    const val OPENING = "opening"
    const val LISTENING = "listening"
    const val PROCESSING = "processing"
    const val SPEAKING = "speaking"
    const val WAITING = "waiting"
    const val DEFERRED = "deferred"
    const val CLOSING = "closing"
    const val CLOSED = "closed"
    const val FAILED = "failed"
}