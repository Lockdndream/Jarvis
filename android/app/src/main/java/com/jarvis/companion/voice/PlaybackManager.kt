package com.jarvis.companion.voice

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Owns all spoken responses for the voice feature, wrapping Android's
 * standard [TextToSpeech] engine. Queue management, barge-in cancellation,
 * high-priority utterance insertion, and audio focus integration are
 * handled here; what to say and when to say it are decided by the caller.
 *
 * Caller-driven lifecycle: explicit [init] / [shutdown] rather than
 * self-starting. Readiness is surfaced through [isReady] because the
 * underlying TTS engine initialises asynchronously.
 */
class PlaybackManager internal constructor(
    private val audioFocus: AudioFocusOwner,
    private val ttsEngine: TtsEngine,
) {
    constructor(context: Context, audioFocus: AudioFocusOwner) : this(
        audioFocus = audioFocus,
        ttsEngine = AndroidTtsEngine(context),
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private val _isReady = MutableStateFlow(false)
    val isReady: StateFlow<Boolean> = _isReady.asStateFlow()

    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    private data class Utterance(
        val text: String,
        val highPriority: Boolean,
        val id: String,
    )

    private val queue = ArrayDeque<Utterance>()
    private var utteranceCounter = 0
    private var volumeFraction = 1.0f

    /** Diagnostics-only read (Milestone 9B.4). Safe without extra
     * synchronization: [queue] is only ever mutated inside [scope]'s
     * Dispatchers.Main coroutines, and diagnostics reads happen from the
     * main thread too. */
    val queueDepth: Int get() = queue.size

    fun init() {
        if (_isReady.value) return
        ttsEngine.onUtteranceDone = { id -> onUtteranceDone(id) }
        ttsEngine.init {
            scope.launch {
                _isReady.value = true
                if (queue.isNotEmpty() && !_isSpeaking.value) {
                    speakNext()
                }
            }
        }
    }

    fun speak(text: String, highPriority: Boolean = false) {
        val utterance = Utterance(text, highPriority, nextUtteranceId())
        scope.launch {
            if (highPriority) {
                queue.addFirst(utterance)
            } else {
                queue.addLast(utterance)
            }
            if (!_isSpeaking.value && _isReady.value) {
                speakNext()
            }
        }
    }

    fun cancel() {
        scope.launch {
            queue.clear()
            ttsEngine.stop()
            if (_isSpeaking.value) {
                _isSpeaking.value = false
                audioFocus.abandonFocus()
            }
        }
    }

    fun setVolume(volume: Float) {
        volumeFraction = volume.coerceIn(0.0f, 1.0f)
    }

    fun shutdown() {
        queue.clear()
        ttsEngine.shutdown()
        audioFocus.abandonFocus()
        _isReady.value = false
        _isSpeaking.value = false
        scope.cancel()
    }

    private fun speakNext() {
        val utterance = queue.removeFirstOrNull() ?: return
        _isSpeaking.value = true

        // If the focus request is denied we still attempt TTS — the engine
        // may produce audible output anyway (e.g. when another app holds
        // focus but is paused), and silently dropping the response would
        // lose information the user is waiting for. The caller manages the
        // broader audio environment; this class errs on the side of
        // speaking.
        audioFocus.requestFocus()

        val success = ttsEngine.speak(utterance.text, volumeFraction, utterance.id)
        if (!success) {
            onUtteranceDone(utterance.id)
        }
    }

    @Suppress("UNUSED_PARAMETER")
    private fun onUtteranceDone(utteranceId: String) {
        scope.launch {
            if (queue.isEmpty()) {
                _isSpeaking.value = false
                audioFocus.abandonFocus()
            } else {
                speakNext()
            }
        }
    }

    private fun nextUtteranceId(): String = "utt_${++utteranceCounter}"

    /**
     * Package-private abstraction over Android's TextToSpeech engine,
     * injected so unit tests can verify queue and focus sequencing
     * without an Android runtime.
     */
    interface TtsEngine {
        val isReady: Boolean
        fun init(onReady: () -> Unit)
        fun speak(text: String, volume: Float, utteranceId: String): Boolean
        fun stop()
        fun shutdown()
        var onUtteranceDone: (String) -> Unit
    }
}

/**
 * Real TTS engine wrapping [android.speech.tts.TextToSpeech]. Instantiated
 * as the default engine by [PlaybackManager] in production; replaced by a
 * fake in unit tests.
 */
internal class AndroidTtsEngine(private val context: Context) : PlaybackManager.TtsEngine {

    override val isReady: Boolean get() = tts != null

    override var onUtteranceDone: (String) -> Unit = {}

    private var tts: TextToSpeech? = null

    override fun init(onReady: () -> Unit) {
        if (tts != null) {
            onReady()
            return
        }
        tts = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.US
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onDone(utteranceId: String) {
                        onUtteranceDone(utteranceId)
                    }
                    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
                    override fun onError(utteranceId: String) {
                        onUtteranceDone(utteranceId)
                    }
                    override fun onStart(utteranceId: String) {}
                })
                onReady()
            }
        }
    }

    override fun speak(text: String, volume: Float, utteranceId: String): Boolean {
        val engine = tts ?: return false
        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, volume)
        }
        return engine.speak(text, TextToSpeech.QUEUE_FLUSH, params, utteranceId) == TextToSpeech.SUCCESS
    }

    override fun stop() {
        tts?.stop()
    }

    override fun shutdown() {
        tts?.stop()
        tts?.shutdown()
        tts = null
    }
}