package com.jarvis.companion.network

import com.jarvis.companion.pairing.PairingConfig
import com.jarvis.companion.pairing.PinnedTrustManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.security.SecureRandom
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

private const val SAFETY_MARGIN_SECONDS = 120L

class WsTokenClient(
    private val pairingConfig: PairingConfig,
    private val deviceId: String,
    private val baseUrl: String = "https://${pairingConfig.host}:${pairingConfig.port}",
) {
    private val mutex = Mutex()

    // Milestone 9B.2 review finding: two separate `var`s (token,
    // expiresAt) let invalidateCache() — which cannot take the suspend
    // mutex — clear one without the other atomically, risking a torn read
    // in getValidToken(). A single @Volatile reference to an immutable
    // pair makes invalidateCache() a plain atomic write, safe to call
    // from any thread without needing the mutex itself.
    @Volatile
    private var cached: TokenResponse? = null

    private val client: OkHttpClient by lazy {
        val trustManager = PinnedTrustManager(pairingConfig.pinnedFingerprint)
        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(null, arrayOf<X509TrustManager>(trustManager), SecureRandom())
        OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            .build()
    }

    suspend fun getValidToken(): String = mutex.withLock {
        val now = System.currentTimeMillis() / 1000
        cached?.let { entry ->
            if (entry.expiresAt - now >= SAFETY_MARGIN_SECONDS) {
                return@withLock entry.token
            }
        }
        val response = fetchToken()
        cached = response
        response.token
    }

    fun invalidateCache() {
        cached = null
    }

    private suspend fun fetchToken(): TokenResponse = withContext(Dispatchers.IO) {
        val requestBody = "{\"client_id\":\"$deviceId\"}"
        val request = Request.Builder()
            .url("$baseUrl/api/ws-token")
            .header("Authorization", "Bearer ${pairingConfig.apiToken}")
            .post(requestBody.toRequestBody(JSON_MEDIA_TYPE))
            .build()
        val response = client.newCall(request).execute()
        if (!response.isSuccessful) {
            throw IOException(
                "Failed to fetch WS token: HTTP ${response.code} ${response.message}"
            )
        }
        val body = response.body?.string() ?: throw IOException("Empty response body")
        parseResponse(body)
    }

    companion object {
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

private data class TokenResponse(val token: String, val expiresAt: Long)

/** Milestone 9B.2 review finding: a malformed-but-200-OK response (only
 * reachable if a future server change or an intermediary rewrites the
 * body — the real /api/ws-token endpoint always returns this shape) used
 * to crash with an uncaught NumberFormatException instead of a clean
 * IOException. Never trust a successful HTTP status to mean a parseable
 * body. */
private fun parseResponse(body: String): TokenResponse {
    val tokenMarker = "\"token\":\""
    if (!body.contains(tokenMarker)) {
        throw IOException("Malformed WS token response (no token field): $body")
    }
    val token = body.substringAfter(tokenMarker).substringBefore("\"")

    val expiresAtMarker = "\"expires_at\":"
    if (!body.contains(expiresAtMarker)) {
        throw IOException("Malformed WS token response (no expires_at field): $body")
    }
    val digits = body.substringAfter(expiresAtMarker).takeWhile { it.isDigit() }
    val expiresAt = digits.toLongOrNull()
        ?: throw IOException("Malformed WS token response (non-numeric expires_at): $body")

    return TokenResponse(token, expiresAt)
}