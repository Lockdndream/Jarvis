package com.jarvis.companion.ui

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.appcompat.app.AppCompatActivity
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.R
import com.jarvis.companion.databinding.ActivityDiagnosticsBinding
import com.jarvis.companion.diagnostics.DiagnosticsRepository
import com.jarvis.companion.diagnostics.buildVoiceDiagnostics
import com.jarvis.companion.diagnostics.buildWakeWordDiagnostics

private const val REFRESH_INTERVAL_MS = 2_000L
private const val TAIL_LINE_COUNT = 200

/** Engineering diagnostics: a live state snapshot (uptime, reconnect
 * count, last disconnect reason, generation, heartbeat timing, pinned
 * fingerprint — Milestone 9B.2) plus the telemetry tail. Read-only, no
 * controls — the same polling pattern validated in
 * spikes/android-presence/MainActivity.kt. */
class DiagnosticsActivity : AppCompatActivity() {
    private lateinit var binding: ActivityDiagnosticsBinding
    private lateinit var diagnostics: DiagnosticsRepository
    private val refreshHandler = Handler(Looper.getMainLooper())
    private val refreshRunnable = object : Runnable {
        override fun run() {
            refresh()
            refreshHandler.postDelayed(this, REFRESH_INTERVAL_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = applicationContext as JarvisCompanionApp
        diagnostics = DiagnosticsRepository(app.telemetry, app.pairingRepository)
        binding = ActivityDiagnosticsBinding.inflate(layoutInflater)
        setContentView(binding.root)
    }

    override fun onResume() {
        super.onResume()
        refreshHandler.post(refreshRunnable)
    }

    override fun onPause() {
        super.onPause()
        refreshHandler.removeCallbacks(refreshRunnable)
    }

    private fun refresh() {
        binding.snapshotText.text = diagnostics.snapshot().formatted()
        val app = applicationContext as JarvisCompanionApp
        val session = app.voiceSessionRepository.current.value
        val voice = buildVoiceDiagnostics(
            audioFocusState = VoiceActivity.activeAudioFocusManager?.focusState?.value?.name,
            audioRoute = VoiceActivity.activeAudioFocusManager?.currentRoute?.value?.name,
            ttsReady = VoiceActivity.activePlaybackManager?.isReady?.value,
            ttsSpeaking = VoiceActivity.activePlaybackManager?.isSpeaking?.value,
            voiceSessionState = session?.state,
            voiceSessionId = session?.voiceSessionId,
            playbackQueueDepth = VoiceActivity.activePlaybackManager?.queueDepth,
            speechInputState = VoiceActivity.activeSpeechInputController?.state?.value?.name,
            lastVoiceEventAtMs = null,
            lastTerminationReason = app.voiceSessionRepository.lastTerminationReason.value,
        )
        binding.voiceText.text = voice.formatted()
        binding.wakeWordText.text = buildWakeWordDiagnostics().formatted()
        val lines = diagnostics.tail(TAIL_LINE_COUNT)
        binding.telemetryText.text = if (lines.isEmpty()) {
            getString(R.string.diagnostics_no_telemetry_yet)
        } else {
            lines.joinToString("\n")
        }
    }
}
