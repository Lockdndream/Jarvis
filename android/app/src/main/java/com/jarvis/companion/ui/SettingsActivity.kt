package com.jarvis.companion.ui

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.R
import com.jarvis.companion.databinding.ActivitySettingsBinding
import com.jarvis.companion.pairing.PairingConfig
import com.jarvis.companion.service.PresenceService
import com.jarvis.companion.settings.PermissionsHelper
import com.jarvis.companion.telemetry.TelemetryRecorder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Launcher screen. Combines pairing setup and the runtime-permission/
 * battery-exemption prompts this app needs, plus entry points to the
 * other two screens — three separate always-visible screens would be more
 * navigation than a foundation with this little UI surface needs.
 */
class SettingsActivity : AppCompatActivity() {
    private lateinit var binding: ActivitySettingsBinding
    private lateinit var app: JarvisCompanionApp

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { refreshStatus() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = applicationContext as JarvisCompanionApp
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.probeButton.setOnClickListener { onProbeClicked() }
        binding.unpairButton.setOnClickListener { onUnpairClicked() }
        binding.startServiceButton.setOnClickListener {
            ContextCompat.startForegroundService(this, Intent(this, PresenceService::class.java))
        }
        binding.stopServiceButton.setOnClickListener {
            stopService(Intent(this, PresenceService::class.java))
        }
        binding.grantNotificationPermissionButton.setOnClickListener {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                notificationPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        binding.requestBatteryExemptionButton.setOnClickListener {
            startActivity(PermissionsHelper.batteryOptimizationExemptionIntent(this))
        }
        binding.openConnectionStatusButton.setOnClickListener {
            startActivity(Intent(this, ConnectionStatusActivity::class.java))
        }
        binding.openDiagnosticsButton.setOnClickListener {
            startActivity(Intent(this, DiagnosticsActivity::class.java))
        }
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun refreshStatus() {
        val config = app.pairingRepository.get()
        val paired = config != null
        binding.pairedStatusText.text = if (config != null) {
            getString(R.string.paired_status_paired, config.host, config.port)
        } else {
            getString(R.string.paired_status_unpaired)
        }
        binding.pairingFormGroup.visibility = if (paired) View.GONE else View.VISIBLE
        binding.unpairButton.visibility = if (paired) View.VISIBLE else View.GONE

        val hasNotificationPermission = PermissionsHelper.hasNotificationPermission(this)
        binding.notificationPermissionStatusText.text = getString(
            if (hasNotificationPermission) R.string.notification_permission_status_granted
            else R.string.notification_permission_status_denied
        )
        binding.grantNotificationPermissionButton.visibility = if (hasNotificationPermission) View.GONE else View.VISIBLE

        val unrestricted = PermissionsHelper.isIgnoringBatteryOptimizations(this)
        binding.batteryStatusText.text = getString(
            if (unrestricted) R.string.battery_status_unrestricted else R.string.battery_status_restricted
        )
        binding.requestBatteryExemptionButton.visibility = if (unrestricted) View.GONE else View.VISIBLE
    }

    private fun onProbeClicked() {
        val host = binding.hostInput.text.toString().trim()
        val port = binding.portInput.text.toString().trim().toIntOrNull()
        val token = binding.tokenInput.text.toString()
        if (host.isEmpty() || port == null) return

        app.telemetry.record(TelemetryRecorder.PAIRING_PROBE_STARTED, "host=$host port=$port")
        lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) { app.pairingClient.probe(host, port) }
            } catch (e: Exception) {
                app.telemetry.record(
                    TelemetryRecorder.PAIRING_PROBE_FAILED,
                    "${e.javaClass.simpleName}:${e.message}",
                )
                AlertDialog.Builder(this@SettingsActivity)
                    .setMessage(getString(R.string.pairing_probe_failed, host, port, e.message ?: e.javaClass.simpleName))
                    .setPositiveButton(android.R.string.ok, null)
                    .show()
                return@launch
            }

            AlertDialog.Builder(this@SettingsActivity)
                .setTitle(R.string.pairing_confirm_title)
                .setMessage(getString(R.string.pairing_confirm_message, result.fingerprint))
                .setPositiveButton(R.string.pairing_confirm_button) { _, _ ->
                    val config = PairingConfig(
                        host = host,
                        port = port,
                        apiToken = token,
                        pinnedFingerprint = result.fingerprint,
                        pairedAtEpochMs = System.currentTimeMillis(),
                    )
                    app.pairingRepository.save(config)
                    app.telemetry.record(TelemetryRecorder.PAIRING_CONFIRMED, "host=$host port=$port")
                    refreshStatus()
                }
                .setNegativeButton(R.string.pairing_cancel_button, null)
                .show()
        }
    }

    private fun onUnpairClicked() {
        stopService(Intent(this, PresenceService::class.java))
        app.pairingRepository.clear()
        app.telemetry.record(TelemetryRecorder.PAIRING_CLEARED)
        refreshStatus()
    }
}
