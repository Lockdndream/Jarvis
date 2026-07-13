package com.jarvis.companion.pairing

import com.jarvis.companion.core.SecureConfigStore

private const val KEY_HOST = "pairing_host"
private const val KEY_PORT = "pairing_port"
private const val KEY_TOKEN = "pairing_api_token"
private const val KEY_FINGERPRINT = "pairing_pinned_fingerprint"
private const val KEY_PAIRED_AT = "pairing_paired_at_epoch_ms"

/** Persists the confirmed PairingConfig. Nothing here talks to the network —
 * that's PairingClient's job — this is storage only. */
class PairingRepository(private val store: SecureConfigStore) {
    fun get(): PairingConfig? {
        val host = store.getString(KEY_HOST) ?: return null
        val port = store.getString(KEY_PORT)?.toIntOrNull() ?: return null
        val token = store.getString(KEY_TOKEN) ?: return null
        val fingerprint = store.getString(KEY_FINGERPRINT) ?: return null
        val pairedAt = store.getString(KEY_PAIRED_AT)?.toLongOrNull() ?: return null
        return PairingConfig(host, port, token, fingerprint, pairedAt)
    }

    fun save(config: PairingConfig) {
        store.putString(KEY_HOST, config.host)
        store.putString(KEY_PORT, config.port.toString())
        store.putString(KEY_TOKEN, config.apiToken)
        store.putString(KEY_FINGERPRINT, config.pinnedFingerprint)
        store.putString(KEY_PAIRED_AT, config.pairedAtEpochMs.toString())
    }

    fun clear() {
        store.remove(KEY_HOST)
        store.remove(KEY_PORT)
        store.remove(KEY_TOKEN)
        store.remove(KEY_FINGERPRINT)
        store.remove(KEY_PAIRED_AT)
    }

    fun isPaired(): Boolean = get() != null
}
