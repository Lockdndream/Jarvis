package com.jarvis.companion.settings

import com.jarvis.companion.core.SecureConfigStore

private const val KEY_CONNECTIVITY_MODE = "operational_connectivity_mode"
private const val KEY_LAST_SYNCED_AT_MS = "operational_last_synced_at_ms"
private const val KEY_MANUAL_CONNECT_REQUESTED = "operational_manual_connect_requested"

/**
 * Android's cache of the connectivity policy JOPS defines and persists
 * server-side (ADR-023). Same SecureConfigStore-backed pattern as
 * WakeWordConfigRepository: synchronous get/set, malformed/missing stored
 * values fall back to a safe default rather than throwing.
 *
 * connectivityMode() defaults to ConnectivityPolicy.DEFAULT_MODE ("always")
 * — not to some Android-local guess — so a device that has never
 * successfully synced, or whose last sync failed, behaves exactly like
 * "Always Connected" rather than silently going dark.
 */
class OperationalSettingsRepository(private val store: SecureConfigStore) {
    fun connectivityMode(): String {
        val stored = store.getString(KEY_CONNECTIVITY_MODE)
        return if (stored in KNOWN_MODES) stored!! else ConnectivityPolicy.DEFAULT_MODE
    }

    fun setConnectivityMode(mode: String) {
        require(mode in KNOWN_MODES) { "connectivityMode must be one of $KNOWN_MODES, was $mode" }
        store.putString(KEY_CONNECTIVITY_MODE, mode)
    }

    fun lastSyncedAtMs(): Long? {
        return store.getString(KEY_LAST_SYNCED_AT_MS)?.toLongOrNull()
    }

    fun setLastSyncedAtMs(value: Long) {
        store.putString(KEY_LAST_SYNCED_AT_MS, value.toString())
    }

    /** MODE_MANUAL only: whether the operator has explicitly asked to be
     * connected. Persisted (not just in-memory) so a PresenceService
     * restart under Manual mode doesn't silently reconnect without a
     * fresh operator action, and doesn't silently forget a still-wanted
     * connection either — it remembers exactly what was last requested. */
    fun manualConnectRequested(): Boolean {
        return store.getString(KEY_MANUAL_CONNECT_REQUESTED)?.toBooleanStrictOrNull() ?: false
    }

    fun setManualConnectRequested(value: Boolean) {
        store.putString(KEY_MANUAL_CONNECT_REQUESTED, value.toString())
    }

    companion object {
        val KNOWN_MODES = setOf(
            ConnectivityPolicy.MODE_ALWAYS,
            ConnectivityPolicy.MODE_WIFI_ONLY,
            ConnectivityPolicy.MODE_MANUAL,
        )
    }
}
