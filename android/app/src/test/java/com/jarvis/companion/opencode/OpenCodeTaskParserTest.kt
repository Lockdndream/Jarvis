package com.jarvis.companion.opencode

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OpenCodeTaskParserTest {

    @Test
    fun `recognizes both event types`() {
        assertTrue(OpenCodeTaskParser.isOpenCodeTaskEventType("opencode_task_created"))
        assertTrue(OpenCodeTaskParser.isOpenCodeTaskEventType("opencode_task_completed"))
        assertFalse(OpenCodeTaskParser.isOpenCodeTaskEventType("opencode_error"))
        assertFalse(OpenCodeTaskParser.isOpenCodeTaskEventType("voice_session_response"))
    }

    @Test
    fun `parseCreated parses task_id and instruction, status forced to running`() {
        val json = """
        {"type": "opencode_task_created", "task_id": "oc_abc", "session_id": "ses_1", "instruction": "Create a file"}
        """.trimIndent()
        val result = OpenCodeTaskParser.parseCreated(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("oc_abc", taskId)
            assertEquals(OpenCodeTaskStatus.RUNNING, status)
            assertEquals("Create a file", instruction)
        }
    }

    @Test
    fun `parseCreated missing task_id returns null`() {
        val json = """{"type": "opencode_task_created", "instruction": "Create a file"}"""
        assertNull(OpenCodeTaskParser.parseCreated(json))
    }

    @Test
    fun `parseCreated wrong type returns null`() {
        val json = """{"type": "opencode_task_completed", "task_id": "oc_abc"}"""
        assertNull(OpenCodeTaskParser.parseCreated(json))
    }

    @Test
    fun `parseCompleted parses task_id and status, instruction always null`() {
        val json = """{"type": "opencode_task_completed", "task_id": "oc_abc", "status": "completed", "source": "opencode"}"""
        val result = OpenCodeTaskParser.parseCompleted(json)
        assertNotNull(result)
        with(result!!) {
            assertEquals("oc_abc", taskId)
            assertEquals("completed", status)
            assertNull(instruction)
        }
    }

    @Test
    fun `parseCompleted with failed status parses as-is`() {
        val json = """{"type": "opencode_task_completed", "task_id": "oc_abc", "status": "failed"}"""
        val result = OpenCodeTaskParser.parseCompleted(json)
        assertEquals("failed", result?.status)
    }

    @Test
    fun `parseCompleted missing status returns null`() {
        val json = """{"type": "opencode_task_completed", "task_id": "oc_abc"}"""
        assertNull(OpenCodeTaskParser.parseCompleted(json))
    }

    @Test
    fun `malformed json returns null rather than throwing`() {
        assertNull(OpenCodeTaskParser.parseCreated("not json"))
        assertNull(OpenCodeTaskParser.parseCompleted("not json"))
    }
}
