package com.jarvis.companion.diagnostics

import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.network.DisconnectReason

/**
 * Everything the Diagnostics screen (an engineering tool, not a user
 * feature — Milestone 9B.2) shows in one place. Deliberately excludes the
 * API token and any other secret — the pinned certificate fingerprint is
 * safe to show (it's the same value the user already saw and confirmed
 * during pairing, not a credential; ADR-011).
 */
data class DiagnosticsSnapshot(
    val serviceRunning: Boolean,
    val serviceUptimeMs: Long?,
    val connectionState: ConnectionState?,
    val currentGeneration: Int?,
    val reconnectCount: Int?,
    val lastDisconnectReason: DisconnectReason?,
    val lastHeartbeatAgoMs: Long?,
    val connectedUptimeMs: Long?,
    val paired: Boolean,
    val pairedHost: String?,
    val pinnedFingerprint: String?,
) {
    fun formatted(): String {
        fun ms(value: Long?): String = if (value == null) "n/a" else "${value / 1000}s"
        return buildString {
            appendLine("service_running=$serviceRunning")
            appendLine("service_uptime=${ms(serviceUptimeMs)}")
            appendLine("paired=$paired host=${pairedHost ?: "n/a"}")
            appendLine("pinned_fingerprint=${pinnedFingerprint ?: "n/a"}")
            appendLine("connection_state=${connectionState ?: "n/a"}")
            appendLine("generation=${currentGeneration ?: "n/a"}")
            appendLine("reconnect_count=${reconnectCount ?: "n/a"}")
            appendLine("last_disconnect_reason=${lastDisconnectReason ?: "n/a"}")
            appendLine("connected_uptime=${ms(connectedUptimeMs)}")
            append("last_heartbeat_ago=${ms(lastHeartbeatAgoMs)}")
        }
    }
}
