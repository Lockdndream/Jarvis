package com.jarvis.companion.settings

import com.jarvis.companion.pairing.PairingConfig
import com.jarvis.companion.pairing.PinnedTrustManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException
import java.security.SecureRandom
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * Reads the operational settings Jarvis already exposes at the existing,
 * LAN-open `GET /api/settings` (app/main.py) — no new protocol, no new
 * endpoint, no Authorization header (that endpoint is deliberately
 * unauthenticated; see its own comment: "the existing, already-LAN-open
 * channel the phone reads any setting from, deliberately not a new
 * command channel to the phone"). Same pinned-TLS pattern as
 * WsTokenClient, minus the Bearer token this endpoint doesn't require.
 */
class OperationalSettingsClient(
    private val pairingConfig: PairingConfig,
    private val baseUrl: String = "https://${pairingConfig.host}:${pairingConfig.port}",
) {
    private val client: OkHttpClient by lazy {
        val trustManager = PinnedTrustManager(pairingConfig.pinnedFingerprint)
        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(null, arrayOf<X509TrustManager>(trustManager), SecureRandom())
        OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            .build()
    }

    /** Throws IOException on any transport failure, non-2xx response, or
     * unparseable body — callers decide how to degrade (this milestone's
     * repository cache is exactly that degrade path: keep the last-known
     * value rather than propagate the failure to the connection state). */
    suspend fun fetchConnectivityMode(): String = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$baseUrl/api/settings")
            .get()
            .build()
        val response = client.newCall(request).execute()
        if (!response.isSuccessful) {
            throw IOException("Failed to fetch settings: HTTP ${response.code} ${response.message}")
        }
        val body = response.body?.string() ?: throw IOException("Empty response body")
        try {
            JSONObject(body).optString("connectivity_mode", ConnectivityPolicy.DEFAULT_MODE)
        } catch (e: org.json.JSONException) {
            throw IOException("Malformed settings response: $body", e)
        }
    }
}
