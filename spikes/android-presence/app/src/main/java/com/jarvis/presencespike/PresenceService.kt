package com.jarvis.presencespike

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import android.os.IBinder

/**
 * Android-13-correct foreground service that owns the test WebSocket
 * transport for the presence-survival spike. No Jarvis reasoning here —
 * this is a dumb presence probe, per README.md's scope boundary.
 */
class PresenceService : Service() {

    private lateinit var telemetry: TelemetryRecorder
    private var connectionClient: TestConnectionClient? = null
    private var connected = false

    private lateinit var connectivityManager: ConnectivityManager
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            telemetry.record(TelemetryRecorder.NETWORK_AVAILABLE)
        }

        override fun onLost(network: Network) {
            telemetry.record(TelemetryRecorder.NETWORK_LOST)
        }
    }

    // Legitimately observable via the standard system broadcasts — never
    // inferred from anything indirect.
    private val screenStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                Intent.ACTION_SCREEN_OFF -> telemetry.record(TelemetryRecorder.SCREEN_OFF_OBSERVED)
                Intent.ACTION_SCREEN_ON -> telemetry.record(TelemetryRecorder.SCREEN_ON_OBSERVED)
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        telemetry = TelemetryRecorder(applicationContext)
        telemetry.record(TelemetryRecorder.SERVICE_CREATED)

        connectivityManager = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        connectivityManager.registerNetworkCallback(request, networkCallback)

        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_SCREEN_ON)
        }
        registerReceiver(screenStateReceiver, filter)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // START_STICKY redelivers a null intent when Android recreates a
        // killed service on its own — distinguishing this from a real
        // user-triggered start is exactly the "did the OS kill/restart the
        // service" signal the survival protocol needs, not something to
        // infer indirectly from a process-state poll alone.
        if (intent == null) {
            telemetry.record(TelemetryRecorder.SERVICE_RESTARTED_BY_OS)
        }
        telemetry.record(TelemetryRecorder.SERVICE_STARTED)
        startForeground(NOTIFICATION_ID, buildNotification(connected = false))
        telemetry.record(TelemetryRecorder.FOREGROUND_ENTERED)
        telemetry.record(TelemetryRecorder.NOTIFICATION_POSTED, "connected=false (initial)")

        if (connectionClient == null) {
            connectionClient = TestConnectionClient(telemetry = telemetry) { isConnected ->
                connected = isConnected
                updateNotification(isConnected)
            }
            connectionClient?.connect()
        }

        // START_STICKY: if the system kills this process under memory
        // pressure, ask it to recreate the service (without redelivering
        // the last intent) — the spike wants to observe this, not fight it
        // with a stronger restart guarantee that would mask the real answer.
        return START_STICKY
    }

    override fun onDestroy() {
        connectionClient?.disconnect()
        connectionClient = null
        try {
            connectivityManager.unregisterNetworkCallback(networkCallback)
        } catch (_: IllegalArgumentException) {
            // Already unregistered — harmless on this disposable spike.
        }
        try {
            unregisterReceiver(screenStateReceiver)
        } catch (_: IllegalArgumentException) {
        }
        telemetry.record(TelemetryRecorder.SERVICE_DESTROYED)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Fires if the user swipes the app away from Recents — a real,
        // common real-world event distinct from an explicit Stop tap or an
        // OS-initiated low-memory kill. The service is NOT stopped here;
        // this only records that it happened, so the survival protocol can
        // tell the three cases apart in the log.
        telemetry.record(TelemetryRecorder.TASK_REMOVED)
        super.onTaskRemoved(rootIntent)
    }

    private fun buildNotification(connected: Boolean): Notification {
        val channelId = ensureNotificationChannel()
        val status = if (connected) "connected" else "connecting…"
        return Notification.Builder(this, channelId)
            .setContentTitle("Jarvis presence spike")
            .setContentText("Test transport: $status")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(connected: Boolean) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIFICATION_ID, buildNotification(connected))
        // Phase 2: an explicit record of when the notification's content
        // last changed, independent of asking the user to visually check
        // it — makes "was the notification actually still being updated"
        // answerable from the log alone.
        telemetry.record(TelemetryRecorder.NOTIFICATION_POSTED, "connected=$connected")
    }

    private fun ensureNotificationChannel(): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Presence spike status",
                NotificationManager.IMPORTANCE_LOW,
            )
            nm.createNotificationChannel(channel)
        }
        return CHANNEL_ID
    }

    companion object {
        private const val CHANNEL_ID = "presence_spike_status"
        private const val NOTIFICATION_ID = 1001
    }
}
