package com.jarvis.companion.settings

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.core.content.ContextCompat

/**
 * Runtime-permission and OEM battery-optimization checks the Settings
 * screen needs. Pure query/intent-building helpers — no UI here.
 *
 * Requesting "Unrestricted" battery status is not optional guidance: it is
 * the confirmed fix (Milestone 9B.0 Phase 3, real-device evidence) for the
 * Samsung "Default" battery policy silently dropping this app's WebSocket
 * connection for minutes at a time. See docs/decisions/ (Transport
 * Reachability is a separate, unrelated concern — do not conflate the two).
 */
object PermissionsHelper {
    fun hasNotificationPermission(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }

    fun isIgnoringBatteryOptimizations(context: Context): Boolean {
        val powerManager = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return powerManager.isIgnoringBatteryOptimizations(context.packageName)
    }

    /** Launches the system dialog that lets the user grant "Unrestricted"
     * battery status directly (no manual Settings navigation required). */
    fun batteryOptimizationExemptionIntent(context: Context): Intent {
        return Intent(
            Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
            Uri.parse("package:${context.packageName}"),
        )
    }
}
