package com.jarvis.companion.pairing

import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket

class PairingProbeResult(
    val fingerprint: String,
    val leafCertificate: X509Certificate,
)

/**
 * Performs the one-shot TLS handshake used to discover a candidate
 * server's certificate fingerprint before the user confirms pairing. Never
 * sends the API token or touches /ws — this is TOFU discovery only, run on
 * a background thread by the caller (blocking I/O).
 */
class PairingClient {
    fun probe(host: String, port: Int): PairingProbeResult {
        val discoveryTrustManager = DiscoveryTrustManager()
        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(null, arrayOf(discoveryTrustManager), SecureRandom())
        val socketFactory = sslContext.socketFactory
        (socketFactory.createSocket(host, port) as SSLSocket).use { socket ->
            socket.startHandshake()
        }
        val leaf: X509Certificate = discoveryTrustManager.lastSeenLeaf
            ?: error("TLS handshake completed but no certificate was captured")
        return PairingProbeResult(CertFingerprint.sha256(leaf), leaf)
    }
}
