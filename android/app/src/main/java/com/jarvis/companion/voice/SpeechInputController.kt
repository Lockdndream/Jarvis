package com.jarvis.companion.voice

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Foreground, user-initiated speech recognition wrapper mirroring the PWA's
 * interaction model: one tap = one recognition attempt = it ends. Uses
 * [android.speech.SpeechRecognizer] (in-app, no separate recognition UI).
 *
 * Caller-driven lifecycle — this class never listens on its own. Produces a
 * transcript string via [onResult]; transcript interpretation and
 * networking are separate concerns owned elsewhere (see ADR-007,
 * ADR-001).
 */
class SpeechInputController internal constructor(
    private val recognizerEngine: SpeechRecognizerEngine,
) {
    constructor(context: Context) : this(
        recognizerEngine = AndroidSpeechRecognizerEngine(context),
    )

    enum class State { IDLE, LISTENING, PROCESSING, ERROR }

    private val _state = MutableStateFlow(State.IDLE)
    val state: StateFlow<State> = _state.asStateFlow()

    fun startListening(onResult: (String) -> Unit, onError: (String) -> Unit) {
        if (_state.value != State.IDLE) return

        if (!recognizerEngine.isRecognitionAvailable()) {
            _state.value = State.ERROR
            onError("Speech recognition is not available on this device")
            _state.value = State.IDLE
            return
        }

        _state.value = State.LISTENING
        recognizerEngine.startListening(
            onResultCallback = { transcript ->
                _state.value = State.PROCESSING
                onResult(transcript)
                _state.value = State.IDLE
            },
            onErrorCallback = { message ->
                _state.value = State.ERROR
                onError(message)
                _state.value = State.IDLE
            },
        )
    }

    fun cancel() {
        if (_state.value == State.LISTENING || _state.value == State.PROCESSING) {
            recognizerEngine.cancel()
        }
        _state.value = State.IDLE
    }

    /**
     * Package-private abstraction over Android's [SpeechRecognizer],
     * injected so unit tests can verify state transitions and lifecycle
     * sequencing without an Android runtime.
     */
    interface SpeechRecognizerEngine {
        fun isRecognitionAvailable(): Boolean
        fun startListening(onResultCallback: (String) -> Unit, onErrorCallback: (String) -> Unit)
        fun cancel()
    }
}

/** Pure: extracts the top result from a speech recognition results [Bundle]. */
fun extractTopResult(bundle: Bundle): String? {
    val results = bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
    return results?.firstOrNull()
}

/** Pure: maps [SpeechRecognizer] error code to a short human-readable string. */
fun errorCodeToMessage(errorCode: Int): String = when (errorCode) {
    SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Network timeout"
    SpeechRecognizer.ERROR_NETWORK -> "Network error"
    SpeechRecognizer.ERROR_AUDIO -> "Audio error"
    SpeechRecognizer.ERROR_SERVER -> "Server error"
    SpeechRecognizer.ERROR_CLIENT -> "Client error"
    SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech detected"
    SpeechRecognizer.ERROR_NO_MATCH -> "No match found"
    SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Recognizer busy"
    SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Insufficient permissions"
    SpeechRecognizer.ERROR_TOO_MANY_REQUESTS -> "Too many requests"
    SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED -> "Language not supported"
    SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> "Language unavailable"
    else -> "Unknown error ($errorCode)"
}

/**
 * Real [SpeechRecognizer] engine. Instantiated as the default engine by
 * [SpeechInputController] in production; replaced by a fake in unit tests.
 */
internal class AndroidSpeechRecognizerEngine(
    private val context: Context,
) : SpeechInputController.SpeechRecognizerEngine {

    private var recognizer: SpeechRecognizer? = null

    override fun isRecognitionAvailable(): Boolean =
        SpeechRecognizer.isRecognitionAvailable(context)

    override fun startListening(onResultCallback: (String) -> Unit, onErrorCallback: (String) -> Unit) {
        destroyExisting()

        val sr = SpeechRecognizer.createSpeechRecognizer(context)
        recognizer = sr

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            // Milestone 9B.10 real-device finding: with no silence-length
            // extras set, this device's on-device recognizer (SODA) ends
            // the utterance ~1.1s after onStartOfSpeech -- confirmed via
            // system log (onStartOfSpeech to onEndOfSpeech was 1.14s),
            // regardless of whether the user spoke immediately or waited
            // for the "listening" earcon first. That's shorter than any
            // normal spoken sentence, so every recognition attempt this
            // session was truncated to a near-silent fragment and decoded
            // as a low-confidence "now". These extras are hints, not a
            // hard guarantee (the platform recognizer may still ignore or
            // cap them), but they're the documented lever for this.
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 3000L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 3000L)
        }

        sr.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle) {
                val transcript = extractTopResult(results)
                if (transcript != null) {
                    onResultCallback(transcript)
                } else {
                    onErrorCallback("No recognition results")
                }
                destroyExisting()
            }

            override fun onError(errorCode: Int) {
                onErrorCallback(errorCodeToMessage(errorCode))
                destroyExisting()
            }

            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        sr.startListening(intent)
    }

    override fun cancel() {
        recognizer?.let { sr ->
            sr.cancel()
            sr.destroy()
        }
        recognizer = null
    }

    private fun destroyExisting() {
        recognizer?.destroy()
        recognizer = null
    }
}