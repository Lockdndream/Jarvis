package com.jarvis.companion.voice

import org.json.JSONObject

object VoiceSessionParser {
    private val EVENT_TYPES = setOf(
        "voice_session_opened", "voice_session_response",
        "voice_session_error", "voice_session_closed",
        "voice_session_invitation",
    )

    fun isVoiceSessionEventType(type: String): Boolean = type in EVENT_TYPES

    // Milestone 9B.10 release-candidate real-device finding:
    // `optString(key, null)` alone does not protect against an *explicit*
    // JSON null value (only a missing key) -- org.json's internal
    // JSONObject.NULL sentinel is a real object, not Kotlin/Java null, so
    // optString() stringifies it as the literal string "null" instead of
    // returning the fallback. Reproduced on a real device: the server
    // always sends an explicit "greeting": null for a wake-word-triggered
    // VoiceSession (no bound attention row -- see app/main.py's
    // voice_session_opened response), and the client spoke and displayed
    // the literal word "null" every single time. isNull() is org.json's
    // documented, correct way to test for "missing or explicit null"
    // before ever calling optString().
    private fun JSONObject.optNullableString(name: String): String? =
        if (isNull(name)) null else optString(name).ifEmpty { null }

    fun parseOpened(json: String): VoiceSession? {
        return try {
            val root = JSONObject(json)
            val type = root.optString("type", "")
            if (type != "voice_session_opened") return null
            val id = root.optString("voice_session_id")
            if (id.isEmpty()) return null
            val state = root.optString("state")
            if (state.isEmpty()) return null
            VoiceSession(
                voiceSessionId = id,
                state = state,
                conversationId = root.optNullableString("conversation_id"),
                attentionRequestId = root.optNullableString("attention_request_id"),
                greeting = root.optNullableString("greeting"),
                clientRequestId = root.optNullableString("client_request_id"),
            )
        } catch (_: Exception) {
            null
        }
    }

    fun parseResponse(json: String): VoiceSessionResponse? {
        return try {
            val root = JSONObject(json)
            val type = root.optString("type", "")
            if (type != "voice_session_response") return null
            val id = root.optString("voice_session_id")
            if (id.isEmpty()) return null
            VoiceSessionResponse(
                voiceSessionId = id,
                response = root.optString("response", ""),
                conversationId = root.optNullableString("conversation_id"),
                attentionRequestId = root.optNullableString("attention_request_id"),
                voiceSessionState = root.optNullableString("voice_session_state"),
            )
        } catch (_: Exception) {
            null
        }
    }

    fun parseError(json: String): VoiceSessionError? {
        return try {
            val root = JSONObject(json)
            val type = root.optString("type", "")
            if (type != "voice_session_error") return null
            val error = root.optString("error")
            if (error.isEmpty()) return null
            VoiceSessionError(
                voiceSessionId = root.optNullableString("voice_session_id"),
                error = error,
                clientRequestId = root.optNullableString("client_request_id"),
            )
        } catch (_: Exception) {
            null
        }
    }

    fun parseClosed(json: String): VoiceSessionClosed? {
        return try {
            val root = JSONObject(json)
            val type = root.optString("type", "")
            if (type != "voice_session_closed") return null
            VoiceSessionClosed(
                voiceSessionId = root.optNullableString("voice_session_id"),
                reason = root.optNullableString("reason"),
            )
        } catch (_: Exception) {
            null
        }
    }

    fun parseInvitation(json: String): VoiceSessionInvitation? {
        return try {
            val root = JSONObject(json)
            val type = root.optString("type", "")
            if (type != "voice_session_invitation") return null
            val attId = root.optString("attention_request_id")
            if (attId.isEmpty()) return null
            val attType = root.optString("attention_type")
            if (attType.isEmpty()) return null
            VoiceSessionInvitation(
                attentionRequestId = attId,
                summary = root.optNullableString("summary"),
                taskId = root.optNullableString("task_id"),
                attentionType = attType,
            )
        } catch (_: Exception) {
            null
        }
    }
}