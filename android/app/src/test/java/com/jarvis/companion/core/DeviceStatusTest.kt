package com.jarvis.companion.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class DeviceStatusTest {

    @Test
    fun serializesToTheExactExpectedShape() {
        val status = DeviceStatus(
            deviceId = "cc8342c5-7981-4c8d-b215-104dba061cee",
            capabilities = DeviceCapabilities(),
            pairingState = DeviceStatus.PAIRING_STATE_PAIRED,
            batteryOptimizationExempt = true,
            notificationPermissionGranted = false,
            connectionGeneration = 3,
        )

        val expected = "{" +
            "\"type\":\"device_status\"," +
            "\"device_id\":\"cc8342c5-7981-4c8d-b215-104dba061cee\"," +
            "\"capabilities\":{" +
            "\"voice\":true," +
            "\"wakeword\":false," +
            "\"widget\":false," +
            "\"notifications\":true," +
            "\"foreground_service\":true" +
            "}," +
            "\"pairing_state\":\"paired\"," +
            "\"battery_optimization_exempt\":true," +
            "\"notification_permission_granted\":false," +
            "\"connection_generation\":3" +
            "}"

        assertEquals(expected, status.toJson())
    }

    @Test
    fun neverContainsAnApiTokenOrOtherSecretField() {
        val status = DeviceStatus(
            deviceId = "device-1",
            capabilities = DeviceCapabilities.CURRENT,
            pairingState = DeviceStatus.PAIRING_STATE_PAIRED,
            batteryOptimizationExempt = false,
            notificationPermissionGranted = true,
            connectionGeneration = 1,
        )
        val json = status.toJson()
        assertFalse(json.contains("token", ignoreCase = true))
        assertFalse(json.contains("secret", ignoreCase = true))
    }

    @Test
    fun escapesQuotesAndBackslashesInStringFields() {
        val status = DeviceStatus(
            deviceId = "weird\"id\\with\\escapes",
            capabilities = DeviceCapabilities(),
            pairingState = DeviceStatus.PAIRING_STATE_PAIRED,
            batteryOptimizationExempt = false,
            notificationPermissionGranted = false,
            connectionGeneration = 1,
        )
        val json = status.toJson()
        assertEquals(
            "{\"type\":\"device_status\",\"device_id\":\"weird\\\"id\\\\with\\\\escapes\"," +
                "\"capabilities\":{\"voice\":true,\"wakeword\":false,\"widget\":false," +
                "\"notifications\":true,\"foreground_service\":true},\"pairing_state\":\"paired\"," +
                "\"battery_optimization_exempt\":false,\"notification_permission_granted\":false," +
                "\"connection_generation\":1}",
            json,
        )
    }
}
