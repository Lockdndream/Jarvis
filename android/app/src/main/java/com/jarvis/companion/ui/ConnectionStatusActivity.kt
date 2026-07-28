package com.jarvis.companion.ui

import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.R
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.databinding.ActivityConnectionStatusBinding
import com.jarvis.companion.opencode.OpenCodeTaskStatus
import com.jarvis.companion.settings.ConnectivityPolicy
import java.text.DateFormat
import java.util.Date
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/** Live view of PresenceService's current connection state, plus (Android
 * Companion Integration v1.0 / ADR-023 Phase 4) the connectivity policy
 * JOPS defines and this device enforces: current policy, connection
 * state, last settings sync, paired server, and current Wi-Fi status.
 * The policy itself is set from Jarvis Operations, not here — the one
 * control this screen adds is Connect/Disconnect, which only has meaning
 * in MODE_MANUAL ("remain disconnected until explicitly connected"). */
class ConnectionStatusActivity : AppCompatActivity() {
    private lateinit var binding: ActivityConnectionStatusBinding
    private lateinit var app: JarvisCompanionApp
    private val dateFormat: DateFormat = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.MEDIUM)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = applicationContext as JarvisCompanionApp
        binding = ActivityConnectionStatusBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val config = app.pairingRepository.get()
        binding.pairedHostText.text = if (config != null) "${config.host}:${config.port}" else ""

        binding.manualConnectButton.setOnClickListener { app.updateManualConnectRequested(true) }
        binding.manualDisconnectButton.setOnClickListener { app.updateManualConnectRequested(false) }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                combine(app.connectionState, app.connectivityMode, app.wifiAvailable, app.manualConnectRequested) {
                        state, mode, wifi, manualRequested ->
                    Quadruple(state, mode, wifi, manualRequested)
                }.collect { (state, mode, wifi, manualRequested) ->
                    binding.connectionStateText.text = getString(labelFor(config != null, state))
                    binding.policyModeText.text = getString(labelForMode(mode))
                    binding.wifiStatusText.text = getString(
                        if (wifi) R.string.wifi_status_on_wifi else R.string.wifi_status_not_on_wifi
                    )
                    val lastSyncedAtMs = app.operationalSettingsRepository.lastSyncedAtMs()
                    binding.lastSyncedText.text = if (lastSyncedAtMs != null) {
                        getString(R.string.last_synced_at, dateFormat.format(Date(lastSyncedAtMs)))
                    } else {
                        getString(R.string.last_synced_never)
                    }

                    val isManualMode = mode == ConnectivityPolicy.MODE_MANUAL
                    binding.manualConnectButton.visibility = if (isManualMode && !manualRequested) View.VISIBLE else View.GONE
                    binding.manualDisconnectButton.visibility = if (isManualMode && manualRequested) View.VISIBLE else View.GONE
                }
            }
        }

        // Interaction Layer v1 (Goals 2/3): the same lifecycle the
        // notification shows (PresenceService), rendered here too for
        // whenever this screen happens to be open — a second view of the
        // same repository, not a second source of truth.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                app.openCodeTaskRepository.current.collect { task ->
                    if (task == null) {
                        binding.taskStatusText.visibility = View.GONE
                        return@collect
                    }
                    binding.taskStatusText.visibility = View.VISIBLE
                    val label = when (task.status) {
                        OpenCodeTaskStatus.RUNNING -> getString(R.string.opencode_task_running)
                        OpenCodeTaskStatus.COMPLETED -> getString(R.string.opencode_task_completed)
                        OpenCodeTaskStatus.FAILED -> getString(R.string.opencode_task_failed)
                        else -> getString(R.string.opencode_task_other, task.status)
                    }
                    binding.taskStatusText.text = if (task.instruction != null) {
                        getString(R.string.opencode_task_with_summary, label, task.instruction)
                    } else {
                        label
                    }
                }
            }
        }
    }

    private fun labelFor(paired: Boolean, state: ConnectionState): Int {
        if (!paired) return R.string.connection_state_unpaired
        return when (state) {
            ConnectionState.CONNECTED -> R.string.connection_state_connected
            ConnectionState.CONNECTING -> R.string.connection_state_connecting
            ConnectionState.RECONNECTING -> R.string.connection_state_reconnecting
            ConnectionState.DISCONNECTED -> R.string.connection_state_disconnected
            ConnectionState.FAILED_PERMANENT -> R.string.connection_state_failed_permanent
        }
    }

    private fun labelForMode(mode: String): Int = when (mode) {
        ConnectivityPolicy.MODE_WIFI_ONLY -> R.string.connectivity_mode_wifi_only
        ConnectivityPolicy.MODE_MANUAL -> R.string.connectivity_mode_manual
        else -> R.string.connectivity_mode_always
    }

    private data class Quadruple<A, B, C, D>(val first: A, val second: B, val third: C, val fourth: D)
}
