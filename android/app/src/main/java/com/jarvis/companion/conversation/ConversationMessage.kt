package com.jarvis.companion.conversation

/**
 * One item in the in-memory conversation view. The canonical session history
 * lives on the laptop; this is a transient, phone-side rendering mirror only,
 * intentionally cleared on process death/disconnect.
 *
 * @param id locally generated UUID — no wire type carries a client-usable
 *   message id, so we mint one ourselves.
 * @param type distinguishes user text, assistant replies, thinking/tool
 *   progress, permission requests, and other system events.
 * @param content primary human-readable text for the UI.
 * @param detail secondary text (e.g. the populated thinking_update detail).
 * @param timestamp epoch millis; parsed from the wire's ISO-8601 timestamp when
 *   present, otherwise falls back to System.currentTimeMillis() so the item
 *   still sorts/renders correctly.
 * @param status thinking_update lifecycle marker (started/completed).
 * @param permissionId the attention_request_id for PERMISSION_REQUEST items.
 * @param metadata non-null string keys/values from the wire frame (e.g.
 *   voice_session_id, conversation_id, trace_id) kept out of the main content.
 */
data class ConversationMessage(
    val id: String,
    val type: Type,
    val content: String,
    val detail: String? = null,
    val timestamp: Long,
    val status: Status? = null,
    val permissionId: String? = null,
    val metadata: Map<String, String>? = null,
) {
    enum class Type { USER_MESSAGE, ASSISTANT_MESSAGE, THINKING, PERMISSION_REQUEST, SYSTEM_EVENT }
    enum class Status { STARTED, COMPLETED, FAILED }
}
