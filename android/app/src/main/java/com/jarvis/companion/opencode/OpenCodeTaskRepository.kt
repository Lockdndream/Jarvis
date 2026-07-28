package com.jarvis.companion.opencode

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Interaction Layer v1 (Goals 2/3): client-side mirror of the most recent
 * OpenCode delegated task's lifecycle. Single-operator system (ADR-022's
 * Authorization section) — only the single most recent task is tracked,
 * not a list; a full task history already exists server-side (JOPS's
 * Operation history), this is a live status mirror for the phone UI/
 * notification, not a second history store.
 */
class OpenCodeTaskRepository {
    private val _current = MutableStateFlow<OpenCodeTask?>(null)
    val current: StateFlow<OpenCodeTask?> = _current.asStateFlow()

    fun applyCreated(task: OpenCodeTask) {
        _current.value = task
    }

    /** opencode_task_completed carries no instruction text (see
     * OpenCodeTaskParser) — preserve it from the created event for the
     * same taskId so the UI can still show what the completed/failed task
     * actually was, not just its terminal status. */
    fun applyCompleted(task: OpenCodeTask) {
        _current.update { existing ->
            if (existing?.taskId == task.taskId) task.copy(instruction = existing.instruction) else task
        }
    }

    fun clear() {
        _current.value = null
    }
}
