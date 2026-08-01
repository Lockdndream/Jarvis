package com.jarvis.companion.conversation

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ConversationRepositoryTest {

    private lateinit var repository: ConversationRepository

    @Before
    fun setUp() {
        repository = ConversationRepository()
    }

    @Test
    fun `initial state is empty`() {
        assertTrue(repository.messages.value.isEmpty())
    }

    @Test
    fun `addMessage appends in order`() {
        val first = sampleMessage("First")
        val second = sampleMessage("Second")
        repository.addMessage(first)
        repository.addMessage(second)

        val messages = repository.messages.value
        assertEquals(2, messages.size)
        assertEquals("First", messages[0].content)
        assertEquals("Second", messages[1].content)
    }

    @Test
    fun `clear empties the list`() {
        repository.addMessage(sampleMessage("A"))
        repository.addMessage(sampleMessage("B"))
        assertEquals(2, repository.messages.value.size)

        repository.clear()
        assertTrue(repository.messages.value.isEmpty())
    }

    @Test
    fun `messages cap drops oldest items`() {
        repeat(205) { index ->
            repository.addMessage(sampleMessage("message-$index"))
        }
        val messages = repository.messages.value
        assertEquals(200, messages.size)
        assertEquals("message-5", messages.first().content)
        assertEquals("message-204", messages.last().content)
    }

    @Test
    fun `state flow emits updates`() = runTest {
        val collected = mutableListOf<List<ConversationMessage>>()
        val job = launch { repository.messages.collect { collected.add(it) } }
        advanceUntilIdle()

        repository.addMessage(sampleMessage("A"))
        repository.addMessage(sampleMessage("B"))
        advanceUntilIdle()
        job.cancel()

        assertTrue(collected.size >= 2)
        assertEquals(0, collected.first().size)
        assertEquals(2, collected.last().size)
    }

    @Test
    fun `markPermissionResolved finds and replaces matching message`() {
        val perm = ConversationMessage(
            id = "perm-1",
            type = ConversationMessage.Type.PERMISSION_REQUEST,
            content = "access calendar",
            timestamp = 0L,
            permissionId = "ar-1",
        )
        repository.addMessage(perm)
        assertEquals(1, repository.messages.value.size)
        assertEquals(null, repository.messages.value[0].status)

        repository.markPermissionResolved("ar-1")

        val messages = repository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Status.COMPLETED, messages[0].status)
        assertEquals("perm-1", messages[0].id)
    }

    @Test
    fun `markPermissionResolved no-op when no match`() {
        val perm = ConversationMessage(
            id = "perm-1",
            type = ConversationMessage.Type.PERMISSION_REQUEST,
            content = "access calendar",
            timestamp = 0L,
            permissionId = "ar-1",
        )
        repository.addMessage(perm)

        repository.markPermissionResolved("ar-nonexistent")

        val messages = repository.messages.value
        assertEquals(1, messages.size)
        assertEquals(null, messages[0].status)
    }

    @Test
    fun `markPermissionResolved no-op when already resolved`() {
        val perm = ConversationMessage(
            id = "perm-1",
            type = ConversationMessage.Type.PERMISSION_REQUEST,
            content = "access calendar",
            timestamp = 0L,
            permissionId = "ar-1",
            status = ConversationMessage.Status.COMPLETED,
        )
        repository.addMessage(perm)

        repository.markPermissionResolved("ar-1")

        val messages = repository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Status.COMPLETED, messages[0].status)
    }

    @Test
    fun `markPermissionResolved only affects matching permissionId in mixed list`() {
        val perm1 = ConversationMessage(
            id = "perm-1",
            type = ConversationMessage.Type.PERMISSION_REQUEST,
            content = "access calendar",
            timestamp = 0L,
            permissionId = "ar-1",
        )
        val perm2 = ConversationMessage(
            id = "perm-2",
            type = ConversationMessage.Type.PERMISSION_REQUEST,
            content = "read contacts",
            timestamp = 0L,
            permissionId = "ar-2",
        )
        val user = sampleMessage("hello")
        repository.addMessage(perm1)
        repository.addMessage(user)
        repository.addMessage(perm2)

        repository.markPermissionResolved("ar-1")

        val messages = repository.messages.value
        assertEquals(3, messages.size)
        assertEquals(ConversationMessage.Status.COMPLETED, messages[0].status)
        assertEquals(null, messages[1].status)
        assertEquals(null, messages[2].status)
    }

    @Test
    fun `updateMessage replaces content and preserves position`() {
        val first = sampleMessage("First")
        val second = sampleMessage("Second")
        repository.addMessage(first)
        repository.addMessage(second)

        repository.updateMessage(first.id, content = "Updated first")

        val messages = repository.messages.value
        assertEquals(2, messages.size)
        assertEquals("Updated first", messages[0].content)
        assertEquals("Second", messages[1].content)
        assertEquals(first.id, messages[0].id)
    }

    @Test
    fun `updateMessage sets status when provided`() {
        val message = sampleMessage("Hello")
        repository.addMessage(message)

        repository.updateMessage(message.id, content = "Hello", status = ConversationMessage.Status.STARTED)

        val messages = repository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Status.STARTED, messages[0].status)
    }

    @Test
    fun `updateMessage preserves existing status when omitted`() {
        val message = sampleMessage("Hello").copy(status = ConversationMessage.Status.COMPLETED)
        repository.addMessage(message)

        repository.updateMessage(message.id, content = "Updated")

        val messages = repository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Status.COMPLETED, messages[0].status)
        assertEquals("Updated", messages[0].content)
    }

    @Test
    fun `updateMessage is no-op when id not found`() {
        val message = sampleMessage("Hello")
        repository.addMessage(message)

        repository.updateMessage("nonexistent", content = "Updated")

        val messages = repository.messages.value
        assertEquals(1, messages.size)
        assertEquals("Hello", messages[0].content)
    }

    @Test
    fun `removeMessage deletes message by id`() {
        val first = sampleMessage("First")
        val second = sampleMessage("Second")
        repository.addMessage(first)
        repository.addMessage(second)

        repository.removeMessage(first.id)

        val messages = repository.messages.value
        assertEquals(1, messages.size)
        assertEquals("Second", messages[0].content)
    }

    @Test
    fun `removeMessage is no-op when id not found`() {
        val message = sampleMessage("Hello")
        repository.addMessage(message)

        repository.removeMessage("nonexistent")

        assertEquals(1, repository.messages.value.size)
    }

    private fun sampleMessage(content: String) = ConversationMessage(
        id = content,
        type = ConversationMessage.Type.USER_MESSAGE,
        content = content,
        timestamp = 0L,
    )
}
