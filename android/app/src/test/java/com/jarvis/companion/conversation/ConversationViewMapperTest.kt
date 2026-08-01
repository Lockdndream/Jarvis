package com.jarvis.companion.conversation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationViewMapperTest {

    // --- toConversationViewType ---

    @Test
    fun `USER_MESSAGE maps to chat view type`() {
        val message = makeMessage(type = ConversationMessage.Type.USER_MESSAGE)
        assertEquals(VIEW_TYPE_CHAT, message.toConversationViewType())
    }

    @Test
    fun `ASSISTANT_MESSAGE maps to chat view type`() {
        val message = makeMessage(type = ConversationMessage.Type.ASSISTANT_MESSAGE)
        assertEquals(VIEW_TYPE_CHAT, message.toConversationViewType())
    }

    @Test
    fun `THINKING maps to system view type`() {
        val message = makeMessage(type = ConversationMessage.Type.THINKING)
        assertEquals(VIEW_TYPE_SYSTEM, message.toConversationViewType())
    }

    @Test
    fun `PERMISSION_REQUEST maps to system view type`() {
        val message = makeMessage(type = ConversationMessage.Type.PERMISSION_REQUEST)
        assertEquals(VIEW_TYPE_SYSTEM, message.toConversationViewType())
    }

    @Test
    fun `SYSTEM_EVENT maps to system view type`() {
        val message = makeMessage(type = ConversationMessage.Type.SYSTEM_EVENT)
        assertEquals(VIEW_TYPE_SYSTEM, message.toConversationViewType())
    }

    // --- toConversationDisplayText ---

    @Test
    fun `USER_MESSAGE display text is content`() {
        val message = makeMessage(
            type = ConversationMessage.Type.USER_MESSAGE,
            content = "Turn on the lights",
        )
        assertEquals("Turn on the lights", message.toConversationDisplayText())
    }

    @Test
    fun `USER_MESSAGE started with empty content shows listening placeholder`() {
        val message = makeMessage(
            type = ConversationMessage.Type.USER_MESSAGE,
            content = "",
            status = ConversationMessage.Status.STARTED,
        )
        assertEquals("Listening\u2026", message.toConversationDisplayText())
    }

    @Test
    fun `USER_MESSAGE started with partial content shows content`() {
        val message = makeMessage(
            type = ConversationMessage.Type.USER_MESSAGE,
            content = "what is the",
            status = ConversationMessage.Status.STARTED,
        )
        assertEquals("what is the", message.toConversationDisplayText())
    }

    @Test
    fun `ASSISTANT_MESSAGE display text is content`() {
        val message = makeMessage(
            type = ConversationMessage.Type.ASSISTANT_MESSAGE,
            content = "Done.",
        )
        assertEquals("Done.", message.toConversationDisplayText())
    }

    @Test
    fun `THINKING started includes working prefix and detail`() {
        val message = makeMessage(
            type = ConversationMessage.Type.THINKING,
            content = "tool",
            detail = "checking the weather",
            status = ConversationMessage.Status.STARTED,
        )
        assertEquals(
            "Jarvis is working… checking the weather",
            message.toConversationDisplayText(),
        )
    }

    @Test
    fun `THINKING completed includes done prefix and detail`() {
        val message = makeMessage(
            type = ConversationMessage.Type.THINKING,
            content = "tool",
            detail = "weather is sunny",
            status = ConversationMessage.Status.COMPLETED,
        )
        assertEquals(
            "Done — weather is sunny",
            message.toConversationDisplayText(),
        )
    }

    @Test
    fun `THINKING failed includes failed prefix and detail`() {
        val message = makeMessage(
            type = ConversationMessage.Type.THINKING,
            content = "tool",
            detail = "network unreachable",
            status = ConversationMessage.Status.FAILED,
        )
        assertEquals(
            "Failed — network unreachable",
            message.toConversationDisplayText(),
        )
    }

    @Test
    fun `THINKING falls back to content when detail is blank`() {
        val message = makeMessage(
            type = ConversationMessage.Type.THINKING,
            content = "running tool",
            detail = "",
            status = ConversationMessage.Status.STARTED,
        )
        assertEquals(
            "Jarvis is working… running tool",
            message.toConversationDisplayText(),
        )
    }

    @Test
    fun `THINKING falls back to content when detail is null`() {
        val message = makeMessage(
            type = ConversationMessage.Type.THINKING,
            content = "running tool",
            status = ConversationMessage.Status.STARTED,
        )
        assertEquals(
            "Jarvis is working… running tool",
            message.toConversationDisplayText(),
        )
    }

    @Test
    fun `THINKING without status uses detail as-is`() {
        val message = makeMessage(
            type = ConversationMessage.Type.THINKING,
            content = "tool",
            detail = "progress",
        )
        assertEquals("progress", message.toConversationDisplayText())
    }

    @Test
    fun `PERMISSION_REQUEST display text includes prefix`() {
        val message = makeMessage(
            type = ConversationMessage.Type.PERMISSION_REQUEST,
            content = "access calendar",
        )
        assertEquals(
            "Permission needed: access calendar",
            message.toConversationDisplayText(),
        )
    }

    @Test
    fun `SYSTEM_EVENT display text is content`() {
        val message = makeMessage(
            type = ConversationMessage.Type.SYSTEM_EVENT,
            content = "Microphone permission is required to talk to Jarvis",
        )
        assertEquals(
            "Microphone permission is required to talk to Jarvis",
            message.toConversationDisplayText(),
        )
    }

    // --- isUserAlignedEnd ---

    @Test
    fun `USER_MESSAGE is aligned end`() {
        val message = makeMessage(type = ConversationMessage.Type.USER_MESSAGE)
        assertTrue(message.isUserAlignedEnd())
    }

    @Test
    fun `ASSISTANT_MESSAGE is not aligned end`() {
        val message = makeMessage(type = ConversationMessage.Type.ASSISTANT_MESSAGE)
        assertFalse(message.isUserAlignedEnd())
    }

    @Test
    fun `SYSTEM row types are not aligned end`() {
        for (type in listOf(
            ConversationMessage.Type.THINKING,
            ConversationMessage.Type.PERMISSION_REQUEST,
            ConversationMessage.Type.SYSTEM_EVENT,
        )) {
            assertFalse(
                "type=$type should not be user-aligned end",
                makeMessage(type = type).isUserAlignedEnd(),
            )
        }
    }

    // --- isLivePartialTranscript ---

    @Test
    fun `USER_MESSAGE with STARTED status is live partial transcript`() {
        val message = makeMessage(
            type = ConversationMessage.Type.USER_MESSAGE,
            status = ConversationMessage.Status.STARTED,
        )
        assertTrue(message.isLivePartialTranscript())
    }

    @Test
    fun `USER_MESSAGE with COMPLETED status is not live partial transcript`() {
        val message = makeMessage(
            type = ConversationMessage.Type.USER_MESSAGE,
            status = ConversationMessage.Status.COMPLETED,
        )
        assertFalse(message.isLivePartialTranscript())
    }

    @Test
    fun `USER_MESSAGE without status is not live partial transcript`() {
        val message = makeMessage(type = ConversationMessage.Type.USER_MESSAGE)
        assertFalse(message.isLivePartialTranscript())
    }

    @Test
    fun `non-USER_MESSAGE types are never live partial transcript`() {
        for (type in listOf(
            ConversationMessage.Type.ASSISTANT_MESSAGE,
            ConversationMessage.Type.THINKING,
            ConversationMessage.Type.PERMISSION_REQUEST,
            ConversationMessage.Type.SYSTEM_EVENT,
        )) {
            assertFalse(
                "type=$type should not be a live partial transcript",
                makeMessage(type = type, status = ConversationMessage.Status.STARTED).isLivePartialTranscript(),
            )
        }
    }

    // --- showsPermissionActions ---

    @Test
    fun `unresolved PERMISSION_REQUEST shows actions`() {
        val message = ConversationMessage(
            id = "test-id",
            type = ConversationMessage.Type.PERMISSION_REQUEST,
            content = "access calendar",
            timestamp = 0L,
            permissionId = "ar-1",
            status = null,
        )
        assertTrue(message.showsPermissionActions())
    }

    @Test
    fun `resolved PERMISSION_REQUEST does not show actions`() {
        val message = ConversationMessage(
            id = "test-id",
            type = ConversationMessage.Type.PERMISSION_REQUEST,
            content = "access calendar",
            timestamp = 0L,
            permissionId = "ar-1",
            status = ConversationMessage.Status.COMPLETED,
        )
        assertFalse(message.showsPermissionActions())
    }

    @Test
    fun `non-PERMISSION_REQUEST types never show actions`() {
        for (type in listOf(
            ConversationMessage.Type.USER_MESSAGE,
            ConversationMessage.Type.ASSISTANT_MESSAGE,
            ConversationMessage.Type.THINKING,
            ConversationMessage.Type.SYSTEM_EVENT,
        )) {
            assertFalse(
                "type=$type should not show permission actions",
                makeMessage(type = type).showsPermissionActions(),
            )
        }
    }

    private fun makeMessage(
        type: ConversationMessage.Type,
        content: String = "content",
        detail: String? = null,
        status: ConversationMessage.Status? = null,
    ): ConversationMessage = ConversationMessage(
        id = "test-id",
        type = type,
        content = content,
        detail = detail,
        timestamp = 0L,
        status = status,
    )
}
