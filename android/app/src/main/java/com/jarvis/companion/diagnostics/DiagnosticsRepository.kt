package com.jarvis.companion.diagnostics

import com.jarvis.companion.pairing.PairingRepository
import com.jarvis.companion.service.PresenceService
import com.jarvis.companion.telemetry.TelemetryRecorder

/**
 * Read-only view for the Diagnostics screen: the telemetry log tail (every
 * transition is already recorded there — WS_CONNECTING/WS_RECONNECT_SCHEDULED/
 * WS_STALE_CALLBACK_IGNORED/...) plus a live [DiagnosticsSnapshot] assembled
 * from PresenceService's in-process-only static state (PresenceService.activeClient/
 * serviceCreatedAtMs — see that class's companion object doc comment).
 * Milestone 9B.2: a full bindService()/ServiceConnection layer was
 * considered and rejected as more machinery than reading a same-process
 * volatile field needs — the service and this repository always run in
 * the same process (this app has no other), so a Binder would only add
 * indirection, not safety.
 */
class DiagnosticsRepository(
    private val telemetry: TelemetryRecorder,
    private val pairingRepository: PairingRepository,
) {
    fun tail(lineCount: Int = 50): List<String> = telemetry.tailLines(lineCount)

    fun snapshot(): DiagnosticsSnapshot {
        val client = PresenceService.activeClient
        val pairingConfig = pairingRepository.get()
        val serviceCreatedAtMs = PresenceService.serviceCreatedAtMs
        return DiagnosticsSnapshot(
            serviceRunning = client != null,
            serviceUptimeMs = serviceCreatedAtMs?.let { System.currentTimeMillis() - it },
            connectionState = client?.state,
            currentGeneration = client?.currentGeneration(),
            reconnectCount = client?.reconnectCount,
            lastDisconnectReason = client?.lastDisconnectReason,
            lastHeartbeatAgoMs = client?.lastHeartbeatAgoMs(),
            connectedUptimeMs = client?.connectedSinceMs?.let { System.currentTimeMillis() - it },
            paired = pairingConfig != null,
            pairedHost = pairingConfig?.let { "${it.host}:${it.port}" },
            pinnedFingerprint = pairingConfig?.pinnedFingerprint,
        )
    }
}
