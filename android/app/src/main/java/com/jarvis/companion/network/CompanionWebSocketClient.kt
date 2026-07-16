package com.jarvis.companion.network

import com.jarvis.companion.attention.AttentionParser
import com.jarvis.companion.attention.AttentionRepository
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.core.DeviceCapabilities
import com.jarvis.companion.core.DeviceStatus
import com.jarvis.companion.pairing.PairingConfig
import com.jarvis.companion.pairing.PinnedTrustManager
import com.jarvis.companion.telemetry.TelemetryRecorder
import com.jarvis.companion.voice.VoiceSessionParser
import com.jarvis.companion.voice.VoiceSessionRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

private const val HEARTBEAT_INTERVAL_MS = 30_000L

/** The runtime-changeable parts of DeviceStatus (battery exemption,
 * notification permission) — supplied as a callback so this network-layer
 * class never needs an Android Context directly. Everything else in
 * DeviceStatus is either static (capabilities) or known to this class
 * already (generation, deviceId). */
typealias DeviceStatusInputs = () -> DeviceStatusSnapshot

data class DeviceStatusSnapshot(
    val batteryOptimizationExempt: Boolean,
    val notificationPermissionGranted: Boolean,
)

/**
 * Production WebSocket client for the real, existing /ws protocol.
 * Lifecycle-aware: the caller (PresenceService) drives start()/stop()
 * explicitly to match its own onCreate/onDestroy — this class never
 * starts or stops itself.
 *
 * Reuses two Milestone 9B.0 spike patterns validated on real hardware:
 * ConnectionGenerationTracker (stale-callback guard) and an app-level
 * heartbeat frame the server harmlessly ignores (no server-side protocol
 * change for that specific frame — see ADR-011). BackoffPolicy fixes a
 * cap/jitter ordering bug found in the spike.
 *
 * Milestone 9B.2 additions (ADR-012): every disconnect is classified into
 * a DisconnectReason instead of a generic "disconnected"; a *permanent*
 * reason (AUTH, CERTIFICATE) stops the reconnect loop entirely instead of
 * backing off forever against a condition retrying can never fix
 * (TD-021) — the client instead surfaces ConnectionState.FAILED_PERMANENT
 * and waits for the caller to re-pair. A device_status message
 * (capability advertisement + current state) is sent once per successful
 * (re)connect.
 *
 * Milestone 9B.2 (ADR-014): connects using a short-lived signed token
 * (`?token=`, fetched/cached/renewed via [WsTokenClient]) instead of the
 * deprecated Authorization header. TOKEN_EXPIRED/TOKEN_INVALID are NOT
 * permanent — [WsTokenClient.invalidateCache] is called before the next
 * reconnect attempt so it fetches a fresh token rather than reusing the
 * one the server just rejected.
 *
 * Milestone 9B.3 (ADR-015): every inbound frame is sniffed for a
 * `pending_attention` snapshot or a live `attention_*` event and, if
 * matched, applied to [attentionRepository] — the one client-side mirror
 * of server attention state shared with the widget/in-app screen.
 * [sendAttentionCommand] sends the same `user_message`/
 * `bound_attention_request_id` shape the PWA's dismiss button already
 * sends (no new server-side message type). The repository is cleared on
 * [stop] and on a permanent disconnect (no automatic recovery in flight,
 * so a stale mirror would otherwise persist indefinitely) — not on every
 * transient reconnect, since the next successful connect's
 * `pending_attention` snapshot already supersedes it.
 */
class CompanionWebSocketClient(
    private val telemetry: TelemetryRecorder,
    private val deviceId: String,
    private val statusInputs: DeviceStatusInputs,
    private val attentionRepository: AttentionRepository,
    private val voiceSessionRepository: VoiceSessionRepository,
) {
    private val generationTracker = ConnectionGenerationTracker()
    private val backoff = BackoffPolicy()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private var config: PairingConfig? = null
    private var okHttpClient: OkHttpClient? = null
    private var tokenClient: WsTokenClient? = null
    private var webSocket: WebSocket? = null
    private var heartbeatJob: Job? = null
    private var reconnectJob: Job? = null
    private var stopped = true
    private var lastHeartbeatSentAtMs: Long? = null

    @Volatile
    var state: ConnectionState = ConnectionState.DISCONNECTED
        private set

    @Volatile
    var lastDisconnectReason: DisconnectReason = DisconnectReason.NONE
        private set

    /** Count of successful *re*connects (does not include the first
     * connect of a session) — a diagnostics-facing cumulative counter,
     * distinct from the connection generation (which also counts failed
     * attempts). */
    @Volatile
    var reconnectCount: Int = 0
        private set

    @Volatile
    var connectedSinceMs: Long? = null
        private set

    var onStateChange: (ConnectionState) -> Unit = {}

    fun currentGeneration(): Int = generationTracker.currentGeneration()

    fun lastHeartbeatAgoMs(): Long? = lastHeartbeatSentAtMs?.let { System.currentTimeMillis() - it }

    fun start(pairingConfig: PairingConfig) {
        stopped = false
        config = pairingConfig
        okHttpClient = buildClient(pairingConfig)
        tokenClient = WsTokenClient(pairingConfig, deviceId)
        backoff.reset()
        reconnectCount = 0
        setState(ConnectionState.CONNECTING)
        scope.launch { connect() }
    }

    fun stop() {
        stopped = true
        lastDisconnectReason = DisconnectReason.USER_STOPPED
        reconnectJob?.cancel()
        heartbeatJob?.cancel()
        // Invalidates any in-flight callback for the connection being closed.
        generationTracker.startNewGeneration()
        webSocket?.close(1000, "client stopping")
        webSocket = null
        connectedSinceMs = null
        attentionRepository.clear()
        voiceSessionRepository.clear()
        telemetry.record(TelemetryRecorder.WS_DISCONNECTED, "reason=${DisconnectReason.USER_STOPPED}")
        setState(ConnectionState.DISCONNECTED)
    }

    /** Milestone 9B.4: mirrors sendAttentionCommand()'s shape/no-op-when-
     * disconnected style for the three voice_session_* client-originated
     * message types (see docs/protocols/websocket-protocol-v1.md and
     * app/main.py's voice_session_open/transcript/close handlers).
     * clientRequestId (Milestone 9B.6, ADR-017): optional generic
     * correlation token, echoed back unchanged on both
     * voice_session_opened and voice_session_error — lets a caller (e.g.
     * WakeWordManager's detection handoff) positively identify the
     * response to *this* specific open request, not just "a session
     * became active." */
    fun sendVoiceSessionOpen(
        conversationId: String?,
        attentionRequestId: String?,
        clientRequestId: String? = null,
    ): Boolean {
        val payload = JSONObject().apply {
            put("type", "voice_session_open")
            if (conversationId != null) put("conversation_id", conversationId)
            if (attentionRequestId != null) put("attention_request_id", attentionRequestId)
            if (clientRequestId != null) put("client_request_id", clientRequestId)
        }
        return webSocket?.send(payload.toString()) ?: false
    }

    fun sendVoiceSessionTranscript(voiceSessionId: String, transcript: String): Boolean {
        val payload = JSONObject().apply {
            put("type", "voice_session_transcript")
            put("voice_session_id", voiceSessionId)
            put("transcript", transcript)
        }
        return webSocket?.send(payload.toString()) ?: false
    }

    fun sendVoiceSessionClose(voiceSessionId: String): Boolean {
        val payload = JSONObject().apply {
            put("type", "voice_session_close")
            put("voice_session_id", voiceSessionId)
        }
        return webSocket?.send(payload.toString()) ?: false
    }

    /** Sends the same `user_message`/`bound_attention_request_id` shape
     * the PWA's dismiss/snooze buttons already send (app/static/app.js
     * `sendAttentionCommand`) — no new server-side message type. `phrase`
     * goes through the same deterministic natural-language defer/command
     * parsing the PWA's phrasing does (e.g. "later" to snooze). No-op
     * (returns false) if not currently connected. */
    fun sendAttentionCommand(attentionRequestId: String, phrase: String): Boolean {
        val payload = JSONObject().apply {
            put("type", "user_message")
            put("content", phrase)
            put("bound_attention_request_id", attentionRequestId)
        }
        val sent = webSocket?.send(payload.toString()) ?: false
        telemetry.record(
            TelemetryRecorder.ATTENTION_COMMAND_SENT,
            "queued=$sent attentionRequestId=$attentionRequestId phrase=$phrase",
        )
        return sent
    }

    private fun buildClient(pairingConfig: PairingConfig): OkHttpClient {
        val trustManager = PinnedTrustManager(pairingConfig.pinnedFingerprint)
        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(null, arrayOf<X509TrustManager>(trustManager), SecureRandom())
        return OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            // App-level heartbeat is used instead (see class doc) so native
            // WS ping/pong isn't relied on for liveness telemetry.
            .pingInterval(0, TimeUnit.SECONDS)
            .build()
    }

    private suspend fun connect() {
        val cfg = config ?: return
        val client = okHttpClient ?: return
        val tokens = tokenClient ?: return
        val generation = generationTracker.startNewGeneration()
        telemetry.record(TelemetryRecorder.WS_CONNECTING, "host=${cfg.host} generation=$generation")

        val token = try {
            tokens.getValidToken()
        } catch (e: Exception) {
            // A token-fetch failure (network/HTTP error hitting
            // /api/ws-token) is classified the same way a WS-level
            // network failure would be — retried with backoff, not
            // treated as permanent. Reuses classifyFailure's real
            // exception-type matching rather than inventing a separate
            // path for this REST call.
            if (!generationTracker.isCurrent(generation)) return
            val classified = DisconnectClassifier.classifyFailure(e)
            telemetry.record(
                TelemetryRecorder.WS_DISCONNECTED,
                "tokenFetchFailure=${e.javaClass.simpleName}:${e.message} classified=$classified generation=$generation",
            )
            handleDisconnect(classified)
            return
        }

        // stop() can race with the token fetch above (a real gap the
        // synchronous pre-token-fetch connect() never had) — re-check
        // both the stopped flag and generation currency before opening a
        // socket a concurrent stop() already meant to prevent.
        if (stopped || !generationTracker.isCurrent(generation)) return

        val request = Request.Builder()
            .url("wss://${cfg.host}:${cfg.port}/ws?token=${token}")
            .build()
        webSocket = client.newWebSocket(request, Listener(generation))
    }

    private fun handleDisconnect(reason: DisconnectReason) {
        heartbeatJob?.cancel()
        connectedSinceMs = null
        lastDisconnectReason = reason
        if (stopped) {
            setState(ConnectionState.DISCONNECTED)
            return
        }
        if (reason.needsTokenRefresh) {
            // The cached token was just rejected (expired or otherwise
            // invalid) — reusing it on the next attempt would just
            // reproduce the same rejection. Not a TD-021-style permanent
            // failure: a fresh token, from the still-valid pairing
            // credentials, resolves this without human action.
            tokenClient?.invalidateCache()
        }
        if (reason.isPermanent) {
            // TD-021 fix: a reason that retrying can never resolve on its
            // own (wrong/rejected credential, pinned-certificate mismatch)
            // must not be retried forever — surface it distinctly instead.
            // No automatic recovery is coming, so (ADR-015) the attention
            // mirror is cleared here too rather than left stale forever.
            attentionRepository.clear()
            voiceSessionRepository.clear()
            telemetry.record(TelemetryRecorder.WS_PERMANENT_FAILURE, "reason=$reason")
            setState(ConnectionState.FAILED_PERMANENT)
            return
        }
        setState(ConnectionState.RECONNECTING)
        scheduleReconnect()
    }

    private fun scheduleReconnect() {
        val delay = backoff.nextDelayMs()
        telemetry.record(TelemetryRecorder.WS_RECONNECT_SCHEDULED, "delayMs=$delay")
        reconnectJob = scope.launch {
            delay(delay)
            if (!stopped) connect()
        }
    }

    private fun startHeartbeat(generation: Int) {
        heartbeatJob?.cancel()
        heartbeatJob = scope.launch {
            lastHeartbeatSentAtMs = null
            while (isActive && generationTracker.isCurrent(generation)) {
                delay(HEARTBEAT_INTERVAL_MS)
                if (!generationTracker.isCurrent(generation)) break
                val now = System.currentTimeMillis()
                val sinceLastMs = lastHeartbeatSentAtMs?.let { now - it }
                lastHeartbeatSentAtMs = now
                val sent = webSocket?.send("{\"type\":\"heartbeat\"}") ?: false
                telemetry.record(
                    TelemetryRecorder.WS_HEARTBEAT_SENT,
                    "queued=$sent intervalSinceLastMs=${sinceLastMs ?: "n/a"}",
                )
            }
        }
    }

    private fun sendDeviceStatus(generation: Int) {
        val inputs = statusInputs()
        val status = DeviceStatus(
            deviceId = deviceId,
            capabilities = DeviceCapabilities.CURRENT,
            pairingState = DeviceStatus.PAIRING_STATE_PAIRED,
            batteryOptimizationExempt = inputs.batteryOptimizationExempt,
            notificationPermissionGranted = inputs.notificationPermissionGranted,
            connectionGeneration = generation,
        )
        val sent = webSocket?.send(status.toJson()) ?: false
        telemetry.record(TelemetryRecorder.DEVICE_STATUS_SENT, "queued=$sent generation=$generation")
    }

    private fun setState(newState: ConnectionState) {
        state = newState
        onStateChange(newState)
    }

    /** Sniffs an inbound frame's "type" and, if it's an attention snapshot
     * or live event, applies it to [attentionRepository]. Any other frame
     * type (heartbeat ack, future message kinds) is silently ignored here
     * — this is the one place attention frames are consumed, everything
     * else about the protocol is out of this class's concern. Never
     * throws: an unparseable frame is simply not an attention frame. */
    private fun applyAttentionFrame(text: String) {
        val type = try {
            JSONObject(text).optString("type", "")
        } catch (_: Exception) {
            return
        }
        when {
            type == "pending_attention" -> {
                val requests = AttentionParser.parsePendingAttention(text)
                attentionRepository.applyPendingAttention(requests)
                telemetry.record(TelemetryRecorder.ATTENTION_SNAPSHOT_APPLIED, "count=${requests.size}")
            }
            AttentionParser.isAttentionEventType(type) -> {
                val event = AttentionParser.parseAttentionEvent(text) ?: return
                attentionRepository.applyAttentionEvent(event)
                telemetry.record(
                    TelemetryRecorder.ATTENTION_EVENT_APPLIED,
                    "type=$type attentionRequestId=${event.attentionRequestId} status=${event.status}",
                )
            }
        }
    }

    /** Milestone 9B.4: sniffs an inbound frame's "type" for the five
     * voice_session_* frame types and, if matched, applies it to
     * [voiceSessionRepository] — same one-place-only pattern as
     * [applyAttentionFrame]. Never throws. */
    private fun applyVoiceSessionFrame(text: String) {
        val type = try {
            JSONObject(text).optString("type", "")
        } catch (_: Exception) {
            return
        }
        if (!VoiceSessionParser.isVoiceSessionEventType(type)) return
        when (type) {
            "voice_session_opened" -> VoiceSessionParser.parseOpened(text)?.let { voiceSessionRepository.applyOpened(it) }
            "voice_session_response" -> VoiceSessionParser.parseResponse(text)?.let { voiceSessionRepository.applyResponse(it) }
            "voice_session_error" -> VoiceSessionParser.parseError(text)?.let {
                voiceSessionRepository.applyError(it.voiceSessionId)
                voiceSessionRepository.applyOpenError(it)
            }
            "voice_session_closed" -> VoiceSessionParser.parseClosed(text)?.let { voiceSessionRepository.applyClosed(it.voiceSessionId) }
            "voice_session_invitation" -> voiceSessionRepository.applyInvitation()
        }
    }

    private inner class Listener(private val generation: Int) : WebSocketListener() {
        private fun stillCurrent(event: String): Boolean {
            if (generationTracker.isCurrent(generation)) return true
            telemetry.record(
                TelemetryRecorder.WS_STALE_CALLBACK_IGNORED,
                "event=$event generation=$generation current=${generationTracker.currentGeneration()}",
            )
            return false
        }

        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!stillCurrent("onOpen")) return
            val wasReconnect = state == ConnectionState.RECONNECTING
            backoff.reset()
            connectedSinceMs = System.currentTimeMillis()
            lastDisconnectReason = DisconnectReason.NONE
            if (wasReconnect) reconnectCount += 1
            // Milestone 9B.3 real-device finding: app/main.py only sends
            // pending_attention when there's at least one unresolved item
            // (`if pending_attention:`) — it sends nothing at all when the
            // list is empty. Relying solely on that snapshot to "rebuild
            // from scratch" (ADR-015) would leave stale outstanding items
            // displayed forever after a reconnect where everything got
            // resolved while disconnected, since no message would ever
            // arrive to clear them. Resetting here, unconditionally, before
            // any frame for this connection can be processed (onOpen always
            // precedes onMessage for the same socket) makes correctness
            // independent of that server behavior rather than assuming a
            // message will arrive. Any real pending_attention that does
            // arrive moments later still fully overwrites this via
            // applyPendingAttention's own semantics — no race.
            //
            // Must run BEFORE setState(CONNECTED): setState's onStateChange
            // callback is what triggers PresenceService's widget-push
            // observer (via the connectionState StateFlow emission) — a
            // second, real bug found via real-device validation (the S20 FE
            // widget briefly rendered "never" for last-contact after a
            // reconnect) — the push must see lastContactAtMs already
            // updated, not read it moments before this line runs.
            attentionRepository.applyPendingAttention(emptyList())
            // Milestone 9B.4: a voice session can never survive a
            // disconnect — app/main.py's WS handler unconditionally closes
            // whatever voice session that connection had open (via the
            // finally block added alongside the TD-002 lease guard) before
            // the handler unwinds, and there is no pending-voice-session
            // snapshot message to tell a reconnecting client otherwise.
            // Unlike attention (which has a real, if conditional, snapshot
            // message), correctness here does not even depend on server
            // behavior sending anything — the server-side session is
            // categorically already gone by the time any reconnect can
            // happen, so resetting is always correct, not just a safe
            // default.
            voiceSessionRepository.clear()
            setState(ConnectionState.CONNECTED)
            telemetry.record(
                if (wasReconnect) TelemetryRecorder.WS_RECONNECTED else TelemetryRecorder.WS_CONNECTED,
                "generation=$generation reconnectCount=$reconnectCount",
            )
            startHeartbeat(generation)
            sendDeviceStatus(generation)
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!stillCurrent("onMessage")) return
            telemetry.record(TelemetryRecorder.WS_FRAME_RECEIVED, "bytes=${text.length} generation=$generation")
            applyAttentionFrame(text)
            applyVoiceSessionFrame(text)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!stillCurrent("onClosed")) return
            val classified = DisconnectClassifier.classifyClose(code)
            telemetry.record(
                TelemetryRecorder.WS_DISCONNECTED,
                "code=$code reason=$reason classified=$classified generation=$generation",
            )
            handleDisconnect(classified)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!stillCurrent("onFailure")) return
            // A handshake rejected before accept() (e.g. app/main.py's
            // auth check) surfaces here as an HTTP-level failure, not
            // onClosed — see DisconnectClassifier's AUTH_CODES comment.
            val fromHttpStatus = response?.code?.let { DisconnectClassifier.classifyClose(it) }
                ?.takeIf { it != DisconnectReason.UNKNOWN }
            val classified = fromHttpStatus ?: DisconnectClassifier.classifyFailure(t)
            telemetry.record(
                TelemetryRecorder.WS_DISCONNECTED,
                "failure=${t.javaClass.simpleName}:${t.message} httpStatus=${response?.code} classified=$classified generation=$generation",
            )
            handleDisconnect(classified)
        }
    }
}
