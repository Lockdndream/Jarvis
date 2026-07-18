package com.jarvis.companion.wakeword

import com.jarvis.companion.core.SecureConfigStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class WakeWordConfigRepositoryTest {

    private val backing = mutableMapOf<String, String>()
    private lateinit var store: SecureConfigStore
    private lateinit var repository: WakeWordConfigRepository

    @Before
    fun setUp() {
        backing.clear()
        store = mock(SecureConfigStore::class.java)
        `when`(store.getString(org.mockito.ArgumentMatchers.anyString())).thenAnswer { invocation ->
            backing[invocation.getArgument(0)]
        }
        org.mockito.Mockito.doAnswer { invocation ->
            backing[invocation.getArgument(0)] = invocation.getArgument(1)
            null
        }.`when`(store).putString(org.mockito.ArgumentMatchers.anyString(), org.mockito.ArgumentMatchers.anyString())
        repository = WakeWordConfigRepository(store)
    }

    @Test
    fun `isEnabled defaults to false`() {
        assertFalse(repository.isEnabled())
    }

    @Test
    fun `setEnabled persists and reads back`() {
        repository.setEnabled(true)
        assertTrue(repository.isEnabled())
        repository.setEnabled(false)
        assertFalse(repository.isEnabled())
    }

    @Test
    fun `confidenceThreshold defaults to 0_97`() {
        assertEquals(0.97f, repository.confidenceThreshold(), 0.0001f)
    }

    @Test
    fun `setConfidenceThreshold persists and reads back`() {
        repository.setConfidenceThreshold(0.85f)
        assertEquals(0.85f, repository.confidenceThreshold(), 0.0001f)
    }

    @Test
    fun `modelVersion defaults to hey_jarvis-v1`() {
        assertEquals("hey_jarvis-v1", repository.modelVersion())
    }

    @Test
    fun `diagnosticModeEnabled defaults to false`() {
        assertFalse(repository.diagnosticModeEnabled())
    }

    @Test
    fun `setDiagnosticModeEnabled persists and reads back`() {
        repository.setDiagnosticModeEnabled(true)
        assertTrue(repository.diagnosticModeEnabled())
        repository.setDiagnosticModeEnabled(false)
        assertFalse(repository.diagnosticModeEnabled())
    }

    @Test
    fun `isEnabled falls back to default on malformed stored value`() {
        backing["wakeword_enabled"] = "yes"
        assertFalse(repository.isEnabled())
    }

    @Test
    fun `confidenceThreshold falls back to default on malformed stored value`() {
        backing["wakeword_confidence_threshold"] = "not-a-float"
        assertEquals(0.97f, repository.confidenceThreshold(), 0.0001f)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `setConfidenceThreshold rejects NaN`() {
        repository.setConfidenceThreshold(Float.NaN)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `setConfidenceThreshold rejects infinity`() {
        repository.setConfidenceThreshold(Float.POSITIVE_INFINITY)
    }

    @Test
    fun `handoffConfirmTimeoutMs defaults to 5000`() {
        assertEquals(5000L, repository.handoffConfirmTimeoutMs())
    }

    @Test
    fun `setHandoffConfirmTimeoutMs persists and reads back`() {
        repository.setHandoffConfirmTimeoutMs(8000L)
        assertEquals(8000L, repository.handoffConfirmTimeoutMs())
    }

    @Test
    fun `handoffConfirmTimeoutMs falls back to default on malformed stored value`() {
        backing["wakeword_handoff_confirm_timeout_ms"] = "not-a-long"
        assertEquals(5000L, repository.handoffConfirmTimeoutMs())
    }

    @Test(expected = IllegalArgumentException::class)
    fun `setHandoffConfirmTimeoutMs rejects zero`() {
        repository.setHandoffConfirmTimeoutMs(0L)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `setHandoffConfirmTimeoutMs rejects negative`() {
        repository.setHandoffConfirmTimeoutMs(-1L)
    }
}
