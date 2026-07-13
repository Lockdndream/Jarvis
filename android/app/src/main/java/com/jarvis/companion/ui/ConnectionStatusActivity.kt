package com.jarvis.companion.ui

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.R
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.databinding.ActivityConnectionStatusBinding
import kotlinx.coroutines.launch

/** Live view of PresenceService's current connection state — no controls,
 * no business logic, just the current state and paired host. */
class ConnectionStatusActivity : AppCompatActivity() {
    private lateinit var binding: ActivityConnectionStatusBinding
    private lateinit var app: JarvisCompanionApp

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = applicationContext as JarvisCompanionApp
        binding = ActivityConnectionStatusBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val config = app.pairingRepository.get()
        binding.pairedHostText.text = if (config != null) "${config.host}:${config.port}" else ""

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                app.connectionState.collect { state ->
                    binding.connectionStateText.text = getString(labelFor(config != null, state))
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
}
