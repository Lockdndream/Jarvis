package com.jarvis.companion.ui

import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.voice.VoiceSessionState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceActivityTest {

    // --- voiceSessionStateToUserFacingLabel ---

    @Test
    fun `null maps to finished`() {
        assertEquals("finished", voiceSessionStateToUserFacingLabel(null))
    }

    @Test
    fun `listening maps to listening`() {
        assertEquals("listening", voiceSessionStateToUserFacingLabel(VoiceSessionState.LISTENING))
    }

    @Test
    fun `speaking maps to speaking`() {
        assertEquals("speaking", voiceSessionStateToUserFacingLabel(VoiceSessionState.SPEAKING))
    }

    @Test
    fun `waiting maps to waiting`() {
        assertEquals("waiting", voiceSessionStateToUserFacingLabel(VoiceSessionState.WAITING))
    }

    @Test
    fun `closed maps to finished`() {
        assertEquals("finished", voiceSessionStateToUserFacingLabel(VoiceSessionState.CLOSED))
    }

    @Test
    fun `failed maps to error`() {
        assertEquals("error", voiceSessionStateToUserFacingLabel(VoiceSessionState.FAILED))
    }

    @Test
    fun `opening maps to waiting`() {
        assertEquals("waiting", voiceSessionStateToUserFacingLabel(VoiceSessionState.OPENING))
    }

    @Test
    fun `processing maps to waiting`() {
        assertEquals("waiting", voiceSessionStateToUserFacingLabel(VoiceSessionState.PROCESSING))
    }

    @Test
    fun `deferred maps to waiting`() {
        assertEquals("waiting", voiceSessionStateToUserFacingLabel(VoiceSessionState.DEFERRED))
    }

    @Test
    fun `closing maps to waiting`() {
        assertEquals("waiting", voiceSessionStateToUserFacingLabel(VoiceSessionState.CLOSING))
    }

    @Test
    fun `unknown server state maps to waiting`() {
        assertEquals("waiting", voiceSessionStateToUserFacingLabel("garbage_state"))
    }

    @Test
    fun `confirming maps to confirming`() {
        assertEquals("confirming", voiceSessionStateToUserFacingLabel(VoiceSessionState.CONFIRMING))
    }

    // --- shouldAutoResumeListening ---

    @Test
    fun `resumes on the true-to-false speaking edge while listening and connected`() {
        assertTrue(
            shouldAutoResumeListening(
                wasSpeaking = true,
                isSpeaking = false,
                sessionState = VoiceSessionState.LISTENING,
                hasUserFacingError = false,
                connectionState = ConnectionState.CONNECTED,
            )
        )
    }

    @Test
    fun `resumes while confirming too -- a Goal 5 yes-no reply is also hands-free`() {
        assertTrue(
            shouldAutoResumeListening(
                wasSpeaking = true,
                isSpeaking = false,
                sessionState = VoiceSessionState.CONFIRMING,
                hasUserFacingError = false,
                connectionState = ConnectionState.CONNECTED,
            )
        )
    }

    @Test
    fun `does not resume while still speaking`() {
        assertFalse(
            shouldAutoResumeListening(
                wasSpeaking = true,
                isSpeaking = true,
                sessionState = VoiceSessionState.LISTENING,
                hasUserFacingError = false,
                connectionState = ConnectionState.CONNECTED,
            )
        )
    }

    @Test
    fun `does not resume when it was never speaking (no edge)`() {
        assertFalse(
            shouldAutoResumeListening(
                wasSpeaking = false,
                isSpeaking = false,
                sessionState = VoiceSessionState.LISTENING,
                hasUserFacingError = false,
                connectionState = ConnectionState.CONNECTED,
            )
        )
    }

    @Test
    fun `does not resume when session state is not listening`() {
        for (state in listOf(
            VoiceSessionState.WAITING, VoiceSessionState.PROCESSING, VoiceSessionState.CLOSING,
            VoiceSessionState.CLOSED, VoiceSessionState.FAILED, VoiceSessionState.DEFERRED, null,
        )) {
            assertFalse(
                "state=$state should not resume",
                shouldAutoResumeListening(
                    wasSpeaking = true,
                    isSpeaking = false,
                    sessionState = state,
                    hasUserFacingError = false,
                    connectionState = ConnectionState.CONNECTED,
                )
            )
        }
    }

    @Test
    fun `does not resume when a user-facing error is showing`() {
        assertFalse(
            shouldAutoResumeListening(
                wasSpeaking = true,
                isSpeaking = false,
                sessionState = VoiceSessionState.LISTENING,
                hasUserFacingError = true,
                connectionState = ConnectionState.CONNECTED,
            )
        )
    }

    @Test
    fun `does not resume when not connected`() {
        for (state in listOf(
            ConnectionState.CONNECTING, ConnectionState.RECONNECTING,
            ConnectionState.DISCONNECTED, ConnectionState.FAILED_PERMANENT,
        )) {
            assertFalse(
                "connectionState=$state should not resume",
                shouldAutoResumeListening(
                    wasSpeaking = true,
                    isSpeaking = false,
                    sessionState = VoiceSessionState.LISTENING,
                    hasUserFacingError = false,
                    connectionState = state,
                )
            )
        }
    }
}