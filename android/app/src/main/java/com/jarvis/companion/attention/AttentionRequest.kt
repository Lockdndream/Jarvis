package com.jarvis.companion.attention

data class AttentionRequest(
    val attentionRequestId: String,
    val attentionType: String,
    val status: String,
    val summary: String?,
    val taskId: String?,
    val conversationId: String?,
    val urgency: String?,
    val deferredUntil: String?,
)

object AttentionStatus {
    const val PENDING = "pending"
    const val CONTACTING = "contacting"
    const val DEFERRED = "deferred"
    const val RESOLVING = "resolving"
    const val RESOLVED = "resolved"
    const val CANCELLED = "cancelled"
    const val EXPIRED = "expired"

    val TERMINAL: Set<String> = setOf(RESOLVED, CANCELLED, EXPIRED)

    fun isTerminal(status: String): Boolean = status in TERMINAL
}
