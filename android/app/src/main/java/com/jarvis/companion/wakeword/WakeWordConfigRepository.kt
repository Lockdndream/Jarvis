package com.jarvis.companion.wakeword

import com.jarvis.companion.core.SecureConfigStore

private const val KEY_ENABLED = "wakeword_enabled"
private const val KEY_THRESHOLD = "wakeword_confidence_threshold"
private const val KEY_DIAGNOSTIC_MODE = "wakeword_diagnostic_mode"
private const val KEY_HANDOFF_CONFIRM_TIMEOUT_MS = "wakeword_handoff_confirm_timeout_ms"
private const val MODEL_VERSION = "hey_jarvis-v1"
private const val DEFAULT_HANDOFF_CONFIRM_TIMEOUT_MS = 5000L

class WakeWordConfigRepository(private val store: SecureConfigStore) {
    fun isEnabled(): Boolean {
        return store.getString(KEY_ENABLED)?.toBooleanStrictOrNull() ?: false
    }

    fun setEnabled(value: Boolean) {
        store.putString(KEY_ENABLED, value.toString())
    }

    fun confidenceThreshold(): Float {
        return store.getString(KEY_THRESHOLD)?.toFloatOrNull() ?: 0.97f
    }

    fun setConfidenceThreshold(value: Float) {
        // Float.parseFloat("NaN") successfully parses back to NaN (documented
        // JVM behavior) rather than failing, so toFloatOrNull()'s Elvis
        // fallback to the 0.97f default would never trigger for a stored
        // NaN — closing that off at the write boundary instead.
        require(value.isFinite()) { "confidenceThreshold must be finite, was $value" }
        store.putString(KEY_THRESHOLD, value.toString())
    }

    fun modelVersion(): String = MODEL_VERSION

    fun diagnosticModeEnabled(): Boolean {
        return store.getString(KEY_DIAGNOSTIC_MODE)?.toBooleanStrictOrNull() ?: false
    }

    fun setDiagnosticModeEnabled(value: Boolean) {
        store.putString(KEY_DIAGNOSTIC_MODE, value.toString())
    }

    // Milestone 9B.9 (ADR-017 Section C, Item 7): how long PresenceService
    // waits for the server's voice_session_opened/voice_session_error
    // reply to a wake-word-initiated open before giving up and resuming
    // wake-word listening. Was a hardcoded PresenceService constant
    // (Milestone 9B.9's Task 10); moved here so it's tunable without a
    // rebuild, same as confidenceThreshold — default is unchanged (5000L),
    // so this is not a runtime behavior change on its own.
    fun handoffConfirmTimeoutMs(): Long {
        return store.getString(KEY_HANDOFF_CONFIRM_TIMEOUT_MS)?.toLongOrNull()
            ?: DEFAULT_HANDOFF_CONFIRM_TIMEOUT_MS
    }

    fun setHandoffConfirmTimeoutMs(value: Long) {
        require(value > 0) { "handoffConfirmTimeoutMs must be positive, was $value" }
        store.putString(KEY_HANDOFF_CONFIRM_TIMEOUT_MS, value.toString())
    }
}
