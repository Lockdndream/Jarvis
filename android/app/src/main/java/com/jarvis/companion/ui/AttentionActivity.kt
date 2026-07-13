package com.jarvis.companion.ui

import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.R
import com.jarvis.companion.attention.AttentionRequest
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.databinding.ActivityAttentionBinding
import com.jarvis.companion.service.PresenceService
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

class AttentionActivity : AppCompatActivity() {
    private lateinit var binding: ActivityAttentionBinding
    private lateinit var app: JarvisCompanionApp

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = applicationContext as JarvisCompanionApp
        binding = ActivityAttentionBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.emptyStateText.text = getString(R.string.attention_empty)

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                combine(
                    app.attentionRepository.outstanding,
                    app.connectionState,
                ) { outstanding, connectionState ->
                    Pair(outstanding, connectionState)
                }.collect { (outstanding, connectionState) ->
                    render(outstanding, connectionState)
                }
            }
        }
    }

    private fun render(outstanding: List<AttentionRequest>, connectionState: ConnectionState) {
        val highlightId = intent.getStringExtra(EXTRA_ATTENTION_REQUEST_ID)

        binding.connectionStateText.text = when (connectionState) {
            ConnectionState.CONNECTED -> "Connected"
            ConnectionState.CONNECTING -> "Connecting\u2026"
            ConnectionState.RECONNECTING -> "Reconnecting\u2026"
            ConnectionState.DISCONNECTED -> "Not connected \u2014 dismiss unavailable"
            ConnectionState.FAILED_PERMANENT -> "Not connected \u2014 dismiss unavailable"
        }

        val container = binding.attentionListContainer
        container.removeAllViews()

        if (outstanding.isEmpty()) {
            binding.emptyStateText.visibility = View.VISIBLE
            return
        }

        binding.emptyStateText.visibility = View.GONE

        val isConnected = connectionState == ConnectionState.CONNECTED
        val inflater = layoutInflater

        for (request in outstanding) {
            val row = inflater.inflate(R.layout.item_attention_request, container, false)
            val summaryText = row.findViewById<TextView>(R.id.itemSummaryText)
            val dismissButton = row.findViewById<Button>(R.id.itemDismissButton)

            summaryText.text = "${request.attentionType}: ${request.summary ?: "(no summary)"}"

            dismissButton.isEnabled = isConnected
            dismissButton.setOnClickListener {
                PresenceService.activeClient?.sendAttentionCommand(
                    request.attentionRequestId,
                    "later",
                )
            }

            if (request.attentionRequestId == highlightId) {
                row.setBackgroundColor(Color.argb(30, 100, 149, 237))
            }

            container.addView(row)
        }
    }

    companion object {
        const val EXTRA_ATTENTION_REQUEST_ID = "com.jarvis.companion.EXTRA_ATTENTION_REQUEST_ID"
    }
}