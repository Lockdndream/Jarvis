package com.jarvis.companion.settings

/**
 * ADR-023 connectivity-policy enforcement, Android side. This is a pure
 * decision function deliberately kept free of CompanionWebSocketClient/
 * PresenceService references — PresenceService remains the sole owner of
 * the actual start()/stop() calls (no competing state machine); this
 * object only answers "should the client be connected right now", so the
 * decision itself is exhaustively unit-testable without a device, a
 * network callback, or a live WebSocket.
 */
object ConnectivityPolicy {
    const val MODE_ALWAYS = "always"
    const val MODE_WIFI_ONLY = "wifi_only"
    const val MODE_MANUAL = "manual"

    /** Server's own DEFAULT_MODE (app/operations_connectivity.py) — used
     * as the fail-open default so an unsynced or unreachable server never
     * strands the phone disconnected. */
    const val DEFAULT_MODE = MODE_ALWAYS

    /**
     * @param mode one of MODE_ALWAYS/MODE_WIFI_ONLY/MODE_MANUAL. Any other
     *   value (unrecognized, not-yet-synced) fails open to "always",
     *   matching [DEFAULT_MODE].
     * @param wifiAvailable true if the device's current default network
     *   has the Wi-Fi transport. Ignored outside MODE_WIFI_ONLY.
     * @param manualConnectRequested true if the operator has explicitly
     *   asked to be connected while in MODE_MANUAL. Ignored outside
     *   MODE_MANUAL.
     */
    fun shouldBeConnected(mode: String, wifiAvailable: Boolean, manualConnectRequested: Boolean): Boolean {
        return when (mode) {
            MODE_WIFI_ONLY -> wifiAvailable
            MODE_MANUAL -> manualConnectRequested
            else -> true
        }
    }
}
