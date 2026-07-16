package com.jarvis.companion.ui

import com.jarvis.companion.voice.VoiceSessionState
import org.junit.Assert.assertEquals
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
}