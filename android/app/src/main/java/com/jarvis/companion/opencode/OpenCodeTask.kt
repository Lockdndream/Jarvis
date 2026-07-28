package com.jarvis.companion.opencode

object OpenCodeTaskStatus {
    const val RUNNING = "running"
    const val COMPLETED = "completed"
    const val FAILED = "failed"
}

data class OpenCodeTask(
    val taskId: String,
    val status: String,
    val instruction: String?,
)
