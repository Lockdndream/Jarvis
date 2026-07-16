package com.jarvis.companion.service

import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.IBinder
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.network.CompanionWebSocketClient
import com.jarvis.companion.network.DeviceStatusSnapshot
import com.jarvis.companion.settings.PermissionsHelper
import com.jarvis.companion.telemetry.TelemetryRecorder
import com.jarvis.companion.widget.AttentionWidgetProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/**
 * Foreground service owning this app's one WebSocket connection to the
 * paired Jarvis server. No Jarvis reasoning, no business logic here — this
 * is presence/transport only, per Milestone 9B.1 scope.
 *
 * Lifecycle handling (onTaskRemoved not stopping the service,
 * onStartCommand's null-intent restart detection, START_STICKY) is carried
 * over unchanged from spikes/android-presence/PresenceService.kt, validated
 * on real hardware during Milestone 9B.0. Requesting exemption from Samsung
 * OEM battery optimization (Phase 3's root cause for connection gaps under
 * "Default" battery status) is handled by the Settings screen during setup,
 * not here — this service assumes it has already been granted.
 */
class PresenceService : Service() {

    private lateinit var app: JarvisCompanionApp
    private lateinit var telemetry: TelemetryRecorder
    private lateinit var notifications: PresenceNotifications
    private var connectionClient: CompanionWebSocketClient? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private lateinit var connectivityManager: ConnectivityManager
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            telemetry.record(TelemetryRecorder.NETWORK_AVAILABLE)
        }

        override fun onLost(network: Network) {
            telemetry.record(TelemetryRecorder.NETWORK_LOST)
        }
    }

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
        app = applicationContext as JarvisCompanionApp
        telemetry = app.telemetry
        notifications = PresenceNotifications(applicationContext)
        serviceCreatedAtMs = System.currentTimeMillis()
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

        // Milestone 9B.3 (ADR-015): push-driven widget refresh — the
        // widget's own updatePeriodMillis (Android's 30-minute floor) is
        // only a freshness safety net, this is the real update path.
        // AttentionWidgetProvider.requestUpdate reads a synchronous
        // snapshot itself, so this collector only needs to know *that*
        // something changed, not *what*.
        serviceScope.launch {
            combine(app.attentionRepository.outstanding, app.connectionState) { _, _ -> Unit }
                .collect { AttentionWidgetProvider.requestUpdate(applicationContext) }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // START_STICKY redelivers a null intent when Android recreates a
        // killed service on its own — the signal the survival protocol
        // needs to distinguish an OS-initiated restart from a real start.
        if (intent == null) {
            telemetry.record(TelemetryRecorder.SERVICE_RESTARTED_BY_OS)
        }
        telemetry.record(TelemetryRecorder.SERVICE_STARTED)

        val pairingConfig = app.pairingRepository.get()
        startForeground(PRESENCE_NOTIFICATION_ID, notifications.build(paired = pairingConfig != null, state = ConnectionState.DISCONNECTED))
        telemetry.record(TelemetryRecorder.FOREGROUND_ENTERED)

        if (pairingConfig != null && connectionClient == null) {
            val client = CompanionWebSocketClient(
                telemetry = telemetry,
                deviceId = app.deviceIdentity.get(),
                statusInputs = {
                    DeviceStatusSnapshot(
                        batteryOptimizationExempt = PermissionsHelper.isIgnoringBatteryOptimizations(applicationContext),
                        notificationPermissionGranted = PermissionsHelper.hasNotificationPermission(applicationContext),
                    )
                },
                attentionRepository = app.attentionRepository,
                voiceSessionRepository = app.voiceSessionRepository,
            )
            client.onStateChange = { state ->
                app.updateConnectionState(state)
                startForeground(
                    PRESENCE_NOTIFICATION_ID,
                    notifications.build(paired = true, state = state, failureReason = client.lastDisconnectReason),
                )
                telemetry.record(TelemetryRecorder.NOTIFICATION_POSTED, "state=$state")
            }
            connectionClient = client
            activeClient = client
            client.start(pairingConfig)
        }

        return START_STICKY
    }

    override fun onDestroy() {
        serviceScope.cancel()
        connectionClient?.stop()
        connectionClient = null
        activeClient = null
        serviceCreatedAtMs = null
        app.updateConnectionState(ConnectionState.DISCONNECTED)
        try {
            connectivityManager.unregisterNetworkCallback(networkCallback)
        } catch (_: IllegalArgumentException) {
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
        // Fires if the user swipes the app away from Recents — the service
        // is deliberately NOT stopped here; this only records that it
        // happened, matching the Milestone 9B.0 finding that the base
        // Service.onTaskRemoved() does not itself call stopSelf().
        telemetry.record(TelemetryRecorder.TASK_REMOVED)
        super.onTaskRemoved(rootIntent)
    }

    companion object {
        // In-process only (never cross-process — this app has no other
        // process) read access for the Diagnostics screen, which
        // deliberately does not bind to this service (see
        // diagnostics.DiagnosticsRepository's doc comment) but still needs
        // live connection-client state for its snapshot.
        @Volatile
        var activeClient: CompanionWebSocketClient? = null
            private set

        @Volatile
        var serviceCreatedAtMs: Long? = null
            private set
    }
}
