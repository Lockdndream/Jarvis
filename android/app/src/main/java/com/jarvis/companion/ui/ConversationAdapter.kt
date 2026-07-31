package com.jarvis.companion.ui

import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.color.MaterialColors
import com.jarvis.companion.R
import com.jarvis.companion.conversation.ConversationMessage
import com.jarvis.companion.conversation.VIEW_TYPE_CHAT
import com.jarvis.companion.conversation.VIEW_TYPE_SYSTEM
import com.jarvis.companion.conversation.isUserAlignedEnd
import com.jarvis.companion.conversation.showsPermissionActions
import com.jarvis.companion.conversation.toConversationDisplayText
import com.jarvis.companion.conversation.toConversationViewType
import com.jarvis.companion.service.PresenceService

/**
 * RecyclerView adapter for the in-memory conversation transcript.
 *
 * Two view types:
 * - [VIEW_TYPE_CHAT]: user and assistant utterances, distinguished at bind
 *   time by alignment (user on the trailing/end side, assistant on the
 *   leading/start side).
 * - [VIEW_TYPE_SYSTEM]: thinking updates, permission requests, and system
 *   events rendered as compact, muted informational rows.
 *
 * The actual content and view-type decisions are delegated to the pure
 * functions in [com.jarvis.companion.conversation.ConversationViewMapper] so
 * they remain unit-testable.
 */
class ConversationAdapter : ListAdapter<ConversationMessage, RecyclerView.ViewHolder>(
    ConversationDiffCallback()
) {

    override fun getItemViewType(position: Int): Int =
        getItem(position).toConversationViewType()

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return when (viewType) {
            VIEW_TYPE_CHAT -> ChatViewHolder(
                inflater.inflate(R.layout.item_conversation_chat, parent, false)
            )
            VIEW_TYPE_SYSTEM -> SystemViewHolder(
                inflater.inflate(R.layout.item_conversation_system, parent, false)
            )
            else -> throw IllegalArgumentException("Unknown conversation view type: $viewType")
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        val message = getItem(position)
        when (holder) {
            is ChatViewHolder -> holder.bind(message)
            is SystemViewHolder -> holder.bind(message)
        }
    }

    class ChatViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val card: MaterialCardView = itemView.findViewById(R.id.chatCard)
        private val text: TextView = itemView.findViewById(R.id.chatText)

        fun bind(message: ConversationMessage) {
            text.text = message.toConversationDisplayText()
            val params = card.layoutParams as FrameLayout.LayoutParams
            params.gravity = if (message.isUserAlignedEnd()) Gravity.END else Gravity.START
            card.layoutParams = params
            // Real-device finding (Step 5): alignment alone disappears once a
            // bubble is wide enough to nearly fill the screen -- a colored
            // stroke stays visible regardless of message width, unlike a
            // fill-color change it can't create a text/background contrast
            // problem. colorPrimary is a base MaterialComponents attribute
            // (this app's theme is Theme.MaterialComponents.DayNight, not
            // Material3, so Material3-only container-color attrs aren't
            // guaranteed to resolve here).
            if (message.isUserAlignedEnd()) {
                val primary = MaterialColors.getColor(
                    card, com.google.android.material.R.attr.colorPrimary,
                )
                card.strokeColor = primary
                card.strokeWidth = (2 * card.resources.displayMetrics.density).toInt()
            } else {
                card.strokeWidth = 0
            }
        }
    }

    class SystemViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val text: TextView = itemView.findViewById(R.id.systemText)
        private val permissionActions: LinearLayout = itemView.findViewById(R.id.permissionActions)
        private val approveButton: MaterialButton = itemView.findViewById(R.id.approveButton)
        private val rejectButton: MaterialButton = itemView.findViewById(R.id.rejectButton)

        fun bind(message: ConversationMessage) {
            text.text = message.toConversationDisplayText()
            val showActions = message.showsPermissionActions()
            permissionActions.visibility = if (showActions) View.VISIBLE else View.GONE
            if (showActions) {
                val permissionId = message.permissionId!!
                approveButton.isEnabled = true
                rejectButton.isEnabled = true
                approveButton.setOnClickListener {
                    approveButton.isEnabled = false
                    rejectButton.isEnabled = false
                    PresenceService.activeClient?.sendPermissionResponse(permissionId, "approve")
                }
                rejectButton.setOnClickListener {
                    approveButton.isEnabled = false
                    rejectButton.isEnabled = false
                    PresenceService.activeClient?.sendPermissionResponse(permissionId, "reject")
                }
            }
        }
    }
}

/**
 * [DiffUtil.ItemCallback] for [ConversationMessage]. Items are the same when
 * their locally-minted ids match; contents are the same when the data class
 * structural equality holds.
 */
class ConversationDiffCallback : DiffUtil.ItemCallback<ConversationMessage>() {
    override fun areItemsTheSame(
        oldItem: ConversationMessage,
        newItem: ConversationMessage,
    ): Boolean = oldItem.id == newItem.id

    override fun areContentsTheSame(
        oldItem: ConversationMessage,
        newItem: ConversationMessage,
    ): Boolean = oldItem == newItem
}
