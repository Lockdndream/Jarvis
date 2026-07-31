package com.jarvis.companion.conversation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationParserTest {

    @Test
    fun `parseConversationTurn parses user message`() {
        val json = """
        {
          "type": "conversation_turn",
          "role": "user",
          "content": "What is the weather?",
          "voice_session_id": "vs_abc",
          "conversation_id": "conv_123",
          "timestamp": "2026-07-31T12:34:56.789Z"
        }
        """.trimIndent()
        val result = ConversationParser.parseConversationTurn(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals(ConversationMessage.Type.USER_MESSAGE, type)
            assertEquals("What is the weather?", content)
            assertEquals("vs_abc", metadata?.get("voice_session_id"))
            assertEquals("conv_123", metadata?.get("conversation_id"))
            assertEquals(1785501296789L, timestamp)
        }
    }

    @Test
    fun `parseConversationTurn with explicit null conversation_id does not stringify null`() {
        // Regression test for the org.json optString/null bug (Milestone 9B.10):
        // an explicit JSON null must be treated as absent, not as the literal
        // string "null".
        val json = """
        {
          "type": "conversation_turn",
          "role": "user",
          "content": "Hello",
          "voice_session_id": "vs_abc",
          "conversation_id": null,
          "timestamp": "2026-07-31T12:34:56.789Z"
        }
        """.trimIndent()
        val result = ConversationParser.parseConversationTurn(json)
        assertNotNull(result)
        assertNull(result!!.metadata?.get("conversation_id"))
    }

    @Test
    fun `parseConversationTurn missing content returns null`() {
        val json = """{"type":"conversation_turn","voice_session_id":"vs_abc"}"""
        assertNull(ConversationParser.parseConversationTurn(json))
    }

    @Test
    fun `parseConversationTurn missing voice_session_id returns null`() {
        val json = """{"type":"conversation_turn","content":"Hello"}"""
        assertNull(ConversationParser.parseConversationTurn(json))
    }

    @Test
    fun `parseThinkingUpdate started parses correctly`() {
        val json = """
        {
          "type": "thinking_update",
          "action": "tool_call",
          "status": "started",
          "summary": "Looking up calendar",
          "detail": null,
          "conversation_id": "conv_123",
          "trace_id": "trace_abc",
          "timestamp": "2026-07-31T12:34:56.789Z"
        }
        """.trimIndent()
        val result = ConversationParser.parseThinkingUpdate(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals(ConversationMessage.Type.THINKING, type)
            assertEquals("Looking up calendar", content)
            assertNull(detail)
            assertEquals(ConversationMessage.Status.STARTED, status)
            assertEquals("conv_123", metadata?.get("conversation_id"))
            assertEquals("trace_abc", metadata?.get("trace_id"))
        }
    }

    @Test
    fun `parseThinkingUpdate completed parses detail`() {
        val json = """
        {
          "type": "thinking_update",
          "action": "tool_call",
          "status": "completed",
          "summary": "Found 3 events",
          "detail": "Retrieved events from work calendar",
          "conversation_id": "conv_123",
          "trace_id": "trace_abc",
          "timestamp": "2026-07-31T12:34:56.789Z"
        }
        """.trimIndent()
        val result = ConversationParser.parseThinkingUpdate(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals(ConversationMessage.Status.COMPLETED, status)
            assertEquals("Found 3 events", content)
            assertEquals("Retrieved events from work calendar", detail)
        }
    }

    @Test
    fun `parseThinkingUpdate unknown status returns null`() {
        val json = """
        {"type":"thinking_update","action":"tool_call","status":"running","summary":"x"}
        """.trimIndent()
        assertNull(ConversationParser.parseThinkingUpdate(json))
    }

    @Test
    fun `parsePermissionResponseAck parses system event`() {
        val json = """
        {
          "type": "permission_response_ack",
          "attention_request_id": "ar_123",
          "response": "yes",
          "conversation_id": "conv_123"
        }
        """.trimIndent()
        val result = ConversationParser.parsePermissionResponseAck(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals(ConversationMessage.Type.SYSTEM_EVENT, type)
            assertEquals("Permission response: yes", content)
            assertEquals("ar_123", permissionId)
            assertEquals("conv_123", metadata?.get("conversation_id"))
        }
    }

    @Test
    fun `parsePermissionResponseAck missing attention_request_id returns null`() {
        val json = """{"type":"permission_response_ack","response":"yes"}"""
        assertNull(ConversationParser.parsePermissionResponseAck(json))
    }

    @Test
    fun `parsePermissionResponseAck missing response returns null`() {
        val json = """{"type":"permission_response_ack","attention_request_id":"ar_123"}"""
        assertNull(ConversationParser.parsePermissionResponseAck(json))
    }

    @Test
    fun `malformed json returns null rather than throwing`() {
        assertNull(ConversationParser.parseConversationTurn("not json"))
        assertNull(ConversationParser.parseThinkingUpdate("not json"))
        assertNull(ConversationParser.parsePermissionResponseAck("not json"))
    }

    @Test
    fun `isConversationEventType recognizes only conversation types`() {
        assertTrue(ConversationParser.isConversationEventType("conversation_turn"))
        assertTrue(ConversationParser.isConversationEventType("thinking_update"))
        assertTrue(ConversationParser.isConversationEventType("permission_response_ack"))
        assertFalse(ConversationParser.isConversationEventType("voice_session_response"))
        assertFalse(ConversationParser.isConversationEventType("attention_created"))
        assertFalse(ConversationParser.isConversationEventType("opencode_task_created"))
    }
}
