package com.jarvis.companion.voice

import org.json.JSONObject

object VoiceSessionParser {
    private val EVENT_TYPES = setOf(
        "voice_session_opened", "voice_session_response",
        "voice_session_error", "voice_session_closed",
        "voice_session_invitation",
    )

    fun isVoiceSessionEventType(type: String): Boolean = type in EVENT_TYPES

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
                conversationId = root.optString("conversation_id", null)?.ifEmpty { null },
                attentionRequestId = root.optString("attention_request_id", null)?.ifEmpty { null },
                greeting = root.optString("greeting", null)?.ifEmpty { null },
                clientRequestId = root.optString("client_request_id", null)?.ifEmpty { null },
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
                conversationId = root.optString("conversation_id", null)?.ifEmpty { null },
                attentionRequestId = root.optString("attention_request_id", null)?.ifEmpty { null },
                voiceSessionState = root.optString("voice_session_state", null)?.ifEmpty { null },
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
                voiceSessionId = root.optString("voice_session_id", null)?.ifEmpty { null },
                error = error,
                clientRequestId = root.optString("client_request_id", null)?.ifEmpty { null },
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
                voiceSessionId = root.optString("voice_session_id", null)?.ifEmpty { null },
                reason = root.optString("reason", null)?.ifEmpty { null },
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
                summary = root.optString("summary", null)?.ifEmpty { null },
                taskId = root.optString("task_id", null)?.ifEmpty { null },
                attentionType = attType,
            )
        } catch (_: Exception) {
            null
        }
    }
}