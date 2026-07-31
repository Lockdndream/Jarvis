package com.jarvis.companion.conversation

import org.json.JSONObject
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.UUID

/** Parses the Interaction Layer v1 conversation-* wire frames into
 * [ConversationMessage]s. Same one-object-per-frame-family,
 * never-throws discipline as [com.jarvis.companion.voice.VoiceSessionParser].
 */
object ConversationParser {
    private val EVENT_TYPES = setOf(
        "conversation_turn", "thinking_update", "permission_response_ack",
    )

    fun isConversationEventType(type: String): Boolean = type in EVENT_TYPES

    // Reproduced in VoiceSessionParser.kt: org.json's optString() with a
    // fallback still returns the literal string "null" when the key is
    // present with an explicit JSON null value, so we guard with isNull().
    private fun JSONObject.optNullableString(name: String): String? =
        if (isNull(name)) null else optString(name).ifEmpty { null }

    /** Parses epoch millis from an ISO-8601 timestamp. Falls back to the
     * current time if the string is missing, empty, or unparseable, so a
     * malformed timestamp never drops an otherwise-valid message. */
    private fun parseTimestamp(iso: String?): Long {
        if (iso.isNullOrEmpty()) return System.currentTimeMillis()
        return try {
            Instant.parse(iso).toEpochMilli()
        } catch (_: Exception) {
            try {
                LocalDateTime.parse(iso, DateTimeFormatter.ISO_LOCAL_DATE_TIME)
                    .atZone(ZoneId.systemDefault())
                    .toInstant()
                    .toEpochMilli()
            } catch (_: Exception) {
                System.currentTimeMillis()
            }
        }
    }

    fun parseConversationTurn(json: String): ConversationMessage? {
        return try {
            val root = JSONObject(json)
            if (root.optString("type", "") != "conversation_turn") return null
            val content = root.optNullableString("content") ?: return null
            val voiceSessionId = root.optString("voice_session_id")
            if (voiceSessionId.isEmpty()) return null

            val metadata = mutableMapOf<String, String>()
            metadata["voice_session_id"] = voiceSessionId
            root.optNullableString("conversation_id")?.let { metadata["conversation_id"] = it }

            ConversationMessage(
                id = UUID.randomUUID().toString(),
                type = ConversationMessage.Type.USER_MESSAGE,
                content = content,
                timestamp = parseTimestamp(root.optNullableString("timestamp")),
                metadata = metadata.takeIf { it.isNotEmpty() },
            )
        } catch (_: Exception) {
            null
        }
    }

    fun parseThinkingUpdate(json: String): ConversationMessage? {
        return try {
            val root = JSONObject(json)
            if (root.optString("type", "") != "thinking_update") return null
            val action = root.optString("action")
            if (action.isEmpty()) return null
            val statusString = root.optString("status")
            if (statusString.isEmpty()) return null
            val status = try {
                ConversationMessage.Status.valueOf(statusString.uppercase())
            } catch (_: IllegalArgumentException) {
                return null
            }
            val summary = root.optNullableString("summary") ?: return null

            val metadata = mutableMapOf<String, String>()
            root.optNullableString("conversation_id")?.let { metadata["conversation_id"] = it }
            root.optNullableString("trace_id")?.let { metadata["trace_id"] = it }

            ConversationMessage(
                id = UUID.randomUUID().toString(),
                type = ConversationMessage.Type.THINKING,
                content = summary,
                detail = root.optNullableString("detail"),
                timestamp = parseTimestamp(root.optNullableString("timestamp")),
                status = status,
                metadata = metadata.takeIf { it.isNotEmpty() },
            )
        } catch (_: Exception) {
            null
        }
    }

    fun parsePermissionResponseAck(json: String): ConversationMessage? {
        return try {
            val root = JSONObject(json)
            if (root.optString("type", "") != "permission_response_ack") return null
            val permissionId = root.optString("attention_request_id")
            if (permissionId.isEmpty()) return null
            val response = root.optNullableString("response") ?: return null

            val metadata = mutableMapOf<String, String>()
            root.optNullableString("conversation_id")?.let { metadata["conversation_id"] = it }

            ConversationMessage(
                id = UUID.randomUUID().toString(),
                type = ConversationMessage.Type.SYSTEM_EVENT,
                content = "Permission response: $response",
                timestamp = System.currentTimeMillis(),
                permissionId = permissionId,
                metadata = metadata.takeIf { it.isNotEmpty() },
            )
        } catch (_: Exception) {
            null
        }
    }
}
