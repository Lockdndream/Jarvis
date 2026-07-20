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

/**
 * Voice-feature diagnostics snapshot (Milestone 9B.4). Every field is
 * nullable/absent-safe — the voice feature may not be active at all
 * when diagnostics is viewed, and this must render sensibly rather than
 * crash or show misleading zeros. None of these fields are secrets.
 */
data class VoiceDiagnostics(
    val audioFocusState: String?,
    val audioRoute: String?,
    val ttsReady: Boolean?,
    val ttsSpeaking: Boolean?,
    val voiceSessionState: String?,
    val voiceSessionId: String?,
    val playbackQueueDepth: Int?,
    val speechInputState: String?,
    val lastVoiceEventAgoMs: Long?,
    // Milestone 9B.10: why the most recently closed session ended —
    // "client_requested", "idle_timeout", or absent (either no session
    // has ever closed yet, or it ended via "disconnect", a reason the
    // client structurally can never observe itself — see
    // VoiceSessionRepository.applyClosed()'s doc comment).
    val lastTerminationReason: String?,
) {
    fun formatted(): String {
        fun ms(value: Long?): String = if (value == null) "n/a" else "${value / 1000}s"
        return buildString {
            appendLine("audio_focus_state=${audioFocusState ?: "n/a"}")
            appendLine("audio_route=${audioRoute ?: "n/a"}")
            appendLine("tts_ready=${ttsReady ?: "n/a"}")
            appendLine("tts_speaking=${ttsSpeaking ?: "n/a"}")
            appendLine("voice_session_state=${voiceSessionState ?: "n/a"}")
            appendLine("voice_session_id=${voiceSessionId ?: "n/a"}")
            appendLine("playback_queue_depth=${playbackQueueDepth ?: "n/a"}")
            appendLine("speech_input_state=${speechInputState ?: "n/a"}")
            appendLine("last_termination_reason=${lastTerminationReason ?: "n/a"}")
            append("last_voice_event_ago=${ms(lastVoiceEventAgoMs)}")
        }
    }
}

/**
 * Wake-word subsystem diagnostics (Milestone 9B.9, ADR-017 Section C,
 * Item 6). Every failure/timeout/recovery path in
 * PresenceService.handleWakeWordDetection() must be observable here, not
 * just in the scrolling telemetry tail — [lastHandoffOutcome] is the
 * single field that answers "what happened to the last detection"
 * without requiring a log search. All fields nullable/absent-safe, same
 * rationale as [VoiceDiagnostics] — wake word may be disabled or never
 * yet started.
 */
data class WakeWordDiagnostics(
    val wakeWordSessionId: String?,
    val managerState: String?,
    val engineLoaded: Boolean?,
    val audioRecordState: String?,
    val detectionCount: Int?,
    val lastDetectionAtMs: Long?,
    val framesProcessed: Long?,
    val avgLatencyMs: Double?,
    val maxLatencyMs: Double?,
    val inferenceErrorCount: Int?,
    val lastDetectionId: String?,
    val lastClientRequestId: String?,
    val lastVoiceSessionId: String?,
    val lastHandoffOutcome: String?,
    val lastHandoffAtMs: Long?,
) {
    fun formatted(): String {
        fun ms(value: Long?): String = if (value == null) "n/a" else "${value / 1000}s"
        return buildString {
            appendLine("wakeword_session_id=${wakeWordSessionId ?: "n/a"}")
            appendLine("manager_state=${managerState ?: "n/a"}")
            appendLine("engine_loaded=${engineLoaded ?: "n/a"}")
            appendLine("audio_record_state=${audioRecordState ?: "n/a"}")
            appendLine("detection_count=${detectionCount ?: "n/a"}")
            appendLine("last_detection_ago=${ms(lastDetectionAtMs)}")
            appendLine("frames_processed=${framesProcessed ?: "n/a"}")
            appendLine("avg_latency_ms=${avgLatencyMs ?: "n/a"}")
            appendLine("max_latency_ms=${maxLatencyMs ?: "n/a"}")
            appendLine("inference_error_count=${inferenceErrorCount ?: "n/a"}")
            appendLine("last_detection_id=${lastDetectionId ?: "n/a"}")
            appendLine("last_client_request_id=${lastClientRequestId ?: "n/a"}")
            appendLine("last_voice_session_id=${lastVoiceSessionId ?: "n/a"}")
            appendLine("last_handoff_outcome=${lastHandoffOutcome ?: "n/a"}")
            append("last_handoff_ago=${ms(lastHandoffAtMs)}")
        }
    }
}
