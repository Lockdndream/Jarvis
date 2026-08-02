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

interface AudioCaptureEngine {
    fun isCaptureAvailable(): Boolean
    fun startCapture(
        onAudioCaptured: (ByteArray) -> Unit,
        onError: (String) -> Unit,
        onIdleTimeout: () -> Unit = {},
    )
    fun cancel()
}

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
    private val audioCaptureEngine: AudioCaptureEngine? = null,
) {
    constructor(context: Context) : this(
        recognizerEngine = AndroidSpeechRecognizerEngine(context),
    )

    constructor(context: Context, audioCaptureEngine: AudioCaptureEngine) : this(
        recognizerEngine = AndroidSpeechRecognizerEngine(context),
        audioCaptureEngine = audioCaptureEngine,
    )

    enum class State { IDLE, LISTENING, PROCESSING, ERROR }

    private val _state = MutableStateFlow(State.IDLE)
    val state: StateFlow<State> = _state.asStateFlow()

    fun isRecognitionAvailable(): Boolean = recognizerEngine.isRecognitionAvailable()

    fun startListening(
        onResult: (String) -> Unit,
        onError: (String) -> Unit,
        onPartialResult: (String) -> Unit = {},
        onBeginningOfSpeech: () -> Unit = {},
        onEmptyResult: () -> Unit = {},
    ) {
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
            onPartialResultCallback = { partial ->
                onPartialResult(partial)
            },
            onBeginningOfSpeechCallback = {
                onBeginningOfSpeech()
            },
            onEmptyResultCallback = {
                _state.value = State.IDLE
                onEmptyResult()
            },
        )
    }

    fun startListeningRaw(onAudioCaptured: (ByteArray) -> Unit, onError: (String) -> Unit) {
        if (_state.value != State.IDLE) return

        val engine = audioCaptureEngine
        if (engine == null) {
            _state.value = State.ERROR
            onError("Raw audio capture is not available on this device")
            _state.value = State.IDLE
            return
        }

        if (!engine.isCaptureAvailable()) {
            _state.value = State.ERROR
            onError("Raw audio capture is not available on this device")
            _state.value = State.IDLE
            return
        }

        _state.value = State.LISTENING
        engine.startCapture(
            onAudioCaptured = { audioBytes ->
                _state.value = State.PROCESSING
                onAudioCaptured(audioBytes)
                _state.value = State.IDLE
            },
            onError = { message ->
                _state.value = State.ERROR
                onError(message)
                _state.value = State.IDLE
            },
        )
    }

    fun cancel() {
        if (_state.value == State.LISTENING || _state.value == State.PROCESSING) {
            recognizerEngine.cancel()
            audioCaptureEngine?.cancel()
        }
        _state.value = State.IDLE
    }

    fun stopListening() {
        if (_state.value != State.LISTENING) return
        recognizerEngine.stopListening()
    }

    /**
     * Package-private abstraction over Android's [SpeechRecognizer],
     * injected so unit tests can verify state transitions and lifecycle
     * sequencing without an Android runtime.
     */
    interface SpeechRecognizerEngine {
        fun isRecognitionAvailable(): Boolean
        fun startListening(
            onResultCallback: (String) -> Unit,
            onErrorCallback: (String) -> Unit,
            onPartialResultCallback: (String) -> Unit = {},
            onBeginningOfSpeechCallback: () -> Unit = {},
            onEmptyResultCallback: () -> Unit = {},
        )
        fun stopListening()
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
    private val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())

    override fun isRecognitionAvailable(): Boolean =
        SpeechRecognizer.isRecognitionAvailable(context)

    override fun startListening(
        onResultCallback: (String) -> Unit,
        onErrorCallback: (String) -> Unit,
        onPartialResultCallback: (String) -> Unit,
        onBeginningOfSpeechCallback: () -> Unit,
        onEmptyResultCallback: () -> Unit,
    ) {
        attemptListen(onResultCallback, onErrorCallback, onPartialResultCallback, onBeginningOfSpeechCallback, onEmptyResultCallback, hasRetried = false)
    }

    private fun attemptListen(
        onResultCallback: (String) -> Unit,
        onErrorCallback: (String) -> Unit,
        onPartialResultCallback: (String) -> Unit,
        onBeginningOfSpeechCallback: () -> Unit,
        onEmptyResultCallback: () -> Unit,
        hasRetried: Boolean,
    ) {
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
            // for the "listening" earcon first. These extras are hints,
            // not a hard guarantee (the platform recognizer may still
            // ignore or cap them), but they're the documented lever for
            // this.
            //
            // Push-to-talk real-device finding: even the 3000/1500ms
            // values above still auto-endpointed mid-sentence during
            // normal pauses, which defeats the point of a manual Stop
            // button in interactive mode -- the user should decide when
            // they're done, not a silence timer. This class is only ever
            // constructed from VoiceActivity's interactive push-to-talk
            // path (and DiagnosticsActivity), never from the background
            // auto-capture flow, so pushing these out to 60s makes
            // auto-endpoint a last-resort safety net (a truly abandoned
            // recognizer, not a normal thinking-pause) without touching
            // background mode's behavior at all.
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 60000L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 60000L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 3000L)
            // TD-029 investigation probe: partials were previously never
            // requested (EXTRA_PARTIAL_RESULTS unset), so onPartialResults
            // never fired even though the callback existed. Needed to
            // measure this device's actual endpointing/partial-result
            // timing before committing to a parallel-capture design.
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        }

        val startedAtMs = android.os.SystemClock.elapsedRealtime()
        sr.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle) {
                val transcript = extractTopResult(results)
                android.util.Log.i(
                    "SpeechRecognizerTiming",
                    "onResults t=${android.os.SystemClock.elapsedRealtime() - startedAtMs}ms transcript=\"$transcript\"",
                )
                if (transcript != null && transcript.isNotBlank()) {
                    onResultCallback(transcript)
                } else if (transcript != null) {
                    onEmptyResultCallback()
                } else {
                    onErrorCallback("No recognition results")
                }
                destroyExisting()
            }

            override fun onError(errorCode: Int) {
                android.util.Log.i(
                    "SpeechRecognizerTiming",
                    "onError t=${android.os.SystemClock.elapsedRealtime() - startedAtMs}ms code=$errorCode (${errorCodeToMessage(errorCode)})",
                )
                // Milestone 9B.10 RC finding: ERROR_CLIENT observed
                // specifically launching via the lock-screen full-screen-
                // intent path, correlated with the screen still turning on
                // / unlocking when startListening() fires. Android's own
                // ERROR_CLIENT is documented as often transient. One
                // retry only -- never loop, so a genuinely broken device
                // still surfaces the error instead of hanging silently.
                if (errorCode == SpeechRecognizer.ERROR_CLIENT && !hasRetried) {
                    destroyExisting()
                    mainHandler.postDelayed({
                        attemptListen(onResultCallback, onErrorCallback, onPartialResultCallback, onBeginningOfSpeechCallback, onEmptyResultCallback, hasRetried = true)
                    }, RETRY_DELAY_MS)
                    return
                }
                onErrorCallback(errorCodeToMessage(errorCode))
                destroyExisting()
            }

            override fun onReadyForSpeech(params: Bundle?) {
                android.util.Log.i("SpeechRecognizerTiming", "onReadyForSpeech t=${android.os.SystemClock.elapsedRealtime() - startedAtMs}ms")
            }
            override fun onBeginningOfSpeech() {
                android.util.Log.i("SpeechRecognizerTiming", "onBeginningOfSpeech t=${android.os.SystemClock.elapsedRealtime() - startedAtMs}ms")
                onBeginningOfSpeechCallback()
            }
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {
                android.util.Log.i("SpeechRecognizerTiming", "onEndOfSpeech t=${android.os.SystemClock.elapsedRealtime() - startedAtMs}ms")
            }
            override fun onPartialResults(partialResults: Bundle?) {
                val partial = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                android.util.Log.i(
                    "SpeechRecognizerTiming",
                    "onPartialResults t=${android.os.SystemClock.elapsedRealtime() - startedAtMs}ms text=\"$partial\"",
                )
                if (partial != null) {
                    onPartialResultCallback(partial)
                }
            }
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        sr.startListening(intent)
    }

    override fun stopListening() {
        recognizer?.stopListening()
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

    companion object {
        private const val RETRY_DELAY_MS = 400L
    }
}