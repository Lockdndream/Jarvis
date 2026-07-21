package com.jarvis.companion.ui

import android.os.Bundle
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.audio.AudioFocusManager
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.databinding.ActivityVoiceBinding
import com.jarvis.companion.service.PresenceService
import com.jarvis.companion.settings.PermissionsHelper
import com.jarvis.companion.voice.AudioFocusOwner
import com.jarvis.companion.voice.PlaybackManager
import com.jarvis.companion.voice.SpeechInputController
import com.jarvis.companion.voice.VoiceSession
import com.jarvis.companion.voice.VoiceSessionState
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/**
 * Maps the server's internal [VoiceSessionState] strings to the five
 * user-facing labels the UI needs to display. The server has more granular
 * internal states than the UI shows — transient intermediary states
 * (opening, processing, deferred, closing) are all mapped to "waiting"
 * because from the user's perspective the system is working and they
 * should wait.
 *
 * null (no active session) maps to "finished".
 */
fun voiceSessionStateToUserFacingLabel(serverState: String?): String = when (serverState) {
    null -> "finished"
    VoiceSessionState.LISTENING -> "listening"
    VoiceSessionState.SPEAKING -> "speaking"
    VoiceSessionState.WAITING -> "waiting"
    VoiceSessionState.CLOSED -> "finished"
    VoiceSessionState.FAILED -> "error"
    else -> "waiting"
}

/**
 * Production voice screen for Milestone 9B.4. Displays the current
 * [VoiceSession] status, the latest spoken response, and a mic button.
 *
 * Owns its own [AudioFocusManager], [PlaybackManager], and
 * [SpeechInputController] instances — [PresenceService] is presence/
 * transport only and must not own audio/UI-adjacent resources.
 */
class VoiceActivity : AppCompatActivity() {
    private lateinit var binding: ActivityVoiceBinding
    private lateinit var app: JarvisCompanionApp
    private lateinit var audioFocusManager: AudioFocusManager
    private lateinit var playbackManager: PlaybackManager
    private lateinit var speechInputController: SpeechInputController

    private var pendingTranscript: String? = null
    private var lastSpokenText: String? = null
    private var lastSpokenSessionId: String? = null
    private var userFacingError: String? = null

    // Milestone 9B.10 RC finding: the server sends exactly one
    // voice_session_response per turn, delivered only after the whole
    // turn (LLM call, tool calls) already finished server-side -- there
    // is no intermediate "processing" push, so the client has no signal
    // at all during the wait. Purely local/client-side; never sent to or
    // read from the server.
    private var isAwaitingResponse = false

    // Milestone 9B.9: true once this screen has observed a real, opened
    // VoiceSession at least once. Distinguishes "the session I was
    // watching just closed" (finish — nothing left for this screen to do)
    // from "no session has opened yet" (the pre-mic-tap / awaiting-open
    // state every screen starts in, where session is also null but must
    // NOT trigger finish()).
    private var hadSession = false

    // Milestone 9B.9: read once in onCreate (intent extras don't change
    // across this Activity's lifetime — onNewIntent isn't overridden), so
    // onStop() doesn't need to re-parse the intent on every call.
    private var launchedByWakeWord = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = applicationContext as JarvisCompanionApp
        binding = ActivityVoiceBinding.inflate(layoutInflater)
        setContentView(binding.root)

        audioFocusManager = AudioFocusManager(this)
        audioFocusManager.start()

        playbackManager = PlaybackManager(this, AudioFocusOwnerAdapter(audioFocusManager))
        playbackManager.init()

        speechInputController = SpeechInputController(this)

        // Same-process-only static exposure for the Diagnostics screen,
        // mirroring PresenceService.activeClient's doc-commented rationale:
        // this Activity and Diagnostics always run in the same process, so
        // a Binder would add indirection without adding safety. Only valid
        // while this screen is actually alive — Diagnostics must treat
        // null as "voice screen not open" and render "n/a", not crash.
        activeAudioFocusManager = audioFocusManager
        activePlaybackManager = playbackManager
        activeSpeechInputController = speechInputController

        val attentionRequestId = intent.getStringExtra(EXTRA_ATTENTION_REQUEST_ID)
        if (attentionRequestId != null && app.voiceSessionRepository.current.value == null) {
            PresenceService.activeClient?.sendVoiceSessionOpen(
                conversationId = null,
                attentionRequestId = attentionRequestId,
            )
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                combine(
                    app.voiceSessionRepository.current,
                    app.voiceSessionRepository.lastResponse,
                    app.connectionState,
                ) { session, response, connectionState ->
                    Triple(session, response, connectionState)
                }.collect { (session, response, connectionState) ->
                    if (session != null) hadSession = true
                    // Milestone 9B.9 (ADR-017 Section C, Item 5): a
                    // wake-word-launched screen that never auto-closes
                    // after its session ends leaves an orphaned instance
                    // on the back stack — the next detection launches a
                    // second one on top instead of reusing this one. Same
                    // immediate-cutoff semantics as onClose()'s existing
                    // manual close (no grace period for in-flight TTS),
                    // just triggered by the session ending server-side
                    // instead of a button tap.
                    if (session == null && hadSession) {
                        finish()
                        return@collect
                    }
                    val pending = pendingTranscript
                    if (pending != null && session != null) {
                        pendingTranscript = null
                        PresenceService.activeClient?.sendVoiceSessionTranscript(
                            session.voiceSessionId, pending,
                        )
                    }
                    render(session, response, connectionState)
                }
            }
        }

        binding.micButton.setOnClickListener { onMicTap() }

        binding.closeButton.setOnClickListener { onClose() }

        // ADR-017 Section C: a wake-word-initiated launch means the user
        // just spoke a trigger phrase — start listening immediately rather
        // than waiting for a mic tap. savedInstanceState == null (not just
        // the intent extra) guards this so a configuration-change
        // recreation of this same Activity/Intent doesn't re-trigger a
        // second startListening() call over an already-listening session.
        launchedByWakeWord = intent.getBooleanExtra(EXTRA_LAUNCHED_BY_WAKEWORD, false)
        if (savedInstanceState == null && launchedByWakeWord) {
            onMicTap()
        }
    }

    override fun onPause() {
        super.onPause()
        speechInputController.cancel()
        playbackManager.cancel()
    }

    override fun onStop() {
        super.onStop()
        // Milestone 9B.9 (Item 5): a wake-word-launched conversation is a
        // one-shot interaction — if the user leaves this screen (Home, app
        // switch) without tapping Close, nothing else will ever close the
        // session, permanently blocking wake-word detection (a real,
        // 49-minute-long orphaned session found during real-device
        // validation — WakeWordManager stays PAUSED_VOICE_SESSION with no
        // VoiceActivity left to close it or ever call
        // resumeAfterVoiceSession()). isChangingConfigurations() excludes
        // rotation — the recreated instance must not have its session
        // pulled out from under it. !isFinishing excludes the case where
        // this onStop() is just the teardown from onClose() already
        // having called finish() — without it, a manual Close tap would
        // send a second, redundant voice_session_close as this screen
        // tears down. Manually-opened ("Talk to Jarvis") sessions are
        // deliberately excluded — backgrounding them still preserves the
        // conversation to return to, matching existing behavior exactly.
        if (!isFinishing && !isChangingConfigurations() && launchedByWakeWord &&
            app.voiceSessionRepository.current.value != null
        ) {
            onClose()
        }
    }

    override fun onDestroy() {
        if (activeAudioFocusManager === audioFocusManager) activeAudioFocusManager = null
        if (activePlaybackManager === playbackManager) activePlaybackManager = null
        if (activeSpeechInputController === speechInputController) activeSpeechInputController = null
        audioFocusManager.stop()
        playbackManager.shutdown()
        super.onDestroy()
    }

    private val recordAudioPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startListening() else showError("Microphone permission is required to talk to Jarvis")
        }

    private fun onMicTap() {
        if (!PermissionsHelper.hasRecordAudioPermission(this)) {
            recordAudioPermissionLauncher.launch(android.Manifest.permission.RECORD_AUDIO)
            return
        }
        startListening()
    }

    private fun startListening() {
        userFacingError = null
        speechInputController.startListening(
            onResult = { transcript ->
                val session = app.voiceSessionRepository.current.value
                if (session != null) {
                    PresenceService.activeClient?.sendVoiceSessionTranscript(
                        session.voiceSessionId, transcript,
                    )
                    isAwaitingResponse = true
                    render(
                        app.voiceSessionRepository.current.value,
                        app.voiceSessionRepository.lastResponse.value,
                        app.connectionState.value,
                    )
                } else {
                    val attentionRequestId = intent.getStringExtra(EXTRA_ATTENTION_REQUEST_ID)
                    pendingTranscript = transcript
                    PresenceService.activeClient?.sendVoiceSessionOpen(
                        conversationId = null,
                        attentionRequestId = attentionRequestId,
                    )
                }
            },
            onError = { message -> showError(message) },
        )
    }

    /** render() otherwise only runs from the session/response/connection
     * StateFlow combine() below — an error from SpeechInputController
     * doesn't touch any of those, so without this explicit call the error
     * would silently never reach the screen. */
    private fun showError(message: String) {
        userFacingError = message
        render(
            app.voiceSessionRepository.current.value,
            app.voiceSessionRepository.lastResponse.value,
            app.connectionState.value,
        )
    }

    private fun onClose() {
        val session = app.voiceSessionRepository.current.value
        if (session != null) {
            PresenceService.activeClient?.sendVoiceSessionClose(session.voiceSessionId)
        }
        playbackManager.cancel()
        pendingTranscript = null
        finish()
    }

    private fun render(
        session: VoiceSession?,
        response: String?,
        connectionState: ConnectionState,
    ) {
        binding.connectionStateText.text = when (connectionState) {
            ConnectionState.CONNECTED -> "Connected"
            ConnectionState.CONNECTING -> "Connecting\u2026"
            ConnectionState.RECONNECTING -> "Reconnecting\u2026"
            ConnectionState.DISCONNECTED -> "Not connected"
            ConnectionState.FAILED_PERMANENT -> "Connection failed"
        }

        if (userFacingError != null) {
            isAwaitingResponse = false
            binding.statusText.text = "error"
            binding.responseText.text = userFacingError
            binding.responseText.visibility = View.VISIBLE
            return
        }

        if (response != null && response != lastSpokenText) {
            isAwaitingResponse = false
        }

        binding.statusText.text = if (isAwaitingResponse) "thinking…" else voiceSessionStateToUserFacingLabel(session?.state)

        // The server never actually reports voice_session_state="speaking"
        // (app/voice_session_manager.py's real transition path only ever
        // sets listening/deferred) — speaking is a purely client-driven
        // fact, exactly like the PWA's own speakForVoiceSession(), which
        // speaks any new response text regardless of server-reported state.
        val sessionId = session?.voiceSessionId
        if (sessionId != null && sessionId != lastSpokenSessionId) {
            lastSpokenSessionId = sessionId
            lastSpokenText = null
            val greeting = session.greeting
            if (greeting != null) {
                lastSpokenText = greeting
                playbackManager.speak(greeting)
            }
        }
        if (response != null && response != lastSpokenText) {
            lastSpokenText = response
            playbackManager.speak(response)
        }

        // The turn response takes priority once one exists; before that, a
        // bound session's greeting is the only text the user has been
        // given yet, so show it rather than nothing.
        val displayText = response ?: session?.greeting
        if (displayText != null) {
            binding.responseText.text = displayText
            binding.responseText.visibility = View.VISIBLE
        } else {
            binding.responseText.visibility = View.GONE
        }
    }

    companion object {
        const val EXTRA_ATTENTION_REQUEST_ID = "com.jarvis.companion.EXTRA_VOICE_ATTENTION_REQUEST_ID"

        // Milestone 9B.9 (ADR-017 Section C): set by PresenceService's
        // confirmation-gated handoff when it launches this Activity after
        // a wake-word detection's VoiceSession open was confirmed by the
        // server — the VoiceSession already exists by the time this
        // Activity is created (see PresenceService.handleWakeWordDetection),
        // so unlike EXTRA_ATTENTION_REQUEST_ID this extra never triggers a
        // sendVoiceSessionOpen() call here; it only triggers auto-listening.
        const val EXTRA_LAUNCHED_BY_WAKEWORD = "com.jarvis.companion.EXTRA_VOICE_LAUNCHED_BY_WAKEWORD"

        // In-process only (this app has no other process), read access for
        // the Diagnostics screen — same rationale as
        // PresenceService.activeClient. Null whenever this screen isn't
        // currently alive; Diagnostics must render "n/a" in that case, not
        // crash or assume the voice feature is broken.
        @Volatile
        var activeAudioFocusManager: AudioFocusManager? = null
            private set

        @Volatile
        var activePlaybackManager: PlaybackManager? = null
            private set

        @Volatile
        var activeSpeechInputController: SpeechInputController? = null
            private set
    }
}

/**
 * Bridges [AudioFocusManager] (whose [AudioFocusManager.abandonFocus]
 * returns [Unit]) to [AudioFocusOwner] (whose [AudioFocusOwner.abandonFocus]
 * returns [Boolean]). The real method has no failure mode to report, so
 * this adapter always returns true.
 */
private class AudioFocusOwnerAdapter(
    private val audioFocusManager: AudioFocusManager,
) : AudioFocusOwner {
    override fun requestFocus(): Boolean = audioFocusManager.requestFocus()
    override fun abandonFocus(): Boolean {
        audioFocusManager.abandonFocus()
        return true
    }
}