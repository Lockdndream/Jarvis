package com.jarvis.companion.voice

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class VoiceSessionRepositoryTest {

    private lateinit var repository: VoiceSessionRepository

    @Before
    fun setUp() {
        repository = VoiceSessionRepository()
    }

    @Test
    fun `initial state is null`() {
        assertNull(repository.current.value)
        assertNull(repository.lastResponse.value)
    }

    @Test
    fun `applyOpened sets current session`() {
        val session = VoiceSession("vs_1", "listening", "conv_1", null, null)
        repository.applyOpened(session)
        assertEquals(session, repository.current.value)
    }

    @Test
    fun `applyOpened replaces existing session`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        val session2 = VoiceSession("vs_2", "listening", "conv_2", null, null)
        repository.applyOpened(session2)
        assertEquals(session2, repository.current.value)
    }

    @Test
    fun `applyOpened clears a leftover lastResponse from a previous session`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyResponse(VoiceSessionResponse("vs_1", "Old response", "conv_1", null, "listening"))
        assertEquals("Old response", repository.lastResponse.value)

        repository.applyOpened(VoiceSession("vs_2", "listening", "conv_2", null, "New session greeting"))

        assertNull(repository.lastResponse.value)
    }

    @Test
    fun `applyResponse updates matching session state`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        val response = VoiceSessionResponse("vs_1", "Hello!", "conv_1", null, "speaking")
        repository.applyResponse(response)
        assertEquals("speaking", repository.current.value?.state)
        assertEquals("Hello!", repository.lastResponse.value)
    }

    @Test
    fun `applyResponse with stale session id ignores`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        val response = VoiceSessionResponse("vs_2", "Hello!", "conv_2", null, "speaking")
        repository.applyResponse(response)
        assertEquals("listening", repository.current.value?.state)
        assertNull(repository.lastResponse.value)
    }

    @Test
    fun `applyResponse when current is null ignores`() {
        val response = VoiceSessionResponse("vs_1", "Hello!", "conv_1", null, "speaking")
        repository.applyResponse(response)
        assertNull(repository.current.value)
        assertNull(repository.lastResponse.value)
    }

    @Test
    fun `applyResponse with null state preserves existing state`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        val response = VoiceSessionResponse("vs_1", "Hello!", "conv_1", null, null)
        repository.applyResponse(response)
        assertEquals("listening", repository.current.value?.state)
        assertEquals("Hello!", repository.lastResponse.value)
    }

    @Test
    fun `applyClosed clears current session`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyClosed("vs_1")
        assertNull(repository.current.value)
    }

    @Test
    fun `applyClosed when already null is idempotent`() {
        repository.applyClosed("vs_1")
        assertNull(repository.current.value)
    }

    @Test
    fun `applyError clears current session`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyError("vs_1")
        assertNull(repository.current.value)
    }

    @Test
    fun `applyError when already null is idempotent`() {
        repository.applyError("vs_1")
        assertNull(repository.current.value)
    }

    @Test
    fun `applyInvitation never mutates current`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyInvitation()
        assertEquals("vs_1", repository.current.value?.voiceSessionId)
        assertEquals("listening", repository.current.value?.state)
    }

    @Test
    fun `applyInvitation when null stays null`() {
        repository.applyInvitation()
        assertNull(repository.current.value)
    }

    @Test
    fun `clear resets both current and lastResponse`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyResponse(VoiceSessionResponse("vs_1", "Hi", "conv_1", null, "speaking"))
        assertNotNull(repository.current.value)
        assertNotNull(repository.lastResponse.value)

        repository.clear()
        assertNull(repository.current.value)
        assertNull(repository.lastResponse.value)
    }

    @Test
    fun `voice_session_closed clears current but not lastResponse`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyResponse(VoiceSessionResponse("vs_1", "Hi", "conv_1", null, "speaking"))
        repository.applyClosed("vs_1")
        assertNull(repository.current.value)
        assertEquals("Hi", repository.lastResponse.value)
    }

    @Test
    fun `voice_session_error clears current but not lastResponse`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyResponse(VoiceSessionResponse("vs_1", "Hi", "conv_1", null, "speaking"))
        repository.applyError("vs_1")
        assertNull(repository.current.value)
        assertEquals("Hi", repository.lastResponse.value)
    }

    @Test
    fun `applyClosed with a stale session id never clears a newer session`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyOpened(VoiceSession("vs_2", "listening", "conv_2", null, null))

        // A delayed close for the already-superseded vs_1 arrives late.
        repository.applyClosed("vs_1")

        assertEquals("vs_2", repository.current.value?.voiceSessionId)
    }

    @Test
    fun `applyError with a stale session id never clears a newer session`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyOpened(VoiceSession("vs_2", "listening", "conv_2", null, null))

        repository.applyError("vs_1")

        assertEquals("vs_2", repository.current.value?.voiceSessionId)
    }

    @Test
    fun `applyClosed with a null session id clears unconditionally`() {
        repository.applyOpened(VoiceSession("vs_1", "listening", "conv_1", null, null))
        repository.applyClosed(null)
        assertNull(repository.current.value)
    }

    @Test
    fun `applyOpened with clientRequestId emits Opened outcome`() = runTest {
        val session = VoiceSession("vs_1", "listening", "conv_1", null, null, clientRequestId = "req-1")
        val outcomes = mutableListOf<VoiceSessionOpenOutcome>()
        val job = launch { repository.openOutcomes.collect { outcomes.add(it) } }
        advanceUntilIdle()
        repository.applyOpened(session)
        advanceUntilIdle()
        job.cancel()

        assertEquals(1, outcomes.size)
        val outcome = outcomes[0] as VoiceSessionOpenOutcome.Opened
        assertEquals("req-1", outcome.clientRequestId)
        assertEquals(session, outcome.session)
    }

    @Test
    fun `applyOpened without clientRequestId emits no outcome`() = runTest {
        val session = VoiceSession("vs_1", "listening", "conv_1", null, null, clientRequestId = null)
        val outcomes = mutableListOf<VoiceSessionOpenOutcome>()
        val job = launch { repository.openOutcomes.collect { outcomes.add(it) } }
        advanceUntilIdle()
        repository.applyOpened(session)
        advanceUntilIdle()
        job.cancel()

        assertEquals(0, outcomes.size)
    }

    @Test
    fun `applyOpenError emits Failed outcome with matching clientRequestId`() = runTest {
        val error = VoiceSessionError(voiceSessionId = null, error = "lease held", clientRequestId = "req-2")
        val outcomes = mutableListOf<VoiceSessionOpenOutcome>()
        val job = launch { repository.openOutcomes.collect { outcomes.add(it) } }
        advanceUntilIdle()
        repository.applyOpenError(error)
        advanceUntilIdle()
        job.cancel()

        assertEquals(1, outcomes.size)
        val outcome = outcomes[0] as VoiceSessionOpenOutcome.Failed
        assertEquals("req-2", outcome.clientRequestId)
        assertEquals("lease held", outcome.error)
    }
}