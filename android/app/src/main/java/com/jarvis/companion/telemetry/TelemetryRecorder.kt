package com.jarvis.companion.telemetry

import android.content.Context
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * Structured, timestamped event log. Every event is written to both logcat
 * (`adb logcat -s JarvisCompanion`) and a local file under app-internal
 * storage, so it survives a screen-off/locked period without an attached
 * debugger — pull it afterward with `adb pull`, or read it from the
 * in-app Diagnostics screen.
 *
 * Never fabricates an event that didn't actually happen — callers only
 * invoke [record] for lifecycle points that genuinely occurred (ADR-010).
 *
 * Ported from spikes/android-presence/TelemetryRecorder.kt (validated
 * across the Milestone 9B.0 survival tests); production adds a size cap so
 * a long-running install doesn't grow the log file unbounded.
 */
class TelemetryRecorder(context: Context) {

    private val logFile = File(context.filesDir, "telemetry.log")
    private val lock = ReentrantLock()
    private val timestampFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US)

    fun record(event: String, detail: String = "") {
        val line = buildString {
            append(timestampFormat.format(Date(System.currentTimeMillis())))
            append(" ")
            append(event)
            if (detail.isNotEmpty()) {
                append(" ")
                append(detail)
            }
        }
        Log.i(TAG, line)
        lock.withLock {
            try {
                rotateIfOversized()
                logFile.appendText(line + "\n")
            } catch (e: Exception) {
                // Telemetry must never crash the component it's observing.
                Log.e(TAG, "failed to append telemetry line", e)
            }
        }
    }

    private fun rotateIfOversized() {
        if (logFile.exists() && logFile.length() > MAX_LOG_BYTES) {
            val kept = logFile.readLines().takeLast(MAX_LOG_KEPT_LINES)
            logFile.writeText(kept.joinToString("\n") + "\n")
        }
    }

    fun readAll(): String = lock.withLock {
        if (!logFile.exists()) "" else logFile.readText()
    }

    fun tailLines(n: Int): List<String> = lock.withLock {
        if (!logFile.exists()) emptyList() else logFile.readLines().takeLast(n)
    }

    companion object {
        const val TAG = "JarvisCompanion"
        private const val MAX_LOG_BYTES = 1_000_000L
        private const val MAX_LOG_KEPT_LINES = 2_000

        // Exact event vocabulary — do not invent new event names ad hoc,
        // extend this list deliberately.
        const val APP_CREATED = "APP_CREATED"
        const val SERVICE_CREATED = "SERVICE_CREATED"
        const val SERVICE_STARTED = "SERVICE_STARTED"
        const val SERVICE_DESTROYED = "SERVICE_DESTROYED"
        const val FOREGROUND_ENTERED = "FOREGROUND_ENTERED"
        const val NOTIFICATION_POSTED = "NOTIFICATION_POSTED"
        const val TASK_REMOVED = "TASK_REMOVED"
        const val SERVICE_RESTARTED_BY_OS = "SERVICE_RESTARTED_BY_OS"

        const val WS_CONNECTING = "WS_CONNECTING"
        const val WS_CONNECTED = "WS_CONNECTED"
        const val WS_RECONNECTED = "WS_RECONNECTED"
        const val WS_DISCONNECTED = "WS_DISCONNECTED"
        const val WS_RECONNECT_SCHEDULED = "WS_RECONNECT_SCHEDULED"
        const val WS_HEARTBEAT_SENT = "WS_HEARTBEAT_SENT"
        const val WS_FRAME_RECEIVED = "WS_FRAME_RECEIVED"
        // A callback arrived from a connection attempt already superseded
        // by a newer one — ignored rather than misattributed to current
        // state. See network.ConnectionGenerationTracker.
        const val WS_STALE_CALLBACK_IGNORED = "WS_STALE_CALLBACK_IGNORED"
        // Milestone 9B.2: a disconnect classified as DisconnectReason.isPermanent
        // — the client has stopped retrying (see ConnectionState.FAILED_PERMANENT,
        // TD-021). Superseded the milestone 9B.1 WS_AUTH_REJECTED constant,
        // which only covered one of several now-classified permanent reasons.
        const val WS_PERMANENT_FAILURE = "WS_PERMANENT_FAILURE"
        // Milestone 9B.2 (ADR-012): the state-sync/capability-advertisement
        // message sent once per successful (re)connect.
        const val DEVICE_STATUS_SENT = "DEVICE_STATUS_SENT"

        const val NETWORK_LOST = "NETWORK_LOST"
        const val NETWORK_AVAILABLE = "NETWORK_AVAILABLE"
        const val SCREEN_OFF_OBSERVED = "SCREEN_OFF_OBSERVED"
        const val SCREEN_ON_OBSERVED = "SCREEN_ON_OBSERVED"

        const val PAIRING_PROBE_STARTED = "PAIRING_PROBE_STARTED"
        const val PAIRING_PROBE_FAILED = "PAIRING_PROBE_FAILED"
        const val PAIRING_CONFIRMED = "PAIRING_CONFIRMED"
        const val PAIRING_CERT_MISMATCH = "PAIRING_CERT_MISMATCH"
        const val PAIRING_CLEARED = "PAIRING_CLEARED"

        // Milestone 9B.3 (ADR-015): inbound attention_* / pending_attention
        // frames applied to the client-side AttentionRepository mirror, and
        // outbound dismiss/talk-now commands sent through it.
        const val ATTENTION_SNAPSHOT_APPLIED = "ATTENTION_SNAPSHOT_APPLIED"
        const val ATTENTION_EVENT_APPLIED = "ATTENTION_EVENT_APPLIED"
        const val ATTENTION_COMMAND_SENT = "ATTENTION_COMMAND_SENT"

        // Milestone 9B.8 (ADR-017): PresenceService hosting WakeWordManager.
        const val WAKEWORD_MANAGER_CREATED = "WAKEWORD_MANAGER_CREATED"
        const val WAKEWORD_STARTED = "WAKEWORD_STARTED"
        const val WAKEWORD_STOPPED = "WAKEWORD_STOPPED"
        const val WAKEWORD_PAUSED_FOR_VOICE_SESSION = "WAKEWORD_PAUSED_FOR_VOICE_SESSION"
        const val WAKEWORD_RESUMED_AFTER_VOICE_SESSION = "WAKEWORD_RESUMED_AFTER_VOICE_SESSION"
        const val WAKEWORD_DETECTED = "WAKEWORD_DETECTED"
        // Milestone 9B.9 (ADR-017 Section C): confirmation-gated handoff
        // from a wake-word detection to a launched VoiceActivity.
        const val WAKEWORD_HANDOFF_LAUNCHED = "WAKEWORD_HANDOFF_LAUNCHED"
        const val WAKEWORD_HANDOFF_FAILED = "WAKEWORD_HANDOFF_FAILED"
    }
}
