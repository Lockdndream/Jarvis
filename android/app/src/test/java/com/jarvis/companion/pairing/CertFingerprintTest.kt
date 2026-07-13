package com.jarvis.companion.pairing

import java.security.MessageDigest
import java.security.cert.X509Certificate
import org.junit.Assert.assertEquals
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class CertFingerprintTest {

    @Test
    fun formatsAsUppercaseColonSeparatedHex() {
        val bytes = "hello-cert".toByteArray()
        val expectedDigest = MessageDigest.getInstance("SHA-256").digest(bytes)
        val expected = expectedDigest.joinToString(":") { "%02X".format(it) }

        val cert = mock(X509Certificate::class.java)
        `when`(cert.encoded).thenReturn(bytes)

        assertEquals(expected, CertFingerprint.sha256(cert))
    }

    @Test
    fun differentCertificatesProduceDifferentFingerprints() {
        val certA = mock(X509Certificate::class.java)
        `when`(certA.encoded).thenReturn("cert-a".toByteArray())
        val certB = mock(X509Certificate::class.java)
        `when`(certB.encoded).thenReturn("cert-b".toByteArray())

        assert(CertFingerprint.sha256(certA) != CertFingerprint.sha256(certB))
    }
}
