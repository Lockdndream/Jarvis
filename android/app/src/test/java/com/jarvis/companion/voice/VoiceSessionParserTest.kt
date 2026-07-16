package com.jarvis.companion.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceSessionParserTest {

    @Test
    fun `parseOpened with all fields parses correctly`() {
        val json = """
        {
          "type": "voice_session_opened",
          "voice_session_id": "vs_abc123",
          "state": "listening",
          "conversation_id": "conv_456",
          "attention_request_id": "ar_789",
          "greeting": "Hello, how can I help?"
        }
        """.trimIndent()
        val result = VoiceSessionParser.parseOpened(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("vs_abc123", voiceSessionId)
            assertEquals("listening", state)
            assertEquals("conv_456", conversationId)
            assertEquals("ar_789", attentionRequestId)
            assertEquals("Hello, how can I help?", greeting)
        }
    }

    @Test
    fun `parseOpened with nullables as null`() {
        val json = """
        {
          "type": "voice_session_opened",
          "voice_session_id": "vs_def",
          "state": "opening",
          "conversation_id": "conv_1",
          "attention_request_id": null,
          "greeting": null
        }
        """.trimIndent()
        val result = VoiceSessionParser.parseOpened(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("vs_def", voiceSessionId)
            assertEquals("opening", state)
            assertEquals("conv_1", conversationId)
            assertNull(attentionRequestId)
            assertNull(greeting)
        }
    }

    @Test
    fun `parseOpened missing voice_session_id returns null`() {
        val json = """{"type": "voice_session_opened", "state": "listening"}"""
        assertNull(VoiceSessionParser.parseOpened(json))
    }

    @Test
    fun `parseOpened missing state returns null`() {
        val json = """{"type": "voice_session_opened", "voice_session_id": "vs_1"}"""
        assertNull(VoiceSessionParser.parseOpened(json))
    }

    @Test
    fun `parseOpened wrong type returns null`() {
        val json = """{"type": "user_message", "voice_session_id": "vs_1", "state": "listening"}"""
        assertNull(VoiceSessionParser.parseOpened(json))
    }

    @Test
    fun `parseOpened garbage json returns null`() {
        assertNull(VoiceSessionParser.parseOpened("not json"))
    }

    @Test
    fun `parseOpened empty string returns null`() {
        assertNull(VoiceSessionParser.parseOpened(""))
    }

    @Test
    fun `parseResponse parses correctly`() {
        val json = """
        {
          "type": "voice_session_response",
          "voice_session_id": "vs_abc",
          "response": "The result is 42.",
          "conversation_id": "conv_1",
          "attention_request_id": "ar_2",
          "voice_session_state": "listening"
        }
        """.trimIndent()
        val result = VoiceSessionParser.parseResponse(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("vs_abc", voiceSessionId)
            assertEquals("The result is 42.", response)
            assertEquals("conv_1", conversationId)
            assertEquals("ar_2", attentionRequestId)
            assertEquals("listening", voiceSessionState)
        }
    }

    @Test
    fun `parseResponse with null attention and state`() {
        val json = """
        {
          "type": "voice_session_response",
          "voice_session_id": "vs_def",
          "response": "Understood.",
          "conversation_id": "conv_3",
          "attention_request_id": null,
          "voice_session_state": null
        }
        """.trimIndent()
        val result = VoiceSessionParser.parseResponse(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("vs_def", voiceSessionId)
            assertEquals("Understood.", response)
            assertEquals("conv_3", conversationId)
            assertNull(attentionRequestId)
            assertNull(voiceSessionState)
        }
    }

    @Test
    fun `parseResponse missing voice_session_id returns null`() {
        val json = """{"type": "voice_session_response", "response": "hi"}"""
        assertNull(VoiceSessionParser.parseResponse(json))
    }

    @Test
    fun `parseResponse wrong type returns null`() {
        val json = """{"type": "voice_session_opened", "voice_session_id": "vs_1", "response": "hi"}"""
        assertNull(VoiceSessionParser.parseResponse(json))
    }

    @Test
    fun `parseResponse garbage json returns null`() {
        assertNull(VoiceSessionParser.parseResponse("not json"))
    }

    @Test
    fun `parseError with session id parses correctly`() {
        val json = """
        {
          "type": "voice_session_error",
          "voice_session_id": "vs_abc",
          "error": "Session timed out"
        }
        """.trimIndent()
        val result = VoiceSessionParser.parseError(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("vs_abc", voiceSessionId)
            assertEquals("Session timed out", error)
        }
    }

    @Test
    fun `parseError with null session id parses correctly`() {
        val json = """
        {
          "type": "voice_session_error",
          "voice_session_id": null,
          "error": "AttentionRequest ar_1 already has an active voice session"
        }
        """.trimIndent()
        val result = VoiceSessionParser.parseError(json)
        assertNotNull(result)
        with(result!!) {
            assertNull(voiceSessionId)
            assertEquals("AttentionRequest ar_1 already has an active voice session", error)
        }
    }

    @Test
    fun `parseError missing error returns null`() {
        val json = """{"type": "voice_session_error", "voice_session_id": "vs_1"}"""
        assertNull(VoiceSessionParser.parseError(json))
    }

    @Test
    fun `parseError wrong type returns null`() {
        val json = """{"type": "voice_session_closed", "error": "something"}"""
        assertNull(VoiceSessionParser.parseError(json))
    }

    @Test
    fun `parseError garbage json returns null`() {
        assertNull(VoiceSessionParser.parseError("not json"))
    }

    @Test
    fun `parseClosed with session id`() {
        val json = """{"type": "voice_session_closed", "voice_session_id": "vs_abc"}"""
        val result = VoiceSessionParser.parseClosed(json)
        assertNotNull(result)
        assertEquals("vs_abc", result!!.voiceSessionId)
    }

    @Test
    fun `parseClosed with null session id`() {
        val json = """{"type": "voice_session_closed", "voice_session_id": null}"""
        val result = VoiceSessionParser.parseClosed(json)
        assertNotNull(result)
        assertNull(result!!.voiceSessionId)
    }

    @Test
    fun `parseClosed wrong type returns null`() {
        val json = """{"type": "voice_session_error", "voice_session_id": "vs_1"}"""
        assertNull(VoiceSessionParser.parseClosed(json))
    }

    @Test
    fun `parseClosed garbage json returns null`() {
        assertNull(VoiceSessionParser.parseClosed("not json"))
    }

    @Test
    fun `parseInvitation parses correctly`() {
        val json = """
        {
          "type": "voice_session_invitation",
          "attention_request_id": "ar_xyz",
          "summary": "Need your input on a question",
          "task_id": "oc_task1",
          "attention_type": "QUESTION"
        }
        """.trimIndent()
        val result = VoiceSessionParser.parseInvitation(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("ar_xyz", attentionRequestId)
            assertEquals("Need your input on a question", summary)
            assertEquals("oc_task1", taskId)
            assertEquals("QUESTION", attentionType)
        }
    }

    @Test
    fun `parseInvitation with null summary and taskId`() {
        val json = """
        {
          "type": "voice_session_invitation",
          "attention_request_id": "ar_abc",
          "summary": null,
          "task_id": null,
          "attention_type": "PERMISSION"
        }
        """.trimIndent()
        val result = VoiceSessionParser.parseInvitation(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("ar_abc", attentionRequestId)
            assertNull(summary)
            assertNull(taskId)
            assertEquals("PERMISSION", attentionType)
        }
    }

    @Test
    fun `parseInvitation missing attention_request_id returns null`() {
        val json = """{"type": "voice_session_invitation", "attention_type": "QUESTION"}"""
        assertNull(VoiceSessionParser.parseInvitation(json))
    }

    @Test
    fun `parseInvitation missing attention_type returns null`() {
        val json = """{"type": "voice_session_invitation", "attention_request_id": "ar_1"}"""
        assertNull(VoiceSessionParser.parseInvitation(json))
    }

    @Test
    fun `parseInvitation wrong type returns null`() {
        val json = """{"type": "voice_session_opened", "attention_request_id": "ar_1", "attention_type": "Q"}"""
        assertNull(VoiceSessionParser.parseInvitation(json))
    }

    @Test
    fun `parseInvitation garbage json returns null`() {
        assertNull(VoiceSessionParser.parseInvitation("not json"))
    }

    @Test
    fun `isVoiceSessionEventType identifies all 5 types`() {
        val types = setOf(
            "voice_session_opened", "voice_session_response",
            "voice_session_error", "voice_session_closed",
            "voice_session_invitation",
        )
        for (t in types) {
            assertTrue("$t should be recognized", VoiceSessionParser.isVoiceSessionEventType(t))
        }
    }

    @Test
    fun `isVoiceSessionEventType rejects unrecognized string`() {
        assertTrue(!VoiceSessionParser.isVoiceSessionEventType("user_message"))
    }
}