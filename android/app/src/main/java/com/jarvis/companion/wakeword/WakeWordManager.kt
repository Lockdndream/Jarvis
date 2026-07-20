package com.jarvis.companion.wakeword

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.util.UUID
import kotlin.math.max
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val TAG = "WakeWordManager"
private const val SAMPLE_RATE = 16000
private const val FEATURE_STEP_SIZE_MS = 10
private const val SLIDING_WINDOW_SIZE = 5
private const val CHUNK_SAMPLES = SAMPLE_RATE / 1000 * FEATURE_STEP_SIZE_MS
// Milestone 9B.10 (real-device finding): how often the capture loop
// rechecks whether LISTENING has resumed while paused. The microphone
// hardware is fully released during any pause (see runCaptureLoop) so a
// concurrent SpeechRecognizer (e.g. VoiceActivity's mic button) can
// actually receive real audio instead of silence — a real, reproduced
// bug where WakeWordManager's own AudioRecord never let go of the mic,
// so every post-wake-word spoken command was recognized as
// NO_SPEECH_DETECTED regardless of what the user said. 200ms adds
// negligible latency to reacquiring the mic after a VoiceSession closes.
private const val PAUSE_POLL_INTERVAL_MS = 200L

/**
 * Milestone 9B.7: abstraction over [AudioRecord] lifecycle so
 * [WakeWordManager]'s capture loop and state machine can be unit-tested
 * without a real Android runtime — [AudioRecord.getMinBufferSize] and
 * every [AudioRecord] API return meaningless stubbed defaults in a JVM
 * unit test (`isReturnDefaultValues = true`), which would otherwise make
 * the real capture path non-deterministically fail on a background
 * thread outside the test framework's control.
 */
interface AudioCaptureSource {
    /** Initializes and starts capturing. Returns false (never throws) on
     * any failure — permission denial, invalid buffer size, or
     * [AudioRecord] init/start failure are all reported the same way,
     * since the caller's response (ERROR transition) is identical either
     * way; only the log message differs. */
    fun start(): Boolean

    /** Blocking read into [buffer]. Returns samples read, or <=0 on
     * error/no data/stopped. */
    fun read(buffer: ShortArray): Int

    fun stop()
    fun release()
}

class AndroidAudioCaptureSource : AudioCaptureSource {
    private var record: AudioRecord? = null

    override fun start(): Boolean {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuf <= 0) {
            Log.e(TAG, "AudioRecord.getMinBufferSize() returned invalid size: $minBuf")
            return false
        }
        val bufferSize = max(minBuf, CHUNK_SAMPLES * 2 * 4)

        val r = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferSize,
            )
        } catch (e: SecurityException) {
            Log.e(TAG, "AudioRecord construction denied (RECORD_AUDIO not granted?)", e)
            return false
        } catch (e: IllegalArgumentException) {
            Log.e(TAG, "AudioRecord construction failed: invalid parameters", e)
            return false
        }

        if (r.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "AudioRecord failed to initialize (state=${r.state})")
            r.release()
            return false
        }

        try {
            r.startRecording()
        } catch (e: IllegalStateException) {
            Log.e(TAG, "AudioRecord.startRecording() failed", e)
            r.release()
            return false
        }

        record = r
        return true
    }

    override fun read(buffer: ShortArray): Int = record?.read(buffer, 0, buffer.size) ?: -1

    override fun stop() {
        try {
            record?.stop()
        } catch (e: IllegalStateException) {
            Log.w(TAG, "stop(): AudioRecord.stop() threw (already stopped?)", e)
        }
    }

    override fun release() {
        record?.release()
        record = null
    }
}

/**
 * Milestone 9B.7 (ADR-017): single owner of wake-word model lifecycle,
 * AudioRecord lifecycle, the inference loop, detection state, and
 * diagnostics. Knows nothing about networking, VoiceActivity, the
 * WebSocket protocol, the VoiceSession protocol, or UI — PresenceService
 * orchestrates all of that by observing [state]/[onDetected].
 *
 * Owns exactly one [WakeWordEngine] instance per [start]/[stop] cycle —
 * created once in [start], reused for every [onAudioChunk] call, destroyed
 * once in [stop]. Never relies on finalize() as the primary cleanup path
 * (the engine's own finalize() fallback exists only as a last-resort net,
 * per its own doc comment — this class's lifecycle is always explicit).
 *
 * [engineFactory]/[audioFocus]/[audioSourceFactory] are all injected
 * (rather than constructing [MicroWakeWord]/[AndroidWakeWordAudioFocus]/
 * [AndroidAudioCaptureSource] directly) so unit tests can substitute
 * fakes for every real-Android-runtime dependency — none of the three can
 * run in a JVM unit test.
 */
class WakeWordManager(
    private val config: WakeWordConfigRepository,
    private val engineFactory: () -> WakeWordEngine,
    private val audioFocus: WakeWordAudioFocus = NoOpWakeWordAudioFocus,
    private val audioSourceFactory: () -> AudioCaptureSource = { AndroidAudioCaptureSource() },
) {
    enum class State { STOPPED, INITIALIZING, LISTENING, PAUSED_AUDIO_FOCUS, PAUSED_VOICE_SESSION, ERROR }

    enum class AudioRecordState { NONE, INITIALIZED, FAILED }

    constructor(context: Context, config: WakeWordConfigRepository) : this(
        config = config,
        engineFactory = {
            val afd = context.assets.openFd("hey_jarvis.tflite")
            val modelBuffer: ByteBuffer = FileInputStream(afd.fileDescriptor).use { input ->
                input.channel.map(FileChannel.MapMode.READ_ONLY, afd.startOffset, afd.declaredLength)
            }
            MicroWakeWord(
                modelBuffer = modelBuffer,
                featureStepSizeMs = FEATURE_STEP_SIZE_MS,
                probabilityCutoff = config.confidenceThreshold(),
                slidingWindowSize = SLIDING_WINDOW_SIZE,
            )
        },
        audioFocus = AndroidWakeWordAudioFocus(context),
    )

    private val _state = MutableStateFlow(State.STOPPED)
    val state: StateFlow<State> = _state.asStateFlow()

    private val _engineLoaded = MutableStateFlow(false)
    val engineLoaded: StateFlow<Boolean> = _engineLoaded.asStateFlow()

    private val _audioRecordState = MutableStateFlow(AudioRecordState.NONE)
    val audioRecordState: StateFlow<AudioRecordState> = _audioRecordState.asStateFlow()

    private val _detectionCount = MutableStateFlow(0)
    val detectionCount: StateFlow<Int> = _detectionCount.asStateFlow()

    private val _lastDetectionAtMs = MutableStateFlow<Long?>(null)
    val lastDetectionAtMs: StateFlow<Long?> = _lastDetectionAtMs.asStateFlow()

    private val _framesProcessed = MutableStateFlow(0L)
    val framesProcessed: StateFlow<Long> = _framesProcessed.asStateFlow()

    private val _avgLatencyMs = MutableStateFlow(0.0)
    val avgLatencyMs: StateFlow<Double> = _avgLatencyMs.asStateFlow()

    private val _maxLatencyMs = MutableStateFlow(0.0)
    val maxLatencyMs: StateFlow<Double> = _maxLatencyMs.asStateFlow()

    private val _inferenceErrorCount = MutableStateFlow(0)
    val inferenceErrorCount: StateFlow<Int> = _inferenceErrorCount.asStateFlow()

    private val _engineStartedAtMs = MutableStateFlow<Long?>(null)
    val engineStartedAtMs: StateFlow<Long?> = _engineStartedAtMs.asStateFlow()

    private val _onDetected = MutableSharedFlow<WakeWordDetection>(extraBufferCapacity = 4)
    val onDetected: SharedFlow<WakeWordDetection> = _onDetected.asSharedFlow()

    private var scope: CoroutineScope? = null
    private var captureJob: Job? = null
    private var engine: WakeWordEngine? = null
    // Written on the capture coroutine's thread (Dispatchers.Default),
    // read from stop()'s caller thread -- @Volatile is required for a
    // happens-before guarantee; without it stop() could see a stale null
    // and skip stopping/releasing the real AudioRecord (a real microphone
    // leak), independently confirmed during review.
    @Volatile private var audioSource: AudioCaptureSource? = null
    private var totalLatencyMs = 0.0

    // Serializes every _state.value read-then-write transition below.
    // pauseForAudioFocusLoss()/resumeFromAudioFocusLoss() fire from an
    // arbitrary AudioManager callback thread; pauseForVoiceSession()/
    // resumeAfterVoiceSession() are called by PresenceService (main
    // thread); onAudioChunk()'s detection path runs on the capture
    // thread. Each function's "read current state, decide, write" is not
    // atomic on its own -- a real, reproduced TOCTOU race (two threads
    // both read the pre-transition state before either writes) can lose
    // one of the two transitions entirely. Confirmed during independent
    // review with a concrete interleaving; do not remove this lock.
    private val stateLock = Any()

    // Tracked independently of `state` so a VoiceSession-pause ending while
    // audio focus is *still* lost resumes into PAUSED_AUDIO_FOCUS, not
    // LISTENING — otherwise capture would resume without focus, exactly the
    // conflict this class's own audio-focus request exists to prevent. Not
    // exposed as a separate diagnostics field; `state` already reflects the
    // composite result any observer needs. Always mutated under [stateLock].
    private var audioFocusLost = false

    fun start() {
        if (_state.value != State.STOPPED) {
            Log.w(TAG, "start() ignored: illegal transition from ${_state.value}")
            return
        }
        if (!config.isEnabled()) {
            Log.i(TAG, "start() ignored: wake word disabled in config")
            return
        }
        _state.value = State.INITIALIZING
        _engineLoaded.value = false
        _audioRecordState.value = AudioRecordState.NONE
        _detectionCount.value = 0
        _lastDetectionAtMs.value = null
        _framesProcessed.value = 0
        _avgLatencyMs.value = 0.0
        _maxLatencyMs.value = 0.0
        _inferenceErrorCount.value = 0
        totalLatencyMs = 0.0
        synchronized(stateLock) { audioFocusLost = false }

        val createdEngine = try {
            engineFactory()
        } catch (e: Exception) {
            Log.e(TAG, "start() failed: engine creation threw", e)
            _state.value = State.ERROR
            return
        }
        engine = createdEngine
        _engineLoaded.value = true
        _engineStartedAtMs.value = System.currentTimeMillis()

        val activeScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        scope = activeScope
        _state.value = State.LISTENING
        audioFocus.request { gained -> if (gained) resumeFromAudioFocusLoss() else pauseForAudioFocusLoss() }
        // Pass the launch block's own receiver (`this`, the actual child
        // coroutine's scope backed by the Job captureJob holds), not the
        // outer `activeScope` variable — activeScope's own Job is a
        // SupervisorJob, and by definition a SupervisorJob is never
        // cancelled by a child's cancellation, so checking
        // activeScope.isActive inside the loop would never observe
        // captureJob?.cancel() and the loop would never terminate. A real,
        // reproduced bug found during independent review — do not revert
        // to passing `activeScope` here.
        captureJob = activeScope.launch { runCaptureLoop(this) }
    }

    fun stop() {
        if (_state.value == State.STOPPED) return
        captureJob?.cancel()
        audioFocus.abandon()
        audioSource?.stop()
        audioSource?.release()
        audioSource = null
        _audioRecordState.value = AudioRecordState.NONE
        engine?.close()
        engine = null
        _engineLoaded.value = false
        _engineStartedAtMs.value = null
        captureJob = null
        scope = null
        synchronized(stateLock) { audioFocusLost = false }
        _state.value = State.STOPPED
    }

    private fun pauseForAudioFocusLoss() {
        synchronized(stateLock) {
            audioFocusLost = true
            if (_state.value == State.LISTENING) {
                _state.value = State.PAUSED_AUDIO_FOCUS
            } else {
                Log.d(TAG, "pauseForAudioFocusLoss(): recorded focus loss, no state transition from ${_state.value}")
            }
        }
    }

    private fun resumeFromAudioFocusLoss() {
        synchronized(stateLock) {
            audioFocusLost = false
            if (_state.value == State.PAUSED_AUDIO_FOCUS) {
                _state.value = State.LISTENING
            } else {
                Log.d(TAG, "resumeFromAudioFocusLoss(): recorded focus regain, no state transition from ${_state.value}")
            }
        }
    }

    fun pauseForVoiceSession() {
        synchronized(stateLock) {
            if (_state.value == State.LISTENING || _state.value == State.PAUSED_AUDIO_FOCUS) {
                _state.value = State.PAUSED_VOICE_SESSION
            } else {
                Log.d(TAG, "pauseForVoiceSession() ignored: no-op from ${_state.value}")
            }
        }
        // Milestone 9B.10: no direct audioSource.stop() here -- a real
        // AudioRecord.read() for a 10ms chunk returns within ~10ms on its
        // own (it's bounded by how fast the hardware actually produces
        // that much audio), so runCaptureLoop's own top-of-loop state
        // check already releases the mic promptly without needing to be
        // unblocked from another thread. This call fires only once a
        // VoiceSession has actually opened, which for the wake-word flow
        // is already after onAudioChunk()'s own detection handling
        // self-transitioned to PAUSED_VOICE_SESSION synchronously on the
        // capture thread itself; for a manually-opened session ("Talk to
        // Jarvis") it fires only after SpeechRecognizer already completed
        // its first recognition attempt, so it cannot help that attempt
        // either way -- see ADR-016/9B.10 report for the manual-flow gap
        // this does not close.
    }

    fun resumeAfterVoiceSession() {
        var needsFocusRecheck = false
        synchronized(stateLock) {
            if (_state.value == State.PAUSED_VOICE_SESSION) {
                // If audio focus was lost while paused for the voice session,
                // resuming must land in PAUSED_AUDIO_FOCUS, not LISTENING —
                // otherwise capture would resume without focus.
                if (audioFocusLost) {
                    _state.value = State.PAUSED_AUDIO_FOCUS
                    needsFocusRecheck = true
                } else {
                    _state.value = State.LISTENING
                }
            } else {
                Log.d(TAG, "resumeAfterVoiceSession() ignored: no-op from ${_state.value}")
            }
        }
        // Milestone 9B.10 real-device finding: audioFocusLost goes stale
        // and never clears on its own here. Android only auto-delivers a
        // regain callback after a *transient* loss; a conversation's own
        // TTS playback (com.jarvis.companion.audio.AudioFocusManager)
        // requests permanent AUDIOFOCUS_GAIN, which evicts this class's
        // AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK holder with a *permanent*
        // AUDIOFOCUS_LOSS -- by design, Android never follows a permanent
        // loss with an automatic GAIN notification, so nothing was ever
        // going to call resumeFromAudioFocusLoss() again. Confirmed via
        // real-device repro: audioFocusLost stayed true across 20+ minutes
        // and multiple wake-word attempts until the app was killed and
        // restarted -- a real, reproduced, session-fatal bug, not a
        // theoretical one. Re-requesting here (rather than trusting the
        // stale flag) queries the current ground truth: by the time a
        // VoiceSession actually closes, whatever caused the loss has
        // normally already abandoned its own focus.
        if (needsFocusRecheck) {
            audioFocus.abandon()
            val regained = audioFocus.request { gained -> if (gained) resumeFromAudioFocusLoss() else pauseForAudioFocusLoss() }
            if (regained) resumeFromAudioFocusLoss()
        }
    }

    /** Test-only seam: feeds one chunk directly to [onAudioChunk], bypassing
     * the real capture loop entirely. Production capture calls the exact
     * same function. */
    internal fun feedAudioForTest(samples: ShortArray) {
        onAudioChunk(samples)
    }

    private fun onAudioChunk(samples: ShortArray) {
        if (_state.value != State.LISTENING) return
        val currentEngine = engine ?: return

        val detected: Boolean
        val elapsedMs: Double
        try {
            val t0 = System.nanoTime()
            detected = currentEngine.processAudio(samples)
            elapsedMs = (System.nanoTime() - t0) / 1_000_000.0
        } catch (e: Exception) {
            Log.e(TAG, "processAudio() threw — dropping this frame, staying LISTENING", e)
            _inferenceErrorCount.value = _inferenceErrorCount.value + 1
            return
        }

        _framesProcessed.value = _framesProcessed.value + 1
        totalLatencyMs += elapsedMs
        _avgLatencyMs.value = totalLatencyMs / _framesProcessed.value
        if (elapsedMs > _maxLatencyMs.value) _maxLatencyMs.value = elapsedMs

        if (detected) {
            try {
                currentEngine.reset()
            } catch (e: Exception) {
                Log.e(TAG, "reset() after detection threw", e)
                _inferenceErrorCount.value = _inferenceErrorCount.value + 1
            }
            _detectionCount.value = _detectionCount.value + 1
            val now = System.currentTimeMillis()
            _lastDetectionAtMs.value = now
            synchronized(stateLock) {
                // Re-check under the lock: state may have changed between
                // the fast-path check at the top of this function and now
                // (e.g. audio focus was lost mid-inference) — only force
                // PAUSED_VOICE_SESSION if we're still actually LISTENING,
                // so this can't clobber a PAUSED_AUDIO_FOCUS/ERROR
                // transition that raced ahead of us. The detection itself
                // is still real and still reported either way.
                if (_state.value == State.LISTENING) {
                    _state.value = State.PAUSED_VOICE_SESSION
                }
            }
            _onDetected.tryEmit(
                WakeWordDetection(
                    detectionId = UUID.randomUUID(),
                    timestampMs = now,
                    confidence = null,
                    modelVersion = config.modelVersion(),
                ),
            )
        }
    }

    private suspend fun runCaptureLoop(activeScope: CoroutineScope) {
        val buf = ShortArray(CHUNK_SAMPLES)
        while (activeScope.isActive) {
            if (_state.value != State.LISTENING) {
                // Milestone 9B.10: release the microphone hardware itself
                // while paused (PAUSED_VOICE_SESSION or
                // PAUSED_AUDIO_FOCUS), not just stop acting on it — a
                // held-but-idle AudioRecord still occupies the mic on many
                // devices, starving a concurrent SpeechRecognizer of real
                // audio. stop()/release()/nulling audioSource only ever
                // happens here, on this coroutine — a real AudioRecord's
                // read() for a 10ms chunk returns within ~10ms on its own,
                // so this top-of-loop check runs promptly after a pause
                // without needing to be unblocked from another thread.
                audioSource?.let {
                    it.stop()
                    it.release()
                }
                audioSource = null
                _audioRecordState.value = AudioRecordState.NONE
                delay(PAUSE_POLL_INTERVAL_MS)
                continue
            }

            if (audioSource == null) {
                val source = audioSourceFactory()
                if (!source.start()) {
                    _audioRecordState.value = AudioRecordState.FAILED
                    _state.value = State.ERROR
                    source.release()
                    return
                }
                audioSource = source
                _audioRecordState.value = AudioRecordState.INITIALIZED
            }

            val read = audioSource?.read(buf) ?: -1
            if (read <= 0) {
                if (!activeScope.isActive) break
                continue
            }
            val samples = if (read == buf.size) buf else buf.copyOf(read)
            onAudioChunk(samples)
        }
    }
}
