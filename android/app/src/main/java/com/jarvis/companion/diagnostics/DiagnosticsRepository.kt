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

/**
 * Pure-function builder for [VoiceDiagnostics]. Takes already-extracted
 * primitive values so this file has zero dependency on any voice/audio
 * package's types. The [lastVoiceEventAtMs] parameter is the absolute
 * epoch millis of the most recent voice event; this function converts it
 * to a relative "how long ago" value (matching the existing
 * [DiagnosticsSnapshot] pattern where relative times are computed from
 * absolute timestamps).
 *
 * Integration step: call this function from somewhere in the Activity /
 * Repository once the live [AudioFocusManager], [PlaybackManager] and
 * [VoiceSessionRepository] instances have a defined owner.
 */
fun buildVoiceDiagnostics(
    audioFocusState: String?,
    audioRoute: String?,
    ttsReady: Boolean?,
    ttsSpeaking: Boolean?,
    voiceSessionState: String?,
    voiceSessionId: String?,
    playbackQueueDepth: Int?,
    speechInputState: String?,
    lastVoiceEventAtMs: Long?,
): VoiceDiagnostics {
    val lastVoiceEventAgoMs = lastVoiceEventAtMs?.let { System.currentTimeMillis() - it }
    return VoiceDiagnostics(
        audioFocusState = audioFocusState,
        audioRoute = audioRoute,
        ttsReady = ttsReady,
        ttsSpeaking = ttsSpeaking,
        voiceSessionState = voiceSessionState,
        voiceSessionId = voiceSessionId,
        playbackQueueDepth = playbackQueueDepth,
        speechInputState = speechInputState,
        lastVoiceEventAgoMs = lastVoiceEventAgoMs,
    )
}

/**
 * Milestone 9B.9 (ADR-017 Section C, Item 6): reads
 * [PresenceService.activeWakeWordManager]'s live StateFlows plus the
 * handoff-outcome fields PresenceService tracks on itself, same
 * in-process-only rationale as [DiagnosticsRepository.snapshot]. Returns
 * an all-null [WakeWordDiagnostics] when no manager is active (service
 * not running) rather than crashing.
 */
fun buildWakeWordDiagnostics(): WakeWordDiagnostics {
    val manager = PresenceService.activeWakeWordManager
    return WakeWordDiagnostics(
        wakeWordSessionId = PresenceService.wakeWordSessionId,
        managerState = manager?.state?.value?.name,
        engineLoaded = manager?.engineLoaded?.value,
        audioRecordState = manager?.audioRecordState?.value?.name,
        detectionCount = manager?.detectionCount?.value,
        lastDetectionAtMs = manager?.lastDetectionAtMs?.value?.let { System.currentTimeMillis() - it },
        framesProcessed = manager?.framesProcessed?.value,
        avgLatencyMs = manager?.avgLatencyMs?.value,
        maxLatencyMs = manager?.maxLatencyMs?.value,
        inferenceErrorCount = manager?.inferenceErrorCount?.value,
        lastDetectionId = PresenceService.lastDetectionId,
        lastClientRequestId = PresenceService.lastClientRequestId,
        lastVoiceSessionId = PresenceService.lastVoiceSessionId,
        lastHandoffOutcome = PresenceService.lastHandoffOutcome,
        lastHandoffAtMs = PresenceService.lastHandoffAtMs?.let { System.currentTimeMillis() - it },
    )
}