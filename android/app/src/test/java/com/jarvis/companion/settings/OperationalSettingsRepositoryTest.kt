package com.jarvis.companion.settings

import com.jarvis.companion.core.SecureConfigStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.ArgumentMatchers.anyString
import org.mockito.Mockito.doAnswer
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class OperationalSettingsRepositoryTest {

    private val backing = mutableMapOf<String, String>()
    private lateinit var store: SecureConfigStore
    private lateinit var repository: OperationalSettingsRepository

    @Before
    fun setUp() {
        backing.clear()
        store = mock(SecureConfigStore::class.java)
        `when`(store.getString(anyString())).thenAnswer { invocation -> backing[invocation.getArgument(0)] }
        doAnswer { invocation ->
            backing[invocation.getArgument(0)] = invocation.getArgument(1)
            null
        }.`when`(store).putString(anyString(), anyString())
        repository = OperationalSettingsRepository(store)
    }

    @Test
    fun `connectivityMode defaults to always`() {
        assertEquals(ConnectivityPolicy.MODE_ALWAYS, repository.connectivityMode())
    }

    @Test
    fun `setConnectivityMode persists and reads back each known mode`() {
        for (mode in OperationalSettingsRepository.KNOWN_MODES) {
            repository.setConnectivityMode(mode)
            assertEquals(mode, repository.connectivityMode())
        }
    }

    @Test
    fun `connectivityMode falls back to default on an unrecognized stored value`() {
        backing["operational_connectivity_mode"] = "bogus_mode"
        assertEquals(ConnectivityPolicy.MODE_ALWAYS, repository.connectivityMode())
    }

    @Test(expected = IllegalArgumentException::class)
    fun `setConnectivityMode rejects an unrecognized mode`() {
        repository.setConnectivityMode("bogus_mode")
    }

    @Test
    fun `lastSyncedAtMs defaults to null`() {
        assertNull(repository.lastSyncedAtMs())
    }

    @Test
    fun `setLastSyncedAtMs persists and reads back`() {
        repository.setLastSyncedAtMs(1234567890L)
        assertEquals(1234567890L, repository.lastSyncedAtMs())
    }

    @Test
    fun `lastSyncedAtMs falls back to null on malformed stored value`() {
        backing["operational_last_synced_at_ms"] = "not-a-long"
        assertNull(repository.lastSyncedAtMs())
    }

    @Test
    fun `manualConnectRequested defaults to false`() {
        assertFalse(repository.manualConnectRequested())
    }

    @Test
    fun `setManualConnectRequested persists and reads back`() {
        repository.setManualConnectRequested(true)
        assertTrue(repository.manualConnectRequested())
        repository.setManualConnectRequested(false)
        assertFalse(repository.manualConnectRequested())
    }

    @Test
    fun `manualConnectRequested falls back to default on malformed stored value`() {
        backing["operational_manual_connect_requested"] = "yes"
        assertFalse(repository.manualConnectRequested())
    }
}
