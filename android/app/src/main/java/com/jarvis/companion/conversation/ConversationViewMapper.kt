package com.jarvis.companion.conversation

/**
 * Pure, view-free mapping from [ConversationMessage] data to the view-layer
 * decisions needed by [com.jarvis.companion.ui.ConversationAdapter].
 *
 * All functions are plain Kotlin on plain data so they can be unit-tested
 * without Robolectric or a real Android runtime.
 */

const val VIEW_TYPE_CHAT = 1
const val VIEW_TYPE_SYSTEM = 2

/**
 * Maps a message's [ConversationMessage.Type] to one of the adapter's view
 * type constants.
 *
 * User and assistant utterances share a chat-row layout (alignment distinguishes
 * them at bind time). Thinking updates, permission requests, and other system
 * events share a compact, muted system-row layout.
 */
fun ConversationMessage.toConversationViewType(): Int = when (type) {
    ConversationMessage.Type.USER_MESSAGE,
    ConversationMessage.Type.ASSISTANT_MESSAGE -> VIEW_TYPE_CHAT
    ConversationMessage.Type.THINKING,
    ConversationMessage.Type.PERMISSION_REQUEST,
    ConversationMessage.Type.SYSTEM_EVENT -> VIEW_TYPE_SYSTEM
}

/**
 * Returns the human-readable text that should be rendered for this message.
 *
 * - [ConversationMessage.Type.USER_MESSAGE] / [ConversationMessage.Type.ASSISTANT_MESSAGE]:
 *   the raw [ConversationMessage.content].
 * - [ConversationMessage.Type.THINKING]: a short progress prefix plus the
 *   populated [ConversationMessage.detail] when available, falling back to
 *   [ConversationMessage.content].
 * - [ConversationMessage.Type.PERMISSION_REQUEST]: an informational prefix
 *   plus the request summary. Interactive approve/deny controls are handled
 *   in [com.jarvis.companion.ui.ConversationAdapter.SystemViewHolder].
 * - [ConversationMessage.Type.SYSTEM_EVENT]: the raw [ConversationMessage.content].
 */
fun ConversationMessage.toConversationDisplayText(): String = when (type) {
    ConversationMessage.Type.USER_MESSAGE -> {
        if (status == ConversationMessage.Status.STARTED && content.isEmpty()) {
            "Listening\u2026"
        } else {
            content
        }
    }
    ConversationMessage.Type.ASSISTANT_MESSAGE,
    ConversationMessage.Type.SYSTEM_EVENT -> content
    ConversationMessage.Type.PERMISSION_REQUEST -> "Permission needed: $content"
    ConversationMessage.Type.THINKING -> {
        val base = detail?.takeIf { it.isNotBlank() } ?: content
        when (status) {
            ConversationMessage.Status.STARTED -> "Jarvis is working… $base"
            ConversationMessage.Status.COMPLETED -> "Done — $base"
            ConversationMessage.Status.FAILED -> "Failed — $base"
            null -> base
        }
    }
}

/**
 * True when a chat-row message should be aligned to the trailing/end side of
 * the screen (i.e. it came from the user).
 */
fun ConversationMessage.isUserAlignedEnd(): Boolean =
    type == ConversationMessage.Type.USER_MESSAGE

/**
 * True for a user message that is currently being transcribed live and has
 * not yet reached a final result.
 */
fun ConversationMessage.isLivePartialTranscript(): Boolean =
    type == ConversationMessage.Type.USER_MESSAGE && status == ConversationMessage.Status.STARTED

fun ConversationMessage.showsPermissionActions(): Boolean =
    type == ConversationMessage.Type.PERMISSION_REQUEST && status == null
