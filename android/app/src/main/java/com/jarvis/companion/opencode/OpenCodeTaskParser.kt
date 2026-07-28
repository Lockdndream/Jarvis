package com.jarvis.companion.opencode

import org.json.JSONObject

/** Interaction Layer v1 (Goals 2/3): parses the opencode_task_created/
 * opencode_task_completed broadcasts the server already sends to every
 * connection (app/integrations/opencode_supervisor.py) — previously
 * received but silently dropped by CompanionWebSocketClient. Same
 * one-object-per-frame-family, never-throws pattern as VoiceSessionParser.
 */
object OpenCodeTaskParser {
    private val EVENT_TYPES = setOf("opencode_task_created", "opencode_task_completed")

    fun isOpenCodeTaskEventType(type: String): Boolean = type in EVENT_TYPES

    private fun JSONObject.optNullableString(name: String): String? =
        if (isNull(name)) null else optString(name).ifEmpty { null }

    fun parseCreated(json: String): OpenCodeTask? {
        return try {
            val root = JSONObject(json)
            if (root.optString("type", "") != "opencode_task_created") return null
            val taskId = root.optString("task_id")
            if (taskId.isEmpty()) return null
            OpenCodeTask(
                taskId = taskId,
                status = OpenCodeTaskStatus.RUNNING,
                instruction = root.optNullableString("instruction"),
            )
        } catch (_: Exception) {
            null
        }
    }

    /** status is whatever the server sent ("completed"/"failed") — not
     * hardcoded to OpenCodeTaskStatus here, so an unrecognized future
     * status still parses (the repository/UI layer decides how to render
     * an unknown value) rather than silently dropping the whole frame. */
    fun parseCompleted(json: String): OpenCodeTask? {
        return try {
            val root = JSONObject(json)
            if (root.optString("type", "") != "opencode_task_completed") return null
            val taskId = root.optString("task_id")
            if (taskId.isEmpty()) return null
            val status = root.optString("status")
            if (status.isEmpty()) return null
            OpenCodeTask(taskId = taskId, status = status, instruction = null)
        } catch (_: Exception) {
            null
        }
    }
}
