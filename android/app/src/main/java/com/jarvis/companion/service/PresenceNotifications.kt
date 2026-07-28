package com.jarvis.companion.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.network.DisconnectReason
import com.jarvis.companion.ui.ConnectionStatusActivity
import com.jarvis.companion.ui.SettingsActivity
import com.jarvis.companion.ui.VoiceActivity

private const val CHANNEL_ID = "jarvis_companion_status"
const val PRESENCE_NOTIFICATION_ID = 1001

// Milestone 9B.10 real-device finding: a plain applicationContext.startActivity()
// from this Service's background context can have its window created but
// never granted focus by Android's background-activity-start restrictions
// (confirmed via dumpsys power history + ActivityTaskManager focus-transfer
// logs showing VoiceActivity never received focus, unlike a foreground-
// launched activity moments later). A high-importance channel's
// setFullScreenIntent() is the documented exemption from that restriction
// (the same mechanism incoming-call/alarm UIs use) -- separate channel from
// CHANNEL_ID because full-screen-intent delivery requires IMPORTANCE_HIGH,
// which would be wrong (too noisy) for the ongoing low-priority status
// notification.
private const val HANDOFF_CHANNEL_ID = "jarvis_wakeword_handoff"
const val WAKEWORD_HANDOFF_NOTIFICATION_ID = 1002

// Interaction Layer v1 (Goals 2/3): a delegated OpenCode task's lifecycle.
// TASK_RUNNING reuses CHANNEL_ID (IMPORTANCE_LOW, ambient) — it's an
// ongoing, ID-updated notification for the whole task duration, not
// something that should interrupt. The terminal notification uses its own
// IMPORTANCE_DEFAULT channel so completion/failure actually surfaces
// (sound + brief heads-up) instead of silently joining the low-priority
// status notification — this is the concrete fix for the real gap found
// during capability testing (a task completed with zero signal to the
// user, traced to a missing push dependency; this path doesn't depend on
// push at all, it rides the same WebSocket connection already open).
const val OPENCODE_TASK_RUNNING_NOTIFICATION_ID = 1003
const val OPENCODE_TASK_TERMINAL_NOTIFICATION_ID = 1004
private const val TASK_TERMINAL_CHANNEL_ID = "jarvis_opencode_task_terminal"

/** Owns the foreground-service notification channel and content — kept out
 * of PresenceService itself so the service's lifecycle logic isn't tangled
 * with notification-building detail. */
class PresenceNotifications(private val context: Context) {

    fun ensureChannel(): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Jarvis companion status",
                NotificationManager.IMPORTANCE_LOW,
            )
            nm.createNotificationChannel(channel)
        }
        return CHANNEL_ID
    }

    fun build(
        paired: Boolean,
        state: ConnectionState,
        failureReason: DisconnectReason = DisconnectReason.NONE,
    ): Notification {
        val channelId = ensureChannel()
        val statusText = if (!paired) {
            "Not paired — tap to set up"
        } else when (state) {
            ConnectionState.CONNECTED -> "Connected to Jarvis"
            ConnectionState.CONNECTING -> "Connecting…"
            ConnectionState.RECONNECTING -> "Reconnecting…"
            ConnectionState.DISCONNECTED -> "Disconnected"
            // Milestone 9B.2 (ADR-014): TOKEN_EXPIRED/TOKEN_INVALID no
            // longer reach FAILED_PERMANENT at all — CompanionWebSocketClient
            // refreshes the token and retries automatically for those. Only
            // AUTH/CERTIFICATE (a genuine config/pairing problem) land here.
            ConnectionState.FAILED_PERMANENT -> when (failureReason) {
                DisconnectReason.CERTIFICATE -> "Server certificate changed — re-pair required"
                DisconnectReason.AUTH -> "Not authorized — re-pair required"
                else -> "Connection failed — re-pair required"
            }
        }
        val targetActivity = if (paired) ConnectionStatusActivity::class.java else SettingsActivity::class.java
        val contentIntent = PendingIntent.getActivity(
            context,
            0,
            Intent(context, targetActivity),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(context, channelId)
            .setContentTitle("Jarvis companion")
            .setContentText(statusText)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .build()
    }

    private fun ensureHandoffChannel(): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channel = NotificationChannel(
                HANDOFF_CHANNEL_ID,
                "Jarvis wake-word handoff",
                NotificationManager.IMPORTANCE_HIGH,
            )
            nm.createNotificationChannel(channel)
        }
        return HANDOFF_CHANNEL_ID
    }

    /** Milestone 9B.10: launches [VoiceActivity] via a full-screen intent
     * rather than a direct startActivity() call from PresenceService's
     * background context -- see HANDOFF_CHANNEL_ID's doc comment for why a
     * direct call is unreliable. VoiceActivity reads the already-opened
     * VoiceSession from the shared VoiceSessionRepository (populated when
     * the voice_session_opened frame arrived, before this notification is
     * built) rather than from an intent extra, same as the direct-startActivity
     * call this replaces. */
    fun buildWakeWordHandoff(): Notification {
        val channelId = ensureHandoffChannel()
        val activityIntent = Intent(context, VoiceActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            putExtra(VoiceActivity.EXTRA_LAUNCHED_BY_WAKEWORD, true)
        }
        val fullScreenIntent = PendingIntent.getActivity(
            context,
            WAKEWORD_HANDOFF_NOTIFICATION_ID,
            activityIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(context, channelId)
            .setContentTitle("Jarvis heard you")
            .setContentText("Listening…")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(fullScreenIntent)
            .setFullScreenIntent(fullScreenIntent, true)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_CALL)
            .build()
    }

    private fun ensureTaskTerminalChannel(): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channel = NotificationChannel(
                TASK_TERMINAL_CHANNEL_ID,
                "Jarvis task completed",
                NotificationManager.IMPORTANCE_DEFAULT,
            )
            nm.createNotificationChannel(channel)
        }
        return TASK_TERMINAL_CHANNEL_ID
    }

    /** Ongoing (not auto-cancel), reused low-priority channel — ambient
     * "still working" visibility for the whole task duration, updated in
     * place rather than re-notifying (see PresenceService's collector,
     * which calls this on every distinct RUNNING emission for the same
     * task, always with the same notification ID). */
    fun buildOpenCodeTaskRunning(instruction: String?): Notification {
        val channelId = ensureChannel()
        val summary = instruction?.take(80) ?: "Delegated task"
        val contentIntent = PendingIntent.getActivity(
            context, 0, Intent(context, ConnectionStatusActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(context, channelId)
            .setContentTitle("OpenCode task running…")
            .setContentText(summary)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .build()
    }

    /** status is the server's own "completed"/"failed" string (see
     * OpenCodeTaskParser) — anything else still renders, just without a
     * dedicated title, rather than silently dropping an unrecognized
     * future status. */
    fun buildOpenCodeTaskTerminal(status: String, instruction: String?): Notification {
        val channelId = ensureTaskTerminalChannel()
        val title = when (status) {
            "completed" -> "Task completed"
            "failed" -> "Task failed"
            else -> "Task finished: $status"
        }
        val summary = instruction?.take(80) ?: "Delegated task"
        val contentIntent = PendingIntent.getActivity(
            context, 0, Intent(context, ConnectionStatusActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(context, channelId)
            .setContentTitle(title)
            .setContentText(summary)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(contentIntent)
            .setAutoCancel(true)
            .build()
    }
}
