package com.jarvis.companion.network

import java.net.ConnectException
import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLHandshakeException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DisconnectClassifierTest {

    @Test
    fun classifiesLegacyAuthCloseCodes() {
        assertEquals(DisconnectReason.AUTH, DisconnectClassifier.classifyClose(1008))
        assertEquals(DisconnectReason.AUTH, DisconnectClassifier.classifyClose(401))
        assertEquals(DisconnectReason.AUTH, DisconnectClassifier.classifyClose(403))
    }

    @Test
    fun classifiesAdr014CloseCodes() {
        assertEquals(DisconnectReason.TOKEN_EXPIRED, DisconnectClassifier.classifyClose(4001))
        assertEquals(DisconnectReason.TOKEN_INVALID, DisconnectClassifier.classifyClose(4002))
        assertEquals(DisconnectReason.AUTH, DisconnectClassifier.classifyClose(4003))
        assertEquals(DisconnectReason.AUTH, DisconnectClassifier.classifyClose(4004))
    }

    @Test
    fun unrecognizedCloseCodeIsUnknown() {
        assertEquals(DisconnectReason.UNKNOWN, DisconnectClassifier.classifyClose(1000))
        assertEquals(DisconnectReason.UNKNOWN, DisconnectClassifier.classifyClose(1006))
    }

    @Test
    fun classifiesCertificateFingerprintMismatchAsCertificate() {
        val t = SSLHandshakeException(
            "Server certificate fingerprint changed: expected AA:BB, got CC:DD"
        )
        assertEquals(DisconnectReason.CERTIFICATE, DisconnectClassifier.classifyFailure(t))
    }

    @Test
    fun otherSslHandshakeExceptionsAreNotMisclassifiedAsCertificate() {
        // A generic handshake failure (e.g. protocol negotiation) is not
        // the specific fingerprint-mismatch condition PinnedTrustManager
        // raises — must not be silently treated as a permanent failure.
        val t = SSLHandshakeException("handshake failed")
        assertEquals(DisconnectReason.UNKNOWN, DisconnectClassifier.classifyFailure(t))
    }

    @Test
    fun classifiesConnectExceptionAsServerDown() {
        val t = ConnectException("Failed to connect to /192.168.1.27:8443")
        assertEquals(DisconnectReason.SERVER_DOWN, DisconnectClassifier.classifyFailure(t))
    }

    @Test
    fun classifiesUnknownHostAsNetwork() {
        assertEquals(DisconnectReason.NETWORK, DisconnectClassifier.classifyFailure(UnknownHostException("no route")))
    }

    @Test
    fun classifiesSocketTimeoutAsNetwork() {
        assertEquals(DisconnectReason.NETWORK, DisconnectClassifier.classifyFailure(SocketTimeoutException("timeout")))
    }

    @Test
    fun classifiesSocketExceptionAsNetwork() {
        // The real message observed on a real device Wi-Fi drop
        // (Milestone 9B.1 real-device validation, item 7).
        assertEquals(
            DisconnectReason.NETWORK,
            DisconnectClassifier.classifyFailure(SocketException("Software caused connection abort")),
        )
    }

    @Test
    fun unrecognizedThrowableIsUnknownNotMisclassified() {
        assertEquals(DisconnectReason.UNKNOWN, DisconnectClassifier.classifyFailure(RuntimeException("something else")))
    }

    @Test
    fun authAndCertificateArePermanent() {
        assertTrue(DisconnectReason.AUTH.isPermanent)
        assertTrue(DisconnectReason.CERTIFICATE.isPermanent)
    }

    @Test
    fun tokenExpiredAndTokenInvalidAreNotPermanentButNeedTokenRefresh() {
        // Milestone 9B.2 (ADR-014): unlike AUTH/CERTIFICATE, these are
        // recoverable without human re-pairing — fetch a fresh token and
        // retry, don't give up. See TD-021's original scope (which
        // predates real close codes existing at all).
        assertTrue(!DisconnectReason.TOKEN_EXPIRED.isPermanent)
        assertTrue(!DisconnectReason.TOKEN_INVALID.isPermanent)
        assertTrue(DisconnectReason.TOKEN_EXPIRED.needsTokenRefresh)
        assertTrue(DisconnectReason.TOKEN_INVALID.needsTokenRefresh)
    }

    @Test
    fun networkServerDownUnknownUserStoppedAreNotPermanent() {
        assertTrue(!DisconnectReason.NETWORK.isPermanent)
        assertTrue(!DisconnectReason.SERVER_DOWN.isPermanent)
        assertTrue(!DisconnectReason.UNKNOWN.isPermanent)
        assertTrue(!DisconnectReason.USER_STOPPED.isPermanent)
        assertTrue(!DisconnectReason.NONE.isPermanent)
    }

    @Test
    fun onlyTokenExpiredAndTokenInvalidNeedTokenRefresh() {
        assertTrue(!DisconnectReason.AUTH.needsTokenRefresh)
        assertTrue(!DisconnectReason.CERTIFICATE.needsTokenRefresh)
        assertTrue(!DisconnectReason.NETWORK.needsTokenRefresh)
        assertTrue(!DisconnectReason.SERVER_DOWN.needsTokenRefresh)
        assertTrue(!DisconnectReason.UNKNOWN.needsTokenRefresh)
        assertTrue(!DisconnectReason.USER_STOPPED.needsTokenRefresh)
        assertTrue(!DisconnectReason.NONE.needsTokenRefresh)
    }
}
