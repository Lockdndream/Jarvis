package com.jarvis.companion.core

/**
 * The state-synchronization message sent once per successful (re)connect
 * (Milestone 9B.2 — see ADR-012). Never carries the API token or any other
 * secret; every field here is safe to log and to show on the Diagnostics
 * screen.
 *
 * Hand-built JSON (like the existing heartbeat frame) rather than
 * org.json.JSONObject: every field is a boolean/int/enum-backed string
 * except [deviceId], which is always a UUID (core.DeviceIdentity) — no
 * field here actually needs general string escaping, and avoiding
 * org.json sidesteps that class being Android-stubbed (not the real
 * implementation) in local JVM unit tests.
 */
data class DeviceStatus(
    val deviceId: String,
    val capabilities: DeviceCapabilities,
    val pairingState: String,
    val batteryOptimizationExempt: Boolean,
    val notificationPermissionGranted: Boolean,
    val connectionGeneration: Int,
) {
    fun toJson(): String {
        val id = jsonEscape(deviceId)
        val pairing = jsonEscape(pairingState)
        return "{" +
            "\"type\":\"device_status\"," +
            "\"device_id\":\"$id\"," +
            "\"capabilities\":{" +
            "\"voice\":${capabilities.voice}," +
            "\"wakeword\":${capabilities.wakeword}," +
            "\"widget\":${capabilities.widget}," +
            "\"notifications\":${capabilities.notifications}," +
            "\"foreground_service\":${capabilities.foregroundService}" +
            "}," +
            "\"pairing_state\":\"$pairing\"," +
            "\"battery_optimization_exempt\":$batteryOptimizationExempt," +
            "\"notification_permission_granted\":$notificationPermissionGranted," +
            "\"connection_generation\":$connectionGeneration" +
            "}"
    }

    companion object {
        const val PAIRING_STATE_PAIRED = "paired"

        private fun jsonEscape(value: String): String =
            value.replace("\\", "\\\\").replace("\"", "\\\"")
    }
}
