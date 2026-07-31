package com.jarvis.companion.ui

import com.jarvis.companion.conversation.ConversationMessage
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationAdapterDiffCallbackTest {

    private val diffCallback = ConversationDiffCallback()

    @Test
    fun `areItemsTheSame returns true when ids match`() {
        val oldItem = makeMessage(id = "same-id", content = "old")
        val newItem = makeMessage(id = "same-id", content = "new")
        assertTrue(diffCallback.areItemsTheSame(oldItem, newItem))
    }

    @Test
    fun `areItemsTheSame returns false when ids differ`() {
        val oldItem = makeMessage(id = "id-a")
        val newItem = makeMessage(id = "id-b")
        assertFalse(diffCallback.areItemsTheSame(oldItem, newItem))
    }

    @Test
    fun `areContentsTheSame returns true for equal messages`() {
        val oldItem = makeMessage(id = "x", content = "hello")
        val newItem = makeMessage(id = "x", content = "hello")
        assertTrue(diffCallback.areContentsTheSame(oldItem, newItem))
    }

    @Test
    fun `areContentsTheSame returns false when content differs`() {
        val oldItem = makeMessage(id = "x", content = "hello")
        val newItem = makeMessage(id = "x", content = "world")
        assertFalse(diffCallback.areContentsTheSame(oldItem, newItem))
    }

    @Test
    fun `areContentsTheSame returns false when status differs`() {
        val oldItem = makeMessage(
            id = "x",
            type = ConversationMessage.Type.THINKING,
            status = ConversationMessage.Status.STARTED,
        )
        val newItem = makeMessage(
            id = "x",
            type = ConversationMessage.Type.THINKING,
            status = ConversationMessage.Status.COMPLETED,
        )
        assertFalse(diffCallback.areContentsTheSame(oldItem, newItem))
    }

    private fun makeMessage(
        id: String,
        type: ConversationMessage.Type = ConversationMessage.Type.SYSTEM_EVENT,
        content: String = "content",
        status: ConversationMessage.Status? = null,
    ): ConversationMessage = ConversationMessage(
        id = id,
        type = type,
        content = content,
        timestamp = 0L,
        status = status,
    )
}
