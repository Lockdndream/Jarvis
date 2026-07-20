package com.jarvis.companion.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceDiagnosticsTest {

    @Test
    fun `formatted with all fields present`() {
        val voice = VoiceDiagnostics(
            audioFocusState = "GAINED",
            audioRoute = "SPEAKER",
            ttsReady = true,
            ttsSpeaking = false,
            voiceSessionState = "listening",
            voiceSessionId = "session-001",
            playbackQueueDepth = 3,
            speechInputState = "IDLE",
            lastVoiceEventAgoMs = 5000,
            lastTerminationReason = "idle_timeout",
        )
        val result = voice.formatted()
        val lines = result.split("\n")
        assertEquals("audio_focus_state=GAINED", lines[0])
        assertEquals("audio_route=SPEAKER", lines[1])
        assertEquals("tts_ready=true", lines[2])
        assertEquals("tts_speaking=false", lines[3])
        assertEquals("voice_session_state=listening", lines[4])
        assertEquals("voice_session_id=session-001", lines[5])
        assertEquals("playback_queue_depth=3", lines[6])
        assertEquals("speech_input_state=IDLE", lines[7])
        assertEquals("last_termination_reason=idle_timeout", lines[8])
        assertEquals("last_voice_event_ago=5s", lines[9])
    }

    @Test
    fun `formatted with all fields null shows n-a throughout`() {
        val voice = VoiceDiagnostics(
            audioFocusState = null,
            audioRoute = null,
            ttsReady = null,
            ttsSpeaking = null,
            voiceSessionState = null,
            voiceSessionId = null,
            playbackQueueDepth = null,
            speechInputState = null,
            lastVoiceEventAgoMs = null,
            lastTerminationReason = null,
        )
        val result = voice.formatted()
        val lines = result.split("\n")
        assertEquals(10, lines.size)
        lines.forEachIndexed { index, line ->
            val value = line.substringAfter("=")
            assertEquals("line $index should be n/a", "n/a", value)
        }
    }

    @Test
    fun `buildVoiceDiagnostics converts absolute timestamp to relative ago`() {
        val now = System.currentTimeMillis()
        val fiveSecondsAgo = now - 5000
        val voice = buildVoiceDiagnostics(
            audioFocusState = "GAINED",
            audioRoute = "WIRED_HEADSET",
            ttsReady = true,
            ttsSpeaking = true,
            voiceSessionState = "speaking",
            voiceSessionId = "session-002",
            playbackQueueDepth = 1,
            speechInputState = "LISTENING",
            lastVoiceEventAtMs = fiveSecondsAgo,
        )
        assertEquals("GAINED", voice.audioFocusState)
        assertEquals("WIRED_HEADSET", voice.audioRoute)
        assertEquals(true, voice.ttsReady)
        assertEquals(true, voice.ttsSpeaking)
        assertEquals("speaking", voice.voiceSessionState)
        assertEquals("session-002", voice.voiceSessionId)
        assertEquals(1, voice.playbackQueueDepth)
        assertEquals("LISTENING", voice.speechInputState)
        assertTrue(
            "lastVoiceEventAgoMs should be ~5000, was ${voice.lastVoiceEventAgoMs}",
            voice.lastVoiceEventAgoMs != null && voice.lastVoiceEventAgoMs in (4900L..5100L),
        )
    }

    @Test
    fun `buildVoiceDiagnostics with null timestamp produces null ago`() {
        val voice = buildVoiceDiagnostics(
            audioFocusState = null,
            audioRoute = null,
            ttsReady = null,
            ttsSpeaking = null,
            voiceSessionState = null,
            voiceSessionId = null,
            playbackQueueDepth = null,
            speechInputState = null,
            lastVoiceEventAtMs = null,
        )
        assertEquals(null, voice.audioFocusState)
        assertEquals(null, voice.audioRoute)
        assertEquals(null, voice.ttsReady)
        assertEquals(null, voice.ttsSpeaking)
        assertEquals(null, voice.voiceSessionState)
        assertEquals(null, voice.voiceSessionId)
        assertEquals(null, voice.playbackQueueDepth)
        assertEquals(null, voice.speechInputState)
        assertEquals(null, voice.lastVoiceEventAgoMs)
    }

    @Test
    fun `buildVoiceDiagnostics passes through the termination reason`() {
        val voice = buildVoiceDiagnostics(
            audioFocusState = null,
            audioRoute = null,
            ttsReady = null,
            ttsSpeaking = null,
            voiceSessionState = null,
            voiceSessionId = null,
            playbackQueueDepth = null,
            speechInputState = null,
            lastVoiceEventAtMs = null,
            lastTerminationReason = "client_requested",
        )
        assertEquals("client_requested", voice.lastTerminationReason)
    }
}