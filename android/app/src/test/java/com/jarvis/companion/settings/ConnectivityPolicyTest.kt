package com.jarvis.companion.settings

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConnectivityPolicyTest {

    @Test
    fun `always mode stays connected regardless of wifi or manual flag`() {
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_ALWAYS, wifiAvailable = false, manualConnectRequested = false))
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_ALWAYS, wifiAvailable = true, manualConnectRequested = false))
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_ALWAYS, wifiAvailable = false, manualConnectRequested = true))
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_ALWAYS, wifiAvailable = true, manualConnectRequested = true))
    }

    @Test
    fun `wifi_only mode tracks wifi availability regardless of manual flag`() {
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_WIFI_ONLY, wifiAvailable = true, manualConnectRequested = false))
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_WIFI_ONLY, wifiAvailable = true, manualConnectRequested = true))
        assertFalse(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_WIFI_ONLY, wifiAvailable = false, manualConnectRequested = false))
        assertFalse(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_WIFI_ONLY, wifiAvailable = false, manualConnectRequested = true))
    }

    @Test
    fun `manual mode tracks the manual flag regardless of wifi availability`() {
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_MANUAL, wifiAvailable = false, manualConnectRequested = true))
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_MANUAL, wifiAvailable = true, manualConnectRequested = true))
        assertFalse(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_MANUAL, wifiAvailable = false, manualConnectRequested = false))
        assertFalse(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.MODE_MANUAL, wifiAvailable = true, manualConnectRequested = false))
    }

    @Test
    fun `unrecognized mode fails open to connected, same as DEFAULT_MODE`() {
        assertTrue(ConnectivityPolicy.shouldBeConnected("not_a_real_mode", wifiAvailable = false, manualConnectRequested = false))
        assertTrue(ConnectivityPolicy.shouldBeConnected(ConnectivityPolicy.DEFAULT_MODE, wifiAvailable = false, manualConnectRequested = false))
    }
}
