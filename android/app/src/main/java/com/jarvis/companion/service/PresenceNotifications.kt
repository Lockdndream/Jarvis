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

private const val CHANNEL_ID = "jarvis_companion_status"
const val PRESENCE_NOTIFICATION_ID = 1001

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
}
