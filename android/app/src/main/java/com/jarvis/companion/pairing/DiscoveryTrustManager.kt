package com.jarvis.companion.pairing

import java.security.cert.X509Certificate
import javax.net.ssl.X509TrustManager

/**
 * Used for exactly one purpose: the pairing screen's initial probe request,
 * to retrieve the server's certificate so its fingerprint can be shown to
 * the user for manual confirmation (trust-on-first-use). Deliberately
 * accepts any certificate — this instance must never be reused for the
 * app's real traffic; PinnedTrustManager is what protects every connection
 * after pairing completes.
 */
class DiscoveryTrustManager : X509TrustManager {
    var lastSeenLeaf: X509Certificate? = null
        private set

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        lastSeenLeaf = chain?.firstOrNull()
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}
