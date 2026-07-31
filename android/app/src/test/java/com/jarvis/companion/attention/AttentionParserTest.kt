package com.jarvis.companion.attention

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AttentionParserTest {

    @Test
    fun `parsePendingAttention with 2 items parses all fields`() {
        val json = """
        {
          "type": "pending_attention",
          "attention_requests": [
            {
              "attention_request_id": "ar_abc123",
              "attention_type": "QUESTION",
              "status": "pending",
              "summary": "Which approach should I take?",
              "task_id": "oc_xyz789",
              "conversation_id": "conv_456",
              "urgency": "normal",
              "deferred_until": null
            },
            {
              "attention_request_id": "ar_def456",
              "attention_type": "PERMISSION",
              "status": "resolved",
              "summary": null,
              "task_id": null,
              "conversation_id": null,
              "urgency": "high",
              "deferred_until": "2025-06-01T12:00:00Z"
            }
          ]
        }
        """.trimIndent()

        val result = AttentionParser.parsePendingAttention(json)
        assertEquals(2, result.size)

        with(result[0]) {
            assertEquals("ar_abc123", attentionRequestId)
            assertEquals("QUESTION", attentionType)
            assertEquals("pending", status)
            assertEquals("Which approach should I take?", summary)
            assertEquals("oc_xyz789", taskId)
            assertEquals("conv_456", conversationId)
            assertEquals("normal", urgency)
            assertNull(deferredUntil)
        }

        with(result[1]) {
            assertEquals("ar_def456", attentionRequestId)
            assertEquals("PERMISSION", attentionType)
            assertEquals("resolved", status)
            assertNull(summary)
            assertNull(taskId)
            assertNull(conversationId)
            assertEquals("high", urgency)
            assertEquals("2025-06-01T12:00:00Z", deferredUntil)
        }
    }

    @Test
    fun `parsePendingAttention with empty array returns empty list`() {
        val json = """{"type": "pending_attention", "attention_requests": []}"""
        val result = AttentionParser.parsePendingAttention(json)
        assertTrue(result.isEmpty())
    }

    @Test
    fun `parsePendingAttention skips malformed item among valid ones`() {
        val json = """
        {
          "type": "pending_attention",
          "attention_requests": [
            {
              "attention_request_id": "ar_good",
              "attention_type": "QUESTION",
              "status": "pending",
              "summary": "good",
              "task_id": "t1",
              "conversation_id": "c1",
              "urgency": "low",
              "deferred_until": null
            },
            "this is a string, not an object",
            {
              "attention_request_id": "ar_bad",
              "attention_type": null,
              "status": "pending"
            },
            null,
            {
              "attention_request_id": "ar_also_good",
              "attention_type": "TASK_FAILURE",
              "status": "deferred",
              "summary": "also good",
              "task_id": "t2",
              "conversation_id": "c2",
              "urgency": "urgent",
              "deferred_until": null
            }
          ]
        }
        """.trimIndent()

        val result = AttentionParser.parsePendingAttention(json)
        assertEquals(2, result.size)
        assertEquals("ar_good", result[0].attentionRequestId)
        assertEquals("ar_also_good", result[1].attentionRequestId)
    }

    @Test
    fun `parseAttentionEvent resolves correctly with conversationId and urgency null`() {
        val json = """
        {
          "type": "attention_resolved",
          "attention_request_id": "ar_abc123",
          "status": "resolved",
          "attention_type": "QUESTION",
          "summary": "Which approach should I take?",
          "task_id": "oc_xyz789",
          "deferred_until": null
        }
        """.trimIndent()

        val result = AttentionParser.parseAttentionEvent(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("ar_abc123", attentionRequestId)
            assertEquals("QUESTION", attentionType)
            assertEquals("resolved", status)
            assertEquals("Which approach should I take?", summary)
            assertEquals("oc_xyz789", taskId)
            assertNull(conversationId)
            assertNull(urgency)
            assertNull(deferredUntil)
        }
    }

    @Test
    fun `parseAttentionEvent unrecognized type returns null`() {
        val json = """{"type": "user_message", "attention_request_id": "ar_1", "attention_type": "QUESTION", "status": "pending"}"""
        assertNull(AttentionParser.parseAttentionEvent(json))
    }

    @Test
    fun `parseAttentionEvent garbage json returns null`() {
        assertNull(AttentionParser.parseAttentionEvent("this is not json"))
    }

    @Test
    fun `parseAttentionEvent empty string returns null`() {
        assertNull(AttentionParser.parseAttentionEvent(""))
    }

    @Test
    fun `parseAttentionEvent missing required fields returns null`() {
        val json = """{"type": "attention_created", "attention_request_id": "ar_1"}"""
        assertNull(AttentionParser.parseAttentionEvent(json))
    }

    @Test
    fun `parseAttentionEvent missing attention_request_id returns null`() {
        val json = """{"type": "attention_created", "attention_type": "QUESTION", "status": "pending"}"""
        assertNull(AttentionParser.parseAttentionEvent(json))
    }

    @Test
    fun `parseAttentionEvent missing status returns null`() {
        // Regression: attention_manager.py's initiate_contact() explicit
        // "attention_created" broadcast omitted "status" until a real-device
        // finding (Interaction Layer Step 5) showed the frame was silently
        // dropped by this parser as a result -- Android's own status-based
        // early-return existed long before that broadcast site did, but no
        // test here ever exercised the combination.
        val json = """{"type": "attention_created", "attention_request_id": "ar_1", "attention_type": "PERMISSION", "summary": "s", "task_id": "t1", "urgency": "HIGH"}"""
        assertNull(AttentionParser.parseAttentionEvent(json))
    }

    @Test
    fun `parseAttentionEvent parses the exact attention_created shape initiate_contact sends`() {
        // Matches app/attention_manager.py's initiate_contact() broadcast
        // payload field-for-field (post-fix, "status" included) rather than
        // an idealized/hand-picked field set -- the gap that let the missing
        // "status" field go unnoticed was every existing test here using its
        // own hand-crafted JSON instead of the real server shape.
        val json = """{"type": "attention_created", "attention_request_id": "attn_1", "attention_type": "PERMISSION", "status": "pending", "summary": "Needs your permission to continue.", "task_id": "oc_1", "urgency": "HIGH"}"""
        val event = AttentionParser.parseAttentionEvent(json)
        assertNotNull(event)
        assertEquals("attn_1", event!!.attentionRequestId)
        assertEquals("PERMISSION", event.attentionType)
        assertEquals("pending", event.status)
    }

    @Test
    fun `parsePendingAttention garbage string returns empty list`() {
        assertTrue(AttentionParser.parsePendingAttention("not json").isEmpty())
    }

    @Test
    fun `parsePendingAttention empty string returns empty list`() {
        assertTrue(AttentionParser.parsePendingAttention("").isEmpty())
    }

    @Test
    fun `parsePendingAttention non-pending_attention type returns empty list`() {
        val json = """{"type": "other", "attention_requests": []}"""
        assertTrue(AttentionParser.parsePendingAttention(json).isEmpty())
    }

    @Test
    fun `parsePendingAttention missing attention_requests returns empty list`() {
        val json = """{"type": "pending_attention"}"""
        assertTrue(AttentionParser.parsePendingAttention(json).isEmpty())
    }

    @Test
    fun `isAttentionEventType identifies all 8 event types`() {
        val types = setOf(
            "attention_created", "attention_contacting", "attention_pending",
            "attention_deferred", "attention_resolving", "attention_resolved",
            "attention_cancelled", "attention_expired",
        )
        for (t in types) {
            assertTrue("$t should be recognized", AttentionParser.isAttentionEventType(t))
        }
    }

    @Test
    fun `isAttentionEventType rejects unrecognized string`() {
        assertTrue(!AttentionParser.isAttentionEventType("user_message"))
    }
}
