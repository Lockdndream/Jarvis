package com.jarvis.companion.pairing

/** Everything needed to open an authenticated, pinned connection to a
 * paired Jarvis server. No cloud account, no discovery — host/port are
 * always entered manually against a known LAN address. */
data class PairingConfig(
    val host: String,
    val port: Int,
    val apiToken: String,
    val pinnedFingerprint: String,
    val pairedAtEpochMs: Long,
)
