package com.jarvis.presencespike

import android.content.Context
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * Structured, timestamped event log for the presence-survival spike.
 *
 * Every event is written to both logcat (so `adb logcat -s PresenceSpike`
 * works) and a local file under app-internal storage (so it survives a
 * screen-off/locked period without needing an attached debugger — pull it
 * afterward with `adb pull`).
 *
 * Never fabricates an event that didn't actually happen — callers only
 * invoke [record] for lifecycle points that genuinely occurred.
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
                logFile.appendText(line + "\n")
            } catch (e: Exception) {
                // Telemetry must never crash the service it's observing.
                Log.e(TAG, "failed to append telemetry line", e)
            }
        }
    }

    fun readAll(): String = lock.withLock {
        if (!logFile.exists()) "" else logFile.readText()
    }

    fun tailLines(n: Int): List<String> = lock.withLock {
        if (!logFile.exists()) emptyList() else logFile.readLines().takeLast(n)
    }

    companion object {
        const val TAG = "PresenceSpike"

        // Exact event vocabulary from the spike's README/protocol — do not
        // invent new event names ad hoc, extend this list deliberately.
        const val SERVICE_CREATED = "SERVICE_CREATED"
        const val SERVICE_STARTED = "SERVICE_STARTED"
        const val FOREGROUND_ENTERED = "FOREGROUND_ENTERED"
        const val WS_CONNECTING = "WS_CONNECTING"
        const val WS_CONNECTED = "WS_CONNECTED"
        const val WS_DISCONNECTED = "WS_DISCONNECTED"
        const val WS_RECONNECT_SCHEDULED = "WS_RECONNECT_SCHEDULED"
        const val WS_RECONNECTED = "WS_RECONNECTED"
        const val WS_HEARTBEAT_SENT = "WS_HEARTBEAT_SENT"
        const val NETWORK_LOST = "NETWORK_LOST"
        const val NETWORK_AVAILABLE = "NETWORK_AVAILABLE"
        const val SERVICE_DESTROYED = "SERVICE_DESTROYED"
        const val TASK_REMOVED = "TASK_REMOVED"
        const val SERVICE_RESTARTED_BY_OS = "SERVICE_RESTARTED_BY_OS"
        // Milestone 9B.0 Phase 1: a callback arrived from a WebSocket
        // connection attempt that has since been superseded by a newer
        // one — ignored rather than misattributed to the current
        // connection's state. See ConnectionGenerationTracker.
        const val WS_STALE_CALLBACK_IGNORED = "WS_STALE_CALLBACK_IGNORED"
        // Milestone 9B.0 Phase 2: any inbound frame — the strongest
        // liveness signal available, since it proves the *server* reached
        // *us*, not just that we attempted to reach it.
        const val WS_FRAME_RECEIVED = "WS_FRAME_RECEIVED"
        const val NOTIFICATION_POSTED = "NOTIFICATION_POSTED"
        // Only emitted where legitimately observable (see PresenceService) —
        // never fabricated from a missing/implicit signal.
        const val SCREEN_OFF_OBSERVED = "SCREEN_OFF_OBSERVED"
        const val SCREEN_ON_OBSERVED = "SCREEN_ON_OBSERVED"
    }
}
