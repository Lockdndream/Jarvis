package com.jarvis.companion.voice

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

internal interface AudioSource {
    fun isAvailable(): Boolean
    fun startRecording(): Boolean
    fun read(buffer: ShortArray): Int
    fun stop()
    fun release()
}

internal class AndroidAudioCaptureEngine(
    private val audioSource: AudioSource,
    private val callbackPoster: ((() -> Unit) -> Unit)? = null,
) : AudioCaptureEngine {

    constructor() : this(
        audioSource = PlatformAudioSource(),
    )

    private val mainHandler = Handler(Looper.getMainLooper())

    private var isCapturing = false
    private var captureThread: Thread? = null

    @Volatile
    private var cancelled = false

    private fun postCallback(action: () -> Unit) {
        callbackPoster?.invoke(action) ?: mainHandler.post(action)
    }

    private fun postCallbackIfNotCancelled(action: () -> Unit) {
        if (!cancelled) postCallback(action)
    }

    override fun isCaptureAvailable(): Boolean = audioSource.isAvailable()

    override fun startCapture(
        onAudioCaptured: (ByteArray) -> Unit,
        onError: (String) -> Unit,
        onIdleTimeout: () -> Unit,
    ) {
        if (isCapturing) return

        if (!audioSource.isAvailable()) {
            onError("Audio capture is not available on this device")
            return
        }

        if (!audioSource.startRecording()) {
            onError("Failed to start audio recording")
            return
        }

        isCapturing = true
        cancelled = false
        captureThread = Thread {
            captureLoop(onAudioCaptured, onError, onIdleTimeout)
        }.apply { start() }
    }

    private fun captureLoop(
        onAudioCaptured: (ByteArray) -> Unit,
        onError: (String) -> Unit,
        onIdleTimeout: () -> Unit,
    ) {
        val buffer = ShortArray(READ_BUFFER_SIZE_SAMPLES)
        val pcmStream = ByteArrayOutputStream()
        var capturedDurationMs = 0L
        var consecutiveSilenceMs = 0L
        var hadLoud = false
        var primingComplete = false
        // Freeze-on-hadLoud strategy (combined with speech-chunk exclusion):
        // once speech is first detected, the noise floor stops updating for
        // the remainder of this utterance. This prevents a long continuous
        // utterance from polluting the floor estimate with speech-level RMS
        // values, which would raise the end-of-speech threshold and risk
        // hanging. Speech chunks are excluded from the rolling window even
        // before the freeze, so the floor only ever reflects pre-speech
        // ambient noise.
        var noiseFloorFrozen = false
        val rmsWindow = ArrayDeque<Double>()
        var noiseFloor = NOISE_FLOOR_MIN

        var rmsMin = Double.MAX_VALUE
        var rmsMax = 0.0
        var rmsSum = 0.0
        var rmsCount = 0

        try {
            while (isCapturing) {
                val samplesRead = audioSource.read(buffer)
                if (samplesRead <= 0) {
                    if (pcmStream.size() == 0) {
                        break
                    }
                    break
                }

                val durationMs = samplesRead * 1000L / SAMPLE_RATE
                val chunkRms = computeRms(buffer, samplesRead)
                rmsMin = minOf(rmsMin, chunkRms)
                rmsMax = maxOf(rmsMax, chunkRms)
                rmsSum += chunkRms
                rmsCount += 1

                for (i in 0 until samplesRead) {
                    val sample = buffer[i].toInt()
                    pcmStream.write(sample and 0xFF)
                    pcmStream.write((sample shr 8) and 0xFF)
                }

                capturedDurationMs += durationMs

                if (!primingComplete) {
                    // Priming phase: collect ambient RMS values into the
                    // window without attempting speech detection. This lets
                    // the noise floor stabilise so that steady ambient noise
                    // (fan, wind, traffic) is not misclassified as speech.
                    rmsWindow.addLast(chunkRms)
                    while (rmsWindow.size > NOISE_FLOOR_WINDOW_CHUNKS) {
                        rmsWindow.removeFirst()
                    }
                    noiseFloor = computeNoiseFloor(rmsWindow.toList(), NOISE_FLOOR_MIN)
                    if (rmsWindow.size >= NOISE_FLOOR_PRIME_CHUNKS) {
                        primingComplete = true
                    }
                } else {
                    // Adaptive detection phase
                    val threshold = noiseFloor * SPEECH_MULTIPLIER

                    if (chunkRms > threshold) {
                        hadLoud = true
                        consecutiveSilenceMs = 0
                        noiseFloorFrozen = true
                        // Speech chunk: do NOT add to window
                    } else {
                        consecutiveSilenceMs += durationMs
                        if (!noiseFloorFrozen) {
                            rmsWindow.addLast(chunkRms)
                            while (rmsWindow.size > NOISE_FLOOR_WINDOW_CHUNKS) {
                                rmsWindow.removeFirst()
                            }
                            noiseFloor = computeNoiseFloor(rmsWindow.toList(), NOISE_FLOOR_MIN)
                        }
                    }

                    if (!hadLoud && capturedDurationMs >= IDLE_TIMEOUT_MS) {
                        postCallbackIfNotCancelled { onIdleTimeout() }
                        return
                    }

                    if (capturedDurationMs >= MAX_CAPTURE_DURATION_MS) {
                        break
                    }

                    if (isUtteranceComplete(hadLoud, capturedDurationMs, consecutiveSilenceMs,
                            MIN_CAPTURE_DURATION_MS, SILENCE_DURATION_MS)) {
                        break
                    }
                }
            }

            android.util.Log.i(
                "AudioCaptureEngine",
                "capture finished: durationMs=$capturedDurationMs hadLoud=$hadLoud " +
                    "rms min=${"%.1f".format(if (rmsCount > 0) rmsMin else 0.0)} " +
                    "max=${"%.1f".format(rmsMax)} " +
                    "mean=${"%.1f".format(if (rmsCount > 0) rmsSum / rmsCount else 0.0)} " +
                    "noiseFloor=${"%.1f".format(noiseFloor)} " +
                    "threshold=${"%.1f".format(noiseFloor * SPEECH_MULTIPLIER)}",
            )

            val pcmBytes = pcmStream.toByteArray()
            if (pcmBytes.isEmpty()) {
                postCallbackIfNotCancelled { onError("No audio captured") }
                return
            }

            val wavBytes = buildWav(pcmBytes)
            postCallbackIfNotCancelled { onAudioCaptured(wavBytes) }
        } catch (e: Exception) {
            postCallbackIfNotCancelled { onError("Audio capture error: ${e.message}") }
        } finally {
            isCapturing = false
            audioSource.stop()
            audioSource.release()
        }
    }

    override fun cancel() {
        cancelled = true
        isCapturing = false
        audioSource.stop()
        captureThread?.run {
            try {
                join(500)
            } catch (_: InterruptedException) {
            }
        }
        captureThread = null
    }

    companion object {
        const val SAMPLE_RATE = 16000

        // Legacy absolute threshold — retained only for the regression test
        // that demonstrates the old fixed threshold would hang under ambient
        // noise (TD-029). Not used in the adaptive capture path.
        const val SILENCE_RMS_THRESHOLD = 328.0

        // Adaptive speech detection
        const val SPEECH_MULTIPLIER = 2.5

        // Silence duration required to end utterance (same 3000ms as before)
        const val SILENCE_DURATION_MS = 3000L

        // Never trigger end-of-speech before this much has been captured
        const val MIN_CAPTURE_DURATION_MS = 1000L

        // Hard stop regardless of RMS state — the actual backstop
        const val MAX_CAPTURE_DURATION_MS = 60_000L

        // Floor the noise-floor estimate so a perfectly silent room doesn't
        // produce a near-zero threshold
        const val NOISE_FLOOR_MIN = 50.0

        // Rolling window for noise-floor estimation: 5 seconds at 100ms/chunk
        const val NOISE_FLOOR_WINDOW_MS = 5_000L
        internal const val NOISE_FLOOR_WINDOW_CHUNKS = (NOISE_FLOOR_WINDOW_MS / 100).toInt()

        // Minimum chunks before speech detection is enabled. This brief priming
        // period lets the noise floor stabilise so that steady ambient noise
        // (fan, wind, traffic) is not misclassified as speech on the first
        // chunk.  500ms is short enough that speech onset within the priming
        // window is unlikely to contaminate the floor beyond recovery.
        internal const val NOISE_FLOOR_PRIME_CHUNKS = 5

        // If hadLoud never becomes true within 10 seconds, signal idle timeout
        const val IDLE_TIMEOUT_MS = 10_000L

        // 100ms chunks at 16kHz — responsive enough for silence detection
        // without excessive thread wakeups.
        internal const val READ_BUFFER_SIZE_SAMPLES = SAMPLE_RATE / 10
    }
}

internal fun computeRms(samples: ShortArray, sampleCount: Int): Double {
    var sum = 0.0
    for (i in 0 until sampleCount) {
        val s = samples[i].toDouble()
        sum += s * s
    }
    return sqrt(sum / sampleCount)
}

internal fun computeNoiseFloor(rmsValues: List<Double>, noiseFloorMin: Double): Double {
    if (rmsValues.isEmpty()) return noiseFloorMin
    val sorted = rmsValues.sorted()
    val count = maxOf(1, (sorted.size * 0.2).toInt())
    val lowest = sorted.take(count)
    val floor = lowest.sum() / lowest.size
    return maxOf(floor, noiseFloorMin)
}

internal fun buildWav(pcmData: ByteArray): ByteArray {
    val totalDataLen = pcmData.size + 36
    val header = ByteBuffer.allocate(44).apply {
        order(ByteOrder.LITTLE_ENDIAN)
        put('R'.code.toByte())
        put('I'.code.toByte())
        put('F'.code.toByte())
        put('F'.code.toByte())
        putInt(totalDataLen)
        put('W'.code.toByte())
        put('A'.code.toByte())
        put('V'.code.toByte())
        put('E'.code.toByte())
        put('f'.code.toByte())
        put('m'.code.toByte())
        put('t'.code.toByte())
        put(' '.code.toByte())
        putInt(16)
        putShort(1)
        putShort(1)
        putInt(AndroidAudioCaptureEngine.SAMPLE_RATE)
        putInt(AndroidAudioCaptureEngine.SAMPLE_RATE * 2)
        putShort(2)
        putShort(16)
        put('d'.code.toByte())
        put('a'.code.toByte())
        put('t'.code.toByte())
        put('a'.code.toByte())
        putInt(pcmData.size)
    }
    return header.array() + pcmData
}

internal class PlatformAudioSource : AudioSource {

    private var audioRecord: AudioRecord? = null

    override fun isAvailable(): Boolean {
        val minBuf = AudioRecord.getMinBufferSize(
            AndroidAudioCaptureEngine.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        return minBuf != AudioRecord.ERROR_BAD_VALUE && minBuf != AudioRecord.ERROR
    }

    override fun startRecording(): Boolean {
        val minBuf = AudioRecord.getMinBufferSize(
            AndroidAudioCaptureEngine.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuf == AudioRecord.ERROR_BAD_VALUE || minBuf == AudioRecord.ERROR) {
            return false
        }
        try {
            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                AndroidAudioCaptureEngine.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBuf.coerceAtLeast(AndroidAudioCaptureEngine.SAMPLE_RATE * 2),
            )
            if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
                audioRecord?.release()
                audioRecord = null
                return false
            }
            audioRecord?.startRecording()
            return audioRecord?.recordingState == AudioRecord.RECORDSTATE_RECORDING
        } catch (e: SecurityException) {
            return false
        } catch (e: IllegalArgumentException) {
            return false
        }
    }

    override fun read(buffer: ShortArray): Int = audioRecord?.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING) ?: -1

    override fun stop() {
        try {
            audioRecord?.stop()
        } catch (_: IllegalStateException) {
        }
    }

    override fun release() {
        try {
            audioRecord?.release()
        } catch (_: Exception) {
        }
        audioRecord = null
    }
}

internal fun isUtteranceComplete(
    hadLoud: Boolean,
    capturedDurationMs: Long,
    consecutiveSilenceMs: Long,
    minimumLengthMs: Long = AndroidAudioCaptureEngine.MIN_CAPTURE_DURATION_MS,
    completeSilenceMs: Long = AndroidAudioCaptureEngine.SILENCE_DURATION_MS,
): Boolean {
    return hadLoud && capturedDurationMs >= minimumLengthMs && consecutiveSilenceMs >= completeSilenceMs
}
