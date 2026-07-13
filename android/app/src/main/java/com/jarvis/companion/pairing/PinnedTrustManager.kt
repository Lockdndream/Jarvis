package com.jarvis.companion.pairing

import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.X509TrustManager

/**
 * Trust-on-first-use pinning: the LAN server's certificate is self-signed
 * (mkcert, per-machine — see certs/), so there is no public CA to validate
 * against. Once a fingerprint has been confirmed during pairing
 * (see PairingRepository / DiscoveryTrustManager), every subsequent
 * connection is trusted *only* if the presented leaf certificate's SHA-256
 * fingerprint matches exactly — protecting against a later MITM even though
 * the initial trust decision was manual, the same security model as SSH
 * host-key pinning.
 */
class PinnedTrustManager(private val pinnedFingerprint: String) : X509TrustManager {
    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("Client certificates are not used by this app")
    }

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull()
            ?: throw CertificateException("No server certificate presented")
        val actual = CertFingerprint.sha256(leaf)
        if (actual != pinnedFingerprint) {
            throw CertificateException(
                "Server certificate fingerprint changed: expected $pinnedFingerprint, got $actual"
            )
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}
