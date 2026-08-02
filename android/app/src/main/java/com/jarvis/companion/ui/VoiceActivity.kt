package com.jarvis.companion.ui

import android.os.Bundle
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import com.jarvis.companion.JarvisCompanionApp
import com.jarvis.companion.audio.AudioFocusManager
import com.jarvis.companion.conversation.ConversationMessage
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.databinding.ActivityVoiceBinding
import com.jarvis.companion.service.PresenceService
import com.jarvis.companion.settings.PermissionsHelper
import com.jarvis.companion.voice.AndroidAudioCaptureEngine
import com.jarvis.companion.voice.AudioFocusOwner
import com.jarvis.companion.voice.PlaybackManager
import com.jarvis.companion.voice.SpeechInputController
import com.jarvis.companion.voice.VoiceSession
import com.jarvis.companion.voice.VoiceSessionState
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

private data class VoiceScreenSnapshot(
    val session: VoiceSession?,
    val response: String?,
    val connectionState: ConnectionState,
    val isSpeaking: Boolean,
)

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
    VoiceSessionState.CONFIRMING -> "confirming"
    VoiceSessionState.CLOSED -> "finished"
    VoiceSessionState.FAILED -> "error"
    else -> "waiting"
}

/**
 * Interaction Layer v1 (Goal 1): pure decision for whether a spoken
 * response finishing should automatically resume listening for a
 * follow-up turn, extracted so the gating logic is exhaustively
 * unit-testable without a real TTS engine or SpeechRecognizer. The actual
 * mic start (onMicTap()/startListening()) stays in VoiceActivity — this
 * only answers "should we", matching the same decision/action split used
 * for connectivity-policy enforcement.
 *
 * True only on the isSpeaking true->false *edge* (never re-fires while
 * already false, so this can't loop) while the server has signaled it's
 * the user's turn — LISTENING for an ordinary turn, or CONFIRMING for a
 * Goal 5 yes/no read-back (both are legal predecessors of PROCESSING
 * server-side; without CONFIRMING here, Goal 5 would silently regress
 * Goal 1's hands-free promise for exactly its own confirmation replies)
 * — there's no active error on screen, and the connection is up.
 */
fun shouldAutoResumeListening(
    wasSpeaking: Boolean,
    isSpeaking: Boolean,
    sessionState: String?,
    hasUserFacingError: Boolean,
    connectionState: ConnectionState,
): Boolean {
    if (!wasSpeaking || isSpeaking) return false
    if (sessionState != VoiceSessionState.LISTENING && sessionState != VoiceSessionState.CONFIRMING) return false
    if (hasUserFacingError) return false
    return connectionState == ConnectionState.CONNECTED
}

enum class VoiceScreenState { IDLE, LISTENING, REVIEWING, PROCESSING, RESPONDING }

sealed class VoiceScreenEvent {
    object MicTapped : VoiceScreenEvent()
    object StopTapped : VoiceScreenEvent()
    data class FinalTranscriptReceived(val transcript: String) : VoiceScreenEvent()
    object EmptyTranscriptReceived : VoiceScreenEvent()
    object SendTapped : VoiceScreenEvent()
    object ReRecordTapped : VoiceScreenEvent()
    object ClearTapped : VoiceScreenEvent()
    object ReviewTimedOut : VoiceScreenEvent()
    object ResponseReceived : VoiceScreenEvent()
    object TtsFinished : VoiceScreenEvent()
    object CancelledOrBackgrounded : VoiceScreenEvent()
    object TextTyped : VoiceScreenEvent()
}

fun nextVoiceScreenState(
    current: VoiceScreenState,
    event: VoiceScreenEvent,
    continuousConversationActive: Boolean = false,
): VoiceScreenState = when (current) {
    VoiceScreenState.IDLE -> when (event) {
        VoiceScreenEvent.MicTapped -> VoiceScreenState.LISTENING
        VoiceScreenEvent.TextTyped -> VoiceScreenState.REVIEWING
        else -> current
    }
    VoiceScreenState.LISTENING -> when (event) {
        VoiceScreenEvent.StopTapped -> VoiceScreenState.REVIEWING
        is VoiceScreenEvent.FinalTranscriptReceived -> VoiceScreenState.REVIEWING
        VoiceScreenEvent.EmptyTranscriptReceived -> VoiceScreenState.IDLE
        VoiceScreenEvent.CancelledOrBackgrounded -> VoiceScreenState.IDLE
        else -> current
    }
    VoiceScreenState.REVIEWING -> when (event) {
        VoiceScreenEvent.SendTapped -> VoiceScreenState.PROCESSING
        VoiceScreenEvent.ReRecordTapped -> VoiceScreenState.LISTENING
        VoiceScreenEvent.ClearTapped -> VoiceScreenState.IDLE
        VoiceScreenEvent.ReviewTimedOut -> VoiceScreenState.IDLE
        else -> current
    }
    VoiceScreenState.PROCESSING -> when (event) {
        VoiceScreenEvent.ResponseReceived -> VoiceScreenState.RESPONDING
        else -> current
    }
    VoiceScreenState.RESPONDING -> when (event) {
        VoiceScreenEvent.TtsFinished -> if (continuousConversationActive) VoiceScreenState.LISTENING else VoiceScreenState.IDLE
        VoiceScreenEvent.MicTapped -> VoiceScreenState.LISTENING
        else -> current
    }
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
    private lateinit var conversationAdapter: ConversationAdapter

    private var pendingTranscript: String? = null
    private var pendingAudioBytes: ByteArray? = null
    private var lastSpokenText: String? = null
    private var lastSpokenSessionId: String? = null
    private var userFacingError: String? = null
    private var pendingLiveTranscriptId: String? = null

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

    // Interaction Layer v1 (Goal 1): tracks the previous isSpeaking value
    // so the auto-resume-listening logic below can detect the true->false
    // edge (TTS just finished) rather than firing on every collector
    // emission where isSpeaking happens to already be false.
    private var wasSpeaking = false

    // Push-to-talk state machine field, updated via transition() only.
    private var screenState: VoiceScreenState = VoiceScreenState.IDLE

    // 60-second review-abandonment timeout: if the user leaves a transcript
    // in the review field without editing or sending for 60s, discard it.
    private val reviewTimeoutHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val reviewTimeoutRunnable = Runnable {
        if (screenState == VoiceScreenState.REVIEWING) {
            binding.reviewEditText.setText("")
            transition(VoiceScreenEvent.ReviewTimedOut)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = applicationContext as JarvisCompanionApp
        binding = ActivityVoiceBinding.inflate(layoutInflater)
        setContentView(binding.root)

        audioFocusManager = AudioFocusManager(this)
        audioFocusManager.start()

        playbackManager = PlaybackManager(this, AudioFocusOwnerAdapter(audioFocusManager))
        playbackManager.init()

        speechInputController = SpeechInputController(this, AndroidAudioCaptureEngine())

        conversationAdapter = ConversationAdapter()
        binding.conversationRecyclerView.layoutManager = LinearLayoutManager(this)
        binding.conversationRecyclerView.adapter = conversationAdapter

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                app.conversationRepository.messages.collect { messages ->
                    conversationAdapter.submitList(messages) {
                        if (messages.isNotEmpty()) {
                            binding.conversationRecyclerView.scrollToPosition(messages.size - 1)
                        }
                    }
                }
            }
        }

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
                    playbackManager.isSpeaking,
                ) { session, response, connectionState, isSpeaking ->
                    VoiceScreenSnapshot(session, response, connectionState, isSpeaking)
                }.collect { (session, response, connectionState, isSpeaking) ->
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
                    val pendingAudio = pendingAudioBytes
                    if (pendingAudio != null && session != null) {
                        pendingAudioBytes = null
                        PresenceService.activeClient?.sendVoiceSessionAudio(
                            session.voiceSessionId, pendingAudio,
                        )
                    }
                    if (response != null && response != lastSpokenText && screenState == VoiceScreenState.PROCESSING) {
                        transition(VoiceScreenEvent.ResponseReceived)
                    }
                    render(session, response, connectionState)

                    // Interaction Layer v1 (Goal 1): once a spoken response
                    // finishes playing, automatically resume listening for
                    // a follow-up turn instead of requiring a manual mic
                    // tap every turn (the real friction found during
                    // capability testing). Gated on the isSpeaking
                    // true->false *edge*, not on response arrival — TTS is
                    // still audible for a few seconds after the response
                    // text arrives, and starting the mic while Jarvis is
                    // still talking would just capture its own voice.
                    // Also gated on session.state == LISTENING (the
                    // server's own "your turn" signal — never fires while
                    // closing/failed/deferred) and on no active error, so
                    // a failed turn requires an explicit retry tap rather
                    // than silently retrying forever.
                    // onMicTap() -> startListening() is itself a no-op
                    // unless SpeechInputController is IDLE, so this can
                    // never double-start a session already listening from
                    // the wake-word-launch path.
                    // Push-to-talk (Step 3): the transition is now driven
                    // by the screen state machine — TtsFinished on the
                    // RESPONDING state decides whether to go to LISTENING
                    // or IDLE based on continuousConversationActive.
                    if (wasSpeaking && !isSpeaking && screenState == VoiceScreenState.RESPONDING) {
                        val continuousActive = shouldAutoResumeListening(
                            wasSpeaking = wasSpeaking,
                            isSpeaking = isSpeaking,
                            sessionState = session?.state,
                            hasUserFacingError = userFacingError != null,
                            connectionState = connectionState,
                        )
                        transition(VoiceScreenEvent.TtsFinished, continuousActive)
                        if (screenState == VoiceScreenState.LISTENING) {
                            onMicTap()
                        }
                    }
                    wasSpeaking = isSpeaking
                }
            }
        }

        binding.micButton.setOnClickListener {
            when (screenState) {
                VoiceScreenState.LISTENING -> speechInputController.stopListening()
                // The mic button stays visible (and labeled "Mic") during
                // REVIEWING; without this branch, tapping it silently
                // restarted capture in the background -- screenState never
                // left REVIEWING (no matching table entry), so the old
                // transcript stayed on screen with no "Stop" affordance
                // until the new result overwrote it. Route it through the
                // same discard-and-restart path as the Re-record button,
                // which is what the original spec calls for.
                VoiceScreenState.REVIEWING -> {
                    binding.reviewEditText.setText("")
                    cancelReviewTimeout()
                    transition(VoiceScreenEvent.ReRecordTapped)
                    startListening()
                }
                else -> onMicTap()
            }
        }

        binding.closeButton.setOnClickListener { onClose() }

        binding.sendButton.setOnClickListener {
            val text = binding.reviewEditText.text?.toString()?.trim().orEmpty()
            if (text.isEmpty()) return@setOnClickListener

            cancelReviewTimeout()

            app.conversationRepository.addMessage(
                ConversationMessage(
                    id = java.util.UUID.randomUUID().toString(),
                    type = ConversationMessage.Type.USER_MESSAGE,
                    content = text,
                    status = ConversationMessage.Status.COMPLETED,
                    timestamp = System.currentTimeMillis(),
                )
            )

            val session = app.voiceSessionRepository.current.value
            if (session != null) {
                PresenceService.activeClient?.sendVoiceSessionTranscript(session.voiceSessionId, text)
                isAwaitingResponse = true
            } else {
                pendingTranscript = text
                PresenceService.activeClient?.sendVoiceSessionOpen(
                    conversationId = null,
                    attentionRequestId = intent.getStringExtra(EXTRA_ATTENTION_REQUEST_ID),
                )
            }

            binding.reviewEditText.setText("")
            transition(VoiceScreenEvent.SendTapped)
            render(
                app.voiceSessionRepository.current.value,
                app.voiceSessionRepository.lastResponse.value,
                app.connectionState.value,
            )
        }

        binding.reRecordButton.setOnClickListener {
            binding.reviewEditText.setText("")
            cancelReviewTimeout()
            transition(VoiceScreenEvent.ReRecordTapped)
            startListening()
        }

        binding.clearReviewButton.setOnClickListener {
            binding.reviewEditText.setText("")
            cancelReviewTimeout()
            transition(VoiceScreenEvent.ClearTapped)
        }

        binding.reviewEditText.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                startReviewTimeout()
            }
            override fun afterTextChanged(s: android.text.Editable?) {}
        })

        binding.typeInsteadButton.setOnClickListener {
            binding.reviewEditText.setText("")
            transition(VoiceScreenEvent.TextTyped)
            // setText("") on an already-empty field may not reliably fire
            // the TextWatcher above, so arm the abandonment timeout
            // explicitly here too -- a user who taps "Type instead" and
            // then walks away without typing anything should still get
            // auto-discarded back to IDLE after 60s, same as the voice path.
            startReviewTimeout()
            binding.reviewEditText.requestFocus()
            val imm = getSystemService(android.content.Context.INPUT_METHOD_SERVICE)
                as android.view.inputmethod.InputMethodManager
            imm.showSoftInput(binding.reviewEditText, android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT)
        }

        applyScreenState()

        launchedByWakeWord = intent.getBooleanExtra(EXTRA_LAUNCHED_BY_WAKEWORD, false)
        if (savedInstanceState == null && launchedByWakeWord) {
            onMicTap()
        }
    }

    override fun onPause() {
        super.onPause()
        cancelReviewTimeout()
        val wasCapturing = speechInputController.state.value == SpeechInputController.State.LISTENING ||
            speechInputController.state.value == SpeechInputController.State.PROCESSING
        speechInputController.cancel()
        playbackManager.cancel()
        if (wasCapturing) {
            audioFocusManager.abandonFocus()
            clearPendingLiveTranscript()
            transition(VoiceScreenEvent.CancelledOrBackgrounded)
            app.conversationRepository.addMessage(
                ConversationMessage(
                    id = java.util.UUID.randomUUID().toString(),
                    type = ConversationMessage.Type.SYSTEM_EVENT,
                    content = "Listening was interrupted — please repeat that.",
                    timestamp = System.currentTimeMillis(),
                )
            )
        }
        if (screenState == VoiceScreenState.REVIEWING) {
            binding.reviewEditText.setText("")
            screenState = VoiceScreenState.IDLE
            applyScreenState()
        }
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
        // Barge-in: if Jarvis is currently speaking, cancel TTS (which
        // releases focus) before re-requesting it, to avoid the
        // request/abandon ordering fighting itself.
        if (playbackManager.isSpeaking.value) {
            playbackManager.cancel()
        }
        // Real-device finding: WakeWordManager only pauses via
        // pauseForVoiceSession() (which requires a VoiceSession to already
        // exist) or pauseForAudioFocusLoss() (triggered by a competing
        // AUDIOFOCUS_GAIN request). A manual mic tap with no existing
        // session previously satisfied neither -- WakeWordManager's own
        // AudioRecord kept running and starved SpeechRecognizer of audio,
        // reproducing the same "onReadyForSpeech then silence then
        // NO_MATCH ~5s later" signature found during the parallel-capture
        // investigation. Requesting focus here, unconditionally, before
        // either capture path starts, guarantees WakeWordManager's own
        // focus-loss handler pauses it regardless of session state.
        audioFocusManager.requestFocus()
        if (speechInputController.isRecognitionAvailable()) {
            speechInputController.startListening(
                onResult = { transcript ->
                    clearPendingLiveTranscript()
                    binding.reviewEditText.setText(transcript)
                    binding.reviewEditText.setSelection(binding.reviewEditText.text?.length ?: 0)
                    transition(VoiceScreenEvent.FinalTranscriptReceived(transcript))
                    startReviewTimeout()
                },
                onError = { message -> showError(message) },
                onPartialResult = { text ->
                    val id = pendingLiveTranscriptId
                    if (id != null) {
                        app.conversationRepository.updateMessage(id, content = text, status = ConversationMessage.Status.STARTED)
                    }
                },
                onBeginningOfSpeech = {
                    val id = java.util.UUID.randomUUID().toString()
                    pendingLiveTranscriptId = id
                    app.conversationRepository.addMessage(
                        ConversationMessage(
                            id = id,
                            type = ConversationMessage.Type.USER_MESSAGE,
                            content = "",
                            status = ConversationMessage.Status.STARTED,
                            timestamp = System.currentTimeMillis(),
                        )
                    )
                },
                onEmptyResult = {
                    clearPendingLiveTranscript()
                    transition(VoiceScreenEvent.EmptyTranscriptReceived)
                },
            )
            transition(VoiceScreenEvent.MicTapped)
        } else {
            speechInputController.startListeningRaw(
                onAudioCaptured = { audioBytes ->
                    val session = app.voiceSessionRepository.current.value
                    if (session != null) {
                        PresenceService.activeClient?.sendVoiceSessionAudio(session.voiceSessionId, audioBytes)
                        isAwaitingResponse = true
                        render(
                            app.voiceSessionRepository.current.value,
                            app.voiceSessionRepository.lastResponse.value,
                            app.connectionState.value,
                        )
                    } else {
                        val attentionRequestId = intent.getStringExtra(EXTRA_ATTENTION_REQUEST_ID)
                        pendingAudioBytes = audioBytes
                        PresenceService.activeClient?.sendVoiceSessionOpen(
                            conversationId = null,
                            attentionRequestId = attentionRequestId,
                        )
                    }
                },
                onError = { message -> showError(message) },
            )
        }
    }

    /** render() otherwise only runs from the session/response/connection
     * StateFlow combine() below — an error from SpeechInputController
     * doesn't touch any of those, so without this explicit call the error
     * would silently never reach the screen. */
    private fun showError(message: String) {
        // Remove any in-progress live-transcript bubble before showing the
        // error, so a failed capture doesn't leave a dangling "listening..."
        // bubble on screen.
        clearPendingLiveTranscript()
        // Without this, a recognizer error (NO_MATCH, network, etc.) leaves
        // SpeechInputController back at IDLE but screenState stuck at
        // LISTENING -- the mic button stays showing "Stop" and the next tap
        // calls stopListening() on an already-idle recognizer, a no-op that
        // wedges the screen until the Activity is backgrounded. Only valid
        // from LISTENING (nextVoiceScreenState no-ops otherwise), matching
        // the one state this callback is ever reached from today.
        transition(VoiceScreenEvent.CancelledOrBackgrounded)
        // A failed/errored capture attempt never proceeds to a real
        // VoiceSession, so PresenceService's session-based WakeWordManager
        // pause never happens either -- abandon our own focus request here
        // so wake-word listening isn't left paused indefinitely after a
        // failure.
        audioFocusManager.abandonFocus()
        userFacingError = message
        app.conversationRepository.addMessage(
            ConversationMessage(
                id = java.util.UUID.randomUUID().toString(),
                type = ConversationMessage.Type.SYSTEM_EVENT,
                content = message,
                timestamp = System.currentTimeMillis(),
            )
        )
        render(
            app.voiceSessionRepository.current.value,
            app.voiceSessionRepository.lastResponse.value,
            app.connectionState.value,
        )
    }

    private fun clearPendingLiveTranscript() {
        val id = pendingLiveTranscriptId
        if (id != null) {
            app.conversationRepository.removeMessage(id)
        }
        pendingLiveTranscriptId = null
    }

    private fun transition(event: VoiceScreenEvent, continuousConversationActive: Boolean = false) {
        val from = screenState
        screenState = nextVoiceScreenState(screenState, event, continuousConversationActive)
        android.util.Log.i("VoiceScreenState", "$from + $event -> $screenState")
        applyScreenState()
    }

    private fun applyScreenState() {
        if (screenState != VoiceScreenState.REVIEWING) {
            cancelReviewTimeout()
        }
        when (screenState) {
            VoiceScreenState.IDLE -> {
                binding.micButton.text = "Mic"
                binding.reviewContainer.visibility = View.GONE
                binding.typeInsteadButton.visibility = View.VISIBLE
            }
            VoiceScreenState.LISTENING -> {
                binding.micButton.text = "Stop"
                binding.reviewContainer.visibility = View.GONE
                binding.typeInsteadButton.visibility = View.GONE
            }
            VoiceScreenState.REVIEWING -> {
                binding.micButton.text = "Mic"
                binding.reviewContainer.visibility = View.VISIBLE
                binding.typeInsteadButton.visibility = View.GONE
            }
            VoiceScreenState.PROCESSING -> {
                binding.micButton.text = "Mic"
                binding.reviewContainer.visibility = View.GONE
                binding.typeInsteadButton.visibility = View.GONE
            }
            VoiceScreenState.RESPONDING -> {
                binding.micButton.text = "Mic"
                binding.reviewContainer.visibility = View.GONE
                binding.typeInsteadButton.visibility = View.GONE
            }
        }
    }

    private fun startReviewTimeout() {
        reviewTimeoutHandler.removeCallbacks(reviewTimeoutRunnable)
        reviewTimeoutHandler.postDelayed(reviewTimeoutRunnable, 60_000L)
    }

    private fun cancelReviewTimeout() {
        reviewTimeoutHandler.removeCallbacks(reviewTimeoutRunnable)
    }

    private fun onClose() {
        val session = app.voiceSessionRepository.current.value
        if (session != null) {
            PresenceService.activeClient?.sendVoiceSessionClose(session.voiceSessionId)
        }
        playbackManager.cancel()
        pendingTranscript = null
        pendingAudioBytes = null
        finish()
    }

    private fun render(
        session: VoiceSession?,
        response: String?,
        connectionState: ConnectionState,
    ) {
        binding.connectionStateText.text = when (connectionState) {
            ConnectionState.CONNECTED -> "Connected"
            ConnectionState.CONNECTING -> "Connecting…"
            ConnectionState.RECONNECTING -> "Reconnecting…"
            ConnectionState.DISCONNECTED -> "Not connected"
            ConnectionState.FAILED_PERMANENT -> "Connection failed"
        }

        if (userFacingError != null) {
            isAwaitingResponse = false
            binding.statusText.text = "error"
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
