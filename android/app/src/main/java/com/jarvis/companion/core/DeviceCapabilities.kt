package com.jarvis.companion.core

/**
 * What this build of the companion can do — reported to the server so a
 * future multi-device-routing decision can tell devices apart without the
 * server needing to hardcode per-platform assumptions. Milestone 9B.2:
 * every capability below is fixed at build time (no voice/wakeword/widget
 * exist yet); a later milestone that actually implements one of these
 * flips its constant, it does not add new wire-protocol fields.
 */
data class DeviceCapabilities(
    val voice: Boolean = false,
    val wakeword: Boolean = false,
    val widget: Boolean = false,
    val notifications: Boolean = true,
    val foregroundService: Boolean = true,
) {
    companion object {
        /** This build's actual capability set — one source of truth,
         * not re-declared at each call site. */
        val CURRENT = DeviceCapabilities()
    }
}
