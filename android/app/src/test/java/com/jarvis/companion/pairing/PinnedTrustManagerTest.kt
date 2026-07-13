package com.jarvis.companion.pairing

import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import org.junit.Assert.assertThrows
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

private fun certWithEncodedBytes(bytes: ByteArray): X509Certificate {
    val cert = mock(X509Certificate::class.java)
    `when`(cert.encoded).thenReturn(bytes)
    return cert
}

private fun sha256HexColons(bytes: ByteArray): String =
    MessageDigest.getInstance("SHA-256").digest(bytes).joinToString(":") { "%02X".format(it) }

class PinnedTrustManagerTest {

    @Test
    fun acceptsCertificateMatchingThePinnedFingerprint() {
        val certBytes = "server-cert-bytes".toByteArray()
        val fingerprint = sha256HexColons(certBytes)
        val trustManager = PinnedTrustManager(fingerprint)

        // Must not throw.
        trustManager.checkServerTrusted(arrayOf(certWithEncodedBytes(certBytes)), "RSA")
    }

    @Test
    fun rejectsCertificateWithDifferentFingerprint() {
        val pinned = sha256HexColons("original-server-cert".toByteArray())
        val trustManager = PinnedTrustManager(pinned)
        val differentCert = certWithEncodedBytes("attacker-cert".toByteArray())

        assertThrows(CertificateException::class.java) {
            trustManager.checkServerTrusted(arrayOf(differentCert), "RSA")
        }
    }

    @Test
    fun rejectsEmptyChain() {
        val trustManager = PinnedTrustManager("AA:BB:CC")
        assertThrows(CertificateException::class.java) {
            trustManager.checkServerTrusted(emptyArray(), "RSA")
        }
    }

    @Test
    fun neverTrustsClientCertificates() {
        val trustManager = PinnedTrustManager("AA:BB:CC")
        assertThrows(CertificateException::class.java) {
            trustManager.checkClientTrusted(emptyArray(), "RSA")
        }
    }
}
