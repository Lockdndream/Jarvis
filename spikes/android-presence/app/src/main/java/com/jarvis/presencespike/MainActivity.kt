package com.jarvis.presencespike

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.jarvis.presencespike.databinding.ActivityMainBinding

/**
 * Trivial launcher UI. No Jarvis reasoning, no navigation framework — just
 * Start/Stop for PresenceService and a live tail of its telemetry log, per
 * README.md's scope boundary.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var telemetry: TelemetryRecorder
    private val refreshHandler = Handler(Looper.getMainLooper())
    private val refreshRunnable = object : Runnable {
        override fun run() {
            refreshTelemetryView()
            refreshHandler.postDelayed(this, REFRESH_INTERVAL_MS)
        }
    }

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* no-op either way */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        telemetry = TelemetryRecorder(applicationContext)
        requestNotificationPermissionIfNeeded()

        binding.startButton.setOnClickListener {
            val intent = Intent(this, PresenceService::class.java)
            ContextCompat.startForegroundService(this, intent)
        }
        binding.stopButton.setOnClickListener {
            stopService(Intent(this, PresenceService::class.java))
        }
    }

    override fun onResume() {
        super.onResume()
        refreshHandler.post(refreshRunnable)
    }

    override fun onPause() {
        super.onPause()
        refreshHandler.removeCallbacks(refreshRunnable)
    }

    private fun refreshTelemetryView() {
        val lines = telemetry.tailLines(TAIL_LINE_COUNT)
        binding.telemetryText.text = if (lines.isEmpty()) {
            getString(R.string.no_telemetry_yet)
        } else {
            lines.joinToString("\n")
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    companion object {
        private const val REFRESH_INTERVAL_MS = 2_000L
        private const val TAIL_LINE_COUNT = 12
    }
}
