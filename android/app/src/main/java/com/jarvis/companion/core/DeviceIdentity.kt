package com.jarvis.companion.core

import java.util.UUID

private const val KEY_DEVICE_ID = "device_identity_uuid"

/**
 * Stable per-install identifier, minted once and persisted. Used to
 * correlate this device's connections in server-side logs across
 * reconnects/app restarts — a random UUID per WebSocket connection would
 * make that correlation impossible. Not a substitute for the pairing API
 * token: this identifies "which device," not "is this device authorized."
 */
class DeviceIdentity(private val store: SecureConfigStore) {
    fun get(): String {
        store.getString(KEY_DEVICE_ID)?.let { return it }
        val minted = UUID.randomUUID().toString()
        store.putString(KEY_DEVICE_ID, minted)
        return minted
    }
}
