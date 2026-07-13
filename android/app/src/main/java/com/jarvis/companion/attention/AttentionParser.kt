package com.jarvis.companion.attention

import org.json.JSONArray
import org.json.JSONObject

object AttentionParser {
    private val EVENT_TYPES = setOf(
        "attention_created", "attention_contacting", "attention_pending",
        "attention_deferred", "attention_resolving", "attention_resolved",
        "attention_cancelled", "attention_expired",
    )

    fun isAttentionEventType(type: String): Boolean = type in EVENT_TYPES

    fun parsePendingAttention(json: String): List<AttentionRequest> {
        return try {
            val root = JSONObject(json)
            if (root.optString("type") != "pending_attention") return emptyList()
            val rawArray = root.optJSONArray("attention_requests") ?: return emptyList()
            val result = mutableListOf<AttentionRequest>()
            for (i in 0 until rawArray.length()) {
                val item = try {
                    rawArray.optJSONObject(i) ?: continue
                } catch (_: Exception) {
                    continue
                }
                val parsed = try {
                    parseItem(item)
                } catch (_: Exception) {
                    continue
                }
                if (parsed != null) {
                    result.add(parsed)
                }
            }
            result
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun parseAttentionEvent(json: String): AttentionRequest? {
        return try {
            val root = JSONObject(json)
            val type = root.optString("type", "")
            if (type !in EVENT_TYPES) return null
            val id = root.optString("attention_request_id")
            if (id.isEmpty()) return null
            val attType = root.optString("attention_type")
            if (attType.isEmpty()) return null
            val status = root.optString("status")
            if (status.isEmpty()) return null

            AttentionRequest(
                attentionRequestId = id,
                attentionType = attType,
                status = status,
                summary = root.optString("summary", null)?.ifEmpty { null },
                taskId = root.optString("task_id", null)?.ifEmpty { null },
                conversationId = null,
                urgency = null,
                deferredUntil = root.optString("deferred_until", null)?.ifEmpty { null },
            )
        } catch (_: Exception) {
            null
        }
    }

    private fun parseItem(obj: JSONObject): AttentionRequest? {
        val id = obj.optString("attention_request_id")
        if (id.isEmpty()) return null
        val attType = obj.optString("attention_type")
        if (attType.isEmpty()) return null
        val status = obj.optString("status")
        if (status.isEmpty()) return null

        return AttentionRequest(
            attentionRequestId = id,
            attentionType = attType,
            status = status,
            summary = obj.optString("summary", null)?.ifEmpty { null },
            taskId = obj.optString("task_id", null)?.ifEmpty { null },
            conversationId = obj.optString("conversation_id", null)?.ifEmpty { null },
            urgency = obj.optString("urgency", null)?.ifEmpty { null },
            deferredUntil = obj.optString("deferred_until", null)?.ifEmpty { null },
        )
    }
}
