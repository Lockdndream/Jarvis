package com.jarvis.companion.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.view.View
import android.widget.RemoteViews
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.R
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.service.PresenceService
import com.jarvis.companion.ui.AttentionActivity

class AttentionWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        for (appWidgetId in appWidgetIds) {
            appWidgetManager.updateAppWidget(appWidgetId, buildRemoteViews(context, appWidgetId))
        }
    }

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            ACTION_DISMISS -> {
                val id = intent.getStringExtra(AttentionActivity.EXTRA_ATTENTION_REQUEST_ID)
                if (id != null) {
                    PresenceService.activeClient?.sendAttentionCommand(id, "later")
                }
            }
            ACTION_REFRESH -> requestUpdate(context)
            else -> super.onReceive(context, intent)
        }
    }

    companion object {
        fun requestUpdate(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, AttentionWidgetProvider::class.java))
            for (id in ids) {
                manager.updateAppWidget(id, buildRemoteViews(context, id))
            }
        }
    }
}

private fun buildRemoteViews(context: Context, appWidgetId: Int): RemoteViews {
    val views = RemoteViews(context.packageName, R.layout.widget_attention)
    val app = context.applicationContext as JarvisCompanionApp

    val connectionState = app.connectionState.value
    val outstanding = app.attentionRepository.outstanding.value
    val lastContactAtMs = app.attentionRepository.lastContactAtMs.value

    views.setTextViewText(R.id.connectionStateText, when (connectionState) {
        ConnectionState.CONNECTED -> "Connected"
        ConnectionState.CONNECTING -> "Connecting\u2026"
        ConnectionState.RECONNECTING -> "Reconnecting\u2026"
        ConnectionState.DISCONNECTED -> "Disconnected"
        ConnectionState.FAILED_PERMANENT -> "Not connected \u2014 re-pair required"
    })

    if (outstanding.isEmpty()) {
        views.setTextViewText(R.id.bodyText, "No outstanding items")
        views.setViewVisibility(R.id.dismissButton, View.GONE)
    } else {
        val first = outstanding.first()
        views.setTextViewText(
            R.id.bodyText,
            "${outstanding.size} outstanding \u2014 ${first.attentionType}: ${first.summary ?: "(no summary)"}"
        )
        views.setViewVisibility(R.id.dismissButton, View.VISIBLE)
    }

    views.setTextViewText(R.id.lastContactText, relativeTime(lastContactAtMs))

    val openIntent = Intent(context, AttentionActivity::class.java).apply {
        data = Uri.parse("widget://open/$appWidgetId")
        if (outstanding.isNotEmpty()) {
            putExtra(AttentionActivity.EXTRA_ATTENTION_REQUEST_ID, outstanding.first().attentionRequestId)
        }
    }
    views.setOnClickPendingIntent(
        R.id.widgetRoot,
        // FLAG_UPDATE_CURRENT: PendingIntent identity is matched on
        // action/data/component, not extras — without this flag a cached
        // PendingIntent from an earlier render keeps its *original*
        // extras (a stale attentionRequestId) forever.
        PendingIntent.getActivity(
            context, 0, openIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    )

    val dismissIntent = Intent(context, AttentionWidgetProvider::class.java).apply {
        action = ACTION_DISMISS
        data = Uri.parse("widget://dismiss/$appWidgetId")
        if (outstanding.isNotEmpty()) {
            putExtra(AttentionActivity.EXTRA_ATTENTION_REQUEST_ID, outstanding.first().attentionRequestId)
        }
    }
    views.setOnClickPendingIntent(
        R.id.dismissButton,
        PendingIntent.getBroadcast(
            context, 0, dismissIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    )

    val refreshIntent = Intent(context, AttentionWidgetProvider::class.java).apply {
        action = ACTION_REFRESH
        data = Uri.parse("widget://refresh/$appWidgetId")
    }
    views.setOnClickPendingIntent(
        R.id.refreshButton,
        PendingIntent.getBroadcast(
            context, 0, refreshIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    )

    return views
}

private const val ACTION_DISMISS = "com.jarvis.companion.widget.ACTION_DISMISS"
private const val ACTION_REFRESH = "com.jarvis.companion.widget.ACTION_REFRESH"

private fun relativeTime(lastContactAtMs: Long?): String {
    if (lastContactAtMs == null) return "never"
    val elapsed = System.currentTimeMillis() - lastContactAtMs
    return when {
        elapsed < 60_000L -> "just now"
        elapsed < 3_600_000L -> "${elapsed / 60_000L}m ago"
        else -> "${elapsed / 3_600_000L}h ago"
    }
}