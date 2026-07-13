package com.jarvis.presencespike

import android.os.Handler
import android.os.Looper
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit
import kotlin.math.min
import kotlin.random.Random

/**
 * Minimal WebSocket transport for the presence-survival spike.
 *
 * Points at the real Jarvis dev server's existing `/ws` endpoint (same
 * protocol real clients use — this spike does not extend or fork the
 * protocol) over the real mkcert-secured LAN HTTPS deployment already
 * documented in the repo root README.md. Trust for the LAN cert is
 * scoped via network_security_config.xml to this exact host only.
 * Dev-only value — not a secret, just this machine's current LAN IP.
 */
const val TEST_WS_URL = "wss://192.168.1.27:8443/ws"

private const val HEARTBEAT_INTERVAL_MS = 30_000L
private const val BACKOFF_INITIAL_MS = 2_000L
private const val BACKOFF_MAX_MS = 60_000L

class TestConnectionClient(
    private val url: String = TEST_WS_URL,
    private val telemetry: TelemetryRecorder,
    private val onStateChange: (connected: Boolean) -> Unit = {},
) {
    private val client = OkHttpClient.Builder()
        .pingInterval(0, TimeUnit.SECONDS) // we send our own app-level heartbeat instead
        .build()

    private val mainHandler = Handler(Looper.getMainLooper())
    private var webSocket: WebSocket? = null
    private var stopped = false
    private var backoffMs = BACKOFF_INITIAL_MS
    // Cumulative count for this TestConnectionClient's lifetime — reported
    // on every reconnect so a single telemetry line shows "how many times
    // so far," not just "it happened again," making multi-hour logs easier
    // to summarize without re-counting lines by hand.
    private var reconnectCount = 0

    // Phase 1 (Milestone 9B.0) fix: each connect() attempt gets its own
    // generation number, and each callback checks it's still current
    // before touching shared state. Without this, a superseded (already-
    // replaced) connection's delayed onFailure/onClosed callback could be
    // misattributed to the current connection — the real cause of the
    // ~41s "phantom disconnect" observed on the real S20 FE right after a
    // reconnect. See ConnectionGenerationTrackerTest for the regression test.
    private val generationTracker = ConnectionGenerationTracker()

    // Phase 2: makes "heartbeat interval actually observed" directly
    // readable from a single log line instead of requiring a manual diff
    // between two lines.
    private var lastHeartbeatSentAtMs: Long? = null
    private val heartbeatRunnable = object : Runnable {
        override fun run() {
            val now = System.currentTimeMillis()
            val sinceLastMs = lastHeartbeatSentAtMs?.let { now - it }
            lastHeartbeatSentAtMs = now
            val sent = webSocket?.send("{\"type\":\"heartbeat\"}") ?: false
            telemetry.record(
                TelemetryRecorder.WS_HEARTBEAT_SENT,
                "queued=$sent intervalSinceLastMs=${sinceLastMs ?: "n/a"}",
            )
            mainHandler.postDelayed(this, HEARTBEAT_INTERVAL_MS)
        }
    }
    private var reconnectRunnable: Runnable? = null

    fun connect() {
        stopped = false
        val generation = generationTracker.startNewGeneration()
        telemetry.record(TelemetryRecorder.WS_CONNECTING, "url=$url generation=$generation")
        val request = Request.Builder().url(url).build()
        webSocket = client.newWebSocket(request, ConnectionListener(generation))
    }

    fun disconnect() {
        stopped = true
        // Invalidate the connection being closed too — any callback still
        // in flight for it must be recognized as stale, not just future
        // reconnect attempts.
        generationTracker.startNewGeneration()
        mainHandler.removeCallbacks(heartbeatRunnable)
        reconnectRunnable?.let { mainHandler.removeCallbacks(it) }
        webSocket?.close(1000, "client stopping")
        webSocket = null
    }

    private fun scheduleReconnect() {
        if (stopped) return
        val delay = backoffMs
        telemetry.record(TelemetryRecorder.WS_RECONNECT_SCHEDULED, "delayMs=$delay")
        val runnable = Runnable {
            if (!stopped) connect()
        }
        reconnectRunnable = runnable
        mainHandler.postDelayed(runnable, delay)
        // Bounded exponential backoff with jitter, capped at BACKOFF_MAX_MS.
        backoffMs = min(BACKOFF_MAX_MS, backoffMs * 2) + Random.nextLong(0, 500)
    }

    private inner class ConnectionListener(private val generation: Int) : WebSocketListener() {
        private fun stillCurrent(event: String): Boolean {
            if (generationTracker.isCurrent(generation)) return true
            telemetry.record(
                TelemetryRecorder.WS_STALE_CALLBACK_IGNORED,
                "event=$event generation=$generation current=${generationTracker.currentGeneration()}",
            )
            return false
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!stillCurrent("onMessage")) return
            telemetry.record(TelemetryRecorder.WS_FRAME_RECEIVED, "bytes=${text.length} generation=$generation")
        }

        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!stillCurrent("onOpen")) return
            val wasReconnect = backoffMs > BACKOFF_INITIAL_MS
            backoffMs = BACKOFF_INITIAL_MS
            if (wasReconnect) {
                reconnectCount += 1
                telemetry.record(TelemetryRecorder.WS_RECONNECTED, "reconnectCount=$reconnectCount generation=$generation")
            } else {
                telemetry.record(TelemetryRecorder.WS_CONNECTED, "generation=$generation")
            }
            onStateChange(true)
            mainHandler.post(heartbeatRunnable)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!stillCurrent("onClosed")) return
            telemetry.record(TelemetryRecorder.WS_DISCONNECTED, "code=$code reason=$reason generation=$generation")
            onStateChange(false)
            mainHandler.removeCallbacks(heartbeatRunnable)
            scheduleReconnect()
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!stillCurrent("onFailure")) return
            telemetry.record(
                TelemetryRecorder.WS_DISCONNECTED,
                "failure=${t.javaClass.simpleName}:${t.message} generation=$generation",
            )
            onStateChange(false)
            mainHandler.removeCallbacks(heartbeatRunnable)
            scheduleReconnect()
        }
    }
}
