package com.jarvis.companion.conversation

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * In-memory client-side mirror of the conversation items that should be
 * visible in the phone UI. The canonical session history lives on the laptop;
 * this repository is intentionally transient (cleared on process death and on
 * disconnect by [com.jarvis.companion.network.CompanionWebSocketClient]).
 *
 * A 200-message cap prevents an unattended long-running session from growing
 * this list unboundedly in memory; once the cap is exceeded the oldest messages
 * are dropped. 200 was chosen as a generous UI viewport buffer (well beyond
 * what a user can scroll through quickly) while still capping worst-case memory.
 */
class ConversationRepository {
    private val _messages = MutableStateFlow<List<ConversationMessage>>(emptyList())
    val messages: StateFlow<List<ConversationMessage>> = _messages.asStateFlow()

    fun addMessage(message: ConversationMessage) {
        _messages.update { current ->
            (current + message).takeLast(MESSAGE_CAP)
        }
    }

    fun clear() {
        _messages.value = emptyList()
    }

    fun markPermissionResolved(permissionId: String) {
        _messages.update { current ->
            current.map { message ->
                if (message.type == ConversationMessage.Type.PERMISSION_REQUEST
                    && message.permissionId == permissionId
                    && message.status == null
                ) {
                    message.copy(status = ConversationMessage.Status.COMPLETED)
                } else {
                    message
                }
            }
        }
    }

    private companion object {
        const val MESSAGE_CAP = 200
    }
}
