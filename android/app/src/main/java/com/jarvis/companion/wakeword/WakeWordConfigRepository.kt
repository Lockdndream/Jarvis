package com.jarvis.companion.wakeword

import com.jarvis.companion.core.SecureConfigStore

private const val KEY_ENABLED = "wakeword_enabled"
private const val KEY_THRESHOLD = "wakeword_confidence_threshold"
private const val KEY_DIAGNOSTIC_MODE = "wakeword_diagnostic_mode"
private const val MODEL_VERSION = "hey_jarvis-v1"

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
}
