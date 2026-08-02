package com.jarvis.companion.network

import android.content.Context
import com.jarvis.companion.attention.AttentionRepository
import com.jarvis.companion.conversation.ConversationMessage
import com.jarvis.companion.conversation.ConversationRepository
import com.jarvis.companion.opencode.OpenCodeTaskRepository
import com.jarvis.companion.telemetry.TelemetryRecorder
import com.jarvis.companion.voice.VoiceSessionRepository
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito
import java.io.File

/**
 * CompanionWebSocketClient-level regression tests for Interaction Layer Step 2:
 * verifies that conversation frames are dispatched into ConversationRepository
 * and that the existing voice/attention behavior is preserved.
 *
 * The frame-dispatch methods are private, so these tests invoke them via
 * reflection — the same style used elsewhere for testing internal event
 * routing without spinning up a real WebSocket server.
 */
class CompanionWebSocketClientConversationTest {

    private lateinit var telemetry: TelemetryRecorder
    private lateinit var attentionRepository: AttentionRepository
    private lateinit var voiceSessionRepository: VoiceSessionRepository
    private lateinit var openCodeTaskRepository: OpenCodeTaskRepository
    private lateinit var conversationRepository: ConversationRepository
    private lateinit var client: CompanionWebSocketClient

    @Before
    fun setUp() {
        val tempDir = createTempDir("websocket_test_")
        val context = Mockito.mock(Context::class.java)
        Mockito.`when`(context.filesDir).thenReturn(tempDir)
        telemetry = TelemetryRecorder(context)
        attentionRepository = AttentionRepository()
        voiceSessionRepository = VoiceSessionRepository()
        openCodeTaskRepository = OpenCodeTaskRepository()
        conversationRepository = ConversationRepository()
        client = CompanionWebSocketClient(
            telemetry = telemetry,
            deviceId = "test-device",
            statusInputs = { DeviceStatusSnapshot(false, false) },
            attentionRepository = attentionRepository,
            voiceSessionRepository = voiceSessionRepository,
            openCodeTaskRepository = openCodeTaskRepository,
            conversationRepository = conversationRepository,
        )
    }

    @Test
    fun `conversation_turn frame ends up in conversationRepository`() {
        val frame = """
        {
          "type": "conversation_turn",
          "role": "user",
          "content": "Hello from the test",
          "voice_session_id": "vs_test",
          "conversation_id": "conv_test",
          "timestamp": "2026-07-31T12:00:00.000Z"
        }
        """.trimIndent()

        invokePrivate("applyConversationFrame", frame)

        val messages = conversationRepository.messages.value
        assertEquals(1, messages.size)
        with(messages[0]) {
            assertEquals(ConversationMessage.Type.USER_MESSAGE, type)
            assertEquals("Hello from the test", content)
            assertEquals("vs_test", metadata?.get("voice_session_id"))
            assertEquals("conv_test", metadata?.get("conversation_id"))
        }
    }

    @Test
    fun `conversation_turn echo is suppressed when it matches a transcript this device just sent`() {
        client.sendVoiceSessionTranscript("vs_test", "Hello from the test")

        val frame = """
        {
          "type": "conversation_turn",
          "role": "user",
          "content": "Hello from the test",
          "voice_session_id": "vs_test",
          "conversation_id": "conv_test",
          "timestamp": "2026-07-31T12:00:00.000Z"
        }
        """.trimIndent()

        invokePrivate("applyConversationFrame", frame)

        assertTrue(conversationRepository.messages.value.isEmpty())
    }

    @Test
    fun `conversation_turn with different content than what this device sent is not suppressed`() {
        client.sendVoiceSessionTranscript("vs_test", "Something else entirely")

        val frame = """
        {
          "type": "conversation_turn",
          "role": "user",
          "content": "Hello from the test",
          "voice_session_id": "vs_test",
          "conversation_id": "conv_test",
          "timestamp": "2026-07-31T12:00:00.000Z"
        }
        """.trimIndent()

        invokePrivate("applyConversationFrame", frame)

        val messages = conversationRepository.messages.value
        assertEquals(1, messages.size)
        assertEquals("Hello from the test", messages[0].content)
    }

    @Test
    fun `conversation_turn suppression only consumes the marker once`() {
        client.sendVoiceSessionTranscript("vs_test", "Hello from the test")

        val frame = """
        {
          "type": "conversation_turn",
          "role": "user",
          "content": "Hello from the test",
          "voice_session_id": "vs_test",
          "conversation_id": "conv_test",
          "timestamp": "2026-07-31T12:00:00.000Z"
        }
        """.trimIndent()

        invokePrivate("applyConversationFrame", frame)
        invokePrivate("applyConversationFrame", frame)

        // First delivery is suppressed (this device's own echo); a second,
        // identical frame arriving later (e.g. a genuine repeat turn) is not
        // silently swallowed forever -- the marker is one-shot.
        assertEquals(1, conversationRepository.messages.value.size)
    }

    @Test
    fun `voice_session_response frame updates both repositories`() {
        // Seed a voice session so applyResponse actually records the reply.
        voiceSessionRepository.applyOpened(
            com.jarvis.companion.voice.VoiceSession(
                voiceSessionId = "vs_test",
                state = "listening",
                conversationId = "conv_test",
                attentionRequestId = null,
                greeting = null,
            )
        )
        val frame = """
        {
          "type": "voice_session_response",
          "voice_session_id": "vs_test",
          "response": "The answer is 42.",
          "conversation_id": "conv_test",
          "voice_session_state": "listening"
        }
        """.trimIndent()

        invokePrivate("applyVoiceSessionFrame", frame)

        assertEquals("The answer is 42.", voiceSessionRepository.lastResponse.value)
        val messages = conversationRepository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Type.ASSISTANT_MESSAGE, messages[0].type)
        assertEquals("The answer is 42.", messages[0].content)
    }

    @Test
    fun `voice_session_opened frame with a greeting adds an assistant message`() {
        val frame = """
        {
          "type": "voice_session_opened",
          "voice_session_id": "vs_greet",
          "state": "listening",
          "conversation_id": "conv_test",
          "attention_request_id": "ar_bound",
          "greeting": "Hi, what can I help with?"
        }
        """.trimIndent()

        invokePrivate("applyVoiceSessionFrame", frame)

        assertEquals("vs_greet", voiceSessionRepository.current.value?.voiceSessionId)
        val messages = conversationRepository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Type.ASSISTANT_MESSAGE, messages[0].type)
        assertEquals("Hi, what can I help with?", messages[0].content)
        assertEquals("vs_greet", messages[0].metadata?.get("voice_session_id"))
    }

    @Test
    fun `voice_session_opened frame without a greeting adds no conversation message`() {
        val frame = """
        {
          "type": "voice_session_opened",
          "voice_session_id": "vs_no_greet",
          "state": "listening",
          "conversation_id": "conv_test",
          "attention_request_id": null,
          "greeting": null
        }
        """.trimIndent()

        invokePrivate("applyVoiceSessionFrame", frame)

        assertEquals("vs_no_greet", voiceSessionRepository.current.value?.voiceSessionId)
        assertTrue(conversationRepository.messages.value.isEmpty())
    }

    @Test
    fun `attention_created PERMISSION frame updates both repositories`() {
        val frame = """
        {
          "type": "attention_created",
          "attention_request_id": "ar_perm",
          "attention_type": "PERMISSION",
          "status": "pending",
          "summary": "Allow access to contacts?",
          "conversation_id": "conv_test"
        }
        """.trimIndent()

        invokePrivate("applyAttentionFrame", frame)

        val attention = attentionRepository.outstanding.value
        assertEquals(1, attention.size)
        assertEquals("ar_perm", attention[0].attentionRequestId)

        val messages = conversationRepository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Type.PERMISSION_REQUEST, messages[0].type)
        assertEquals("Allow access to contacts?", messages[0].content)
        assertEquals("ar_perm", messages[0].permissionId)
    }

    @Test
    fun `attention_resolved PERMISSION resolves matching conversation message`() {
        conversationRepository.addMessage(
            ConversationMessage(
                id = "msg-1",
                type = ConversationMessage.Type.PERMISSION_REQUEST,
                content = "Allow access to contacts?",
                timestamp = 0L,
                permissionId = "ar_perm",
            )
        )

        val frame = """
        {
          "type": "attention_resolved",
          "attention_request_id": "ar_perm",
          "attention_type": "PERMISSION",
          "status": "resolved",
          "summary": "Allow access to contacts?"
        }
        """.trimIndent()

        invokePrivate("applyAttentionFrame", frame)

        val messages = conversationRepository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Status.COMPLETED, messages[0].status)
    }

    @Test
    fun `attention_cancelled PERMISSION resolves matching conversation message`() {
        conversationRepository.addMessage(
            ConversationMessage(
                id = "msg-1",
                type = ConversationMessage.Type.PERMISSION_REQUEST,
                content = "Allow access to contacts?",
                timestamp = 0L,
                permissionId = "ar_perm",
            )
        )

        val frame = """
        {
          "type": "attention_cancelled",
          "attention_request_id": "ar_perm",
          "attention_type": "PERMISSION",
          "status": "cancelled",
          "summary": "Allow access to contacts?"
        }
        """.trimIndent()

        invokePrivate("applyAttentionFrame", frame)

        val messages = conversationRepository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Status.COMPLETED, messages[0].status)
    }

    @Test
    fun `attention_expired PERMISSION resolves matching conversation message`() {
        conversationRepository.addMessage(
            ConversationMessage(
                id = "msg-1",
                type = ConversationMessage.Type.PERMISSION_REQUEST,
                content = "Allow access to contacts?",
                timestamp = 0L,
                permissionId = "ar_perm",
            )
        )

        val frame = """
        {
          "type": "attention_expired",
          "attention_request_id": "ar_perm",
          "attention_type": "PERMISSION",
          "status": "expired",
          "summary": "Allow access to contacts?"
        }
        """.trimIndent()

        invokePrivate("applyAttentionFrame", frame)

        val messages = conversationRepository.messages.value
        assertEquals(1, messages.size)
        assertEquals(ConversationMessage.Status.COMPLETED, messages[0].status)
    }

    @Test
    fun `attention_resolved non-PERMISSION does not touch conversation repository`() {
        val frame = """
        {
          "type": "attention_resolved",
          "attention_request_id": "ar_question",
          "attention_type": "QUESTION",
          "status": "resolved",
          "summary": "What is your name?"
        }
        """.trimIndent()

        invokePrivate("applyAttentionFrame", frame)

        assertTrue(conversationRepository.messages.value.isEmpty())
    }

    @Test
    fun `permission_response_ack resolves matching permission message`() {
        conversationRepository.addMessage(
            ConversationMessage(
                id = "msg-1",
                type = ConversationMessage.Type.PERMISSION_REQUEST,
                content = "Allow access to contacts?",
                timestamp = 0L,
                permissionId = "ar_perm",
            )
        )

        val frame = """
        {
          "type": "permission_response_ack",
          "attention_request_id": "ar_perm",
          "response": "approved",
          "conversation_id": "conv_test"
        }
        """.trimIndent()

        invokePrivate("applyConversationFrame", frame)

        val messages = conversationRepository.messages.value
        assertEquals(2, messages.size)
        assertEquals(ConversationMessage.Type.PERMISSION_REQUEST, messages[0].type)
        assertEquals(ConversationMessage.Status.COMPLETED, messages[0].status)
        assertEquals(ConversationMessage.Type.SYSTEM_EVENT, messages[1].type)
    }

    @Test
    fun `sendPermissionResponse does not mark resolved when no connection`() {
        conversationRepository.addMessage(
            ConversationMessage(
                id = "msg-1",
                type = ConversationMessage.Type.PERMISSION_REQUEST,
                content = "Allow access to contacts?",
                timestamp = 0L,
                permissionId = "ar_perm",
            )
        )

        val result = client.sendPermissionResponse("ar_perm", "approve")

        assertFalse(result)
        val messages = conversationRepository.messages.value
        assertEquals(null, messages[0].status)
    }

    @Test
    fun `attention_created non-PERMISSION frame does not add conversation message`() {
        val frame = """
        {
          "type": "attention_created",
          "attention_request_id": "ar_question",
          "attention_type": "QUESTION",
          "status": "pending",
          "summary": "What is your name?"
        }
        """.trimIndent()

        invokePrivate("applyAttentionFrame", frame)

        assertEquals(1, attentionRepository.outstanding.value.size)
        assertTrue(conversationRepository.messages.value.isEmpty())
    }

    private fun invokePrivate(methodName: String, text: String) {
        val method = CompanionWebSocketClient::class.java.getDeclaredMethod(
            methodName,
            String::class.java,
        )
        method.isAccessible = true
        method.invoke(client, text)
    }

    private fun createTempDir(prefix: String): File {
        val dir = File(System.getProperty("java.io.tmpdir"), prefix + System.nanoTime())
        dir.mkdirs()
        return dir
    }
}
