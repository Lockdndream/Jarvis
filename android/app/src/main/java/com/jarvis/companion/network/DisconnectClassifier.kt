package com.jarvis.companion.network

import java.net.ConnectException
import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLHandshakeException

/**
 * Maps a WebSocket close code or a connection failure Throwable to a
 * [DisconnectReason]. Pure, no Android/OkHttp dependency — every branch
 * corresponds to a real signal this app can actually observe (ADR-010:
 * never fabricate a classification the evidence doesn't support), falling
 * back to UNKNOWN rather than guessing.
 */
object DisconnectClassifier {
    // Milestone 9B.2 (ADR-014): app/main.py's /ws handshake now always
    // accepts before rejecting an unauthorized connection, then sends a
    // real WS close frame with one of these codes — replacing the earlier
    // pre-accept-HTTP-403 mechanism (ADR-012's uvicorn-source finding).
    // OkHttp surfaces these via onClosed's `code`, not onFailure's HTTP
    // status, for every /ws auth rejection produced by the current server.
    const val WS_CLOSE_TOKEN_EXPIRED = 4001
    const val WS_CLOSE_TOKEN_INVALID = 4002
    const val WS_CLOSE_TOKEN_MISSING = 4003
    const val WS_CLOSE_AUTH_FAILED = 4004

    // Kept for defensive/historical compatibility only: 401/403/1008 were
    // how a pre-ADR-014 server (or a pre-accept HTTP-level rejection, if
    // the server-side mechanism ever reverts) could surface an auth
    // failure. The current server never produces these for /ws specifically
    // — real evidence from ADR-012 showed 1008 was already dead even
    // before ADR-014's accept-then-close change.
    private val LEGACY_AUTH_CODES = setOf(401, 403, 1008)

    fun classifyClose(code: Int): DisconnectReason = when (code) {
        WS_CLOSE_TOKEN_EXPIRED -> DisconnectReason.TOKEN_EXPIRED
        WS_CLOSE_TOKEN_INVALID -> DisconnectReason.TOKEN_INVALID
        WS_CLOSE_TOKEN_MISSING, WS_CLOSE_AUTH_FAILED -> DisconnectReason.AUTH
        in LEGACY_AUTH_CODES -> DisconnectReason.AUTH
        else -> DisconnectReason.UNKNOWN
    }

    fun classifyFailure(throwable: Throwable): DisconnectReason {
        val message = throwable.message ?: ""
        return when {
            throwable is SSLHandshakeException &&
                message.contains("fingerprint", ignoreCase = true) -> DisconnectReason.CERTIFICATE
            throwable is ConnectException -> DisconnectReason.SERVER_DOWN
            throwable is UnknownHostException -> DisconnectReason.NETWORK
            throwable is SocketTimeoutException -> DisconnectReason.NETWORK
            throwable is SocketException -> DisconnectReason.NETWORK
            else -> DisconnectReason.UNKNOWN
        }
    }
}
