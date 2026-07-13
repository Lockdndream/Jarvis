package com.jarvis.companion.pairing

import java.security.MessageDigest
import java.security.cert.X509Certificate

/** Pure formatting/hashing — no I/O, no Android dependency, directly testable. */
object CertFingerprint {
    fun sha256(cert: X509Certificate): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(cert.encoded)
        return digest.joinToString(":") { "%02X".format(it) }
    }
}
