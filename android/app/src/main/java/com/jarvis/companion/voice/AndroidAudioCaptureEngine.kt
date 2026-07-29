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

    private fun postCallback(action: () -> Unit) {
        callbackPoster?.invoke(action) ?: mainHandler.post(action)
    }

    override fun isCaptureAvailable(): Boolean = audioSource.isAvailable()

    override fun startCapture(onAudioCaptured: (ByteArray) -> Unit, onError: (String) -> Unit) {
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
        captureThread = Thread {
            captureLoop(onAudioCaptured, onError)
        }.apply { start() }
    }

    private fun captureLoop(onAudioCaptured: (ByteArray) -> Unit, onError: (String) -> Unit) {
        val buffer = ShortArray(READ_BUFFER_SIZE_SAMPLES)
        val pcmStream = ByteArrayOutputStream()
        var capturedDurationMs = 0L
        var consecutiveSilenceMs = 0L
        var hadLoud = false

        try {
            while (isCapturing) {
                val samplesRead = audioSource.read(buffer)
                if (samplesRead <= 0) {
                    if (pcmStream.size() == 0) {
                        // Never received any audio — treat as error.
                        break
                    }
                    // End of audio stream after receiving at least some data.
                    break
                }

                val durationMs = samplesRead * 1000L / SAMPLE_RATE
                val chunkRms = computeRms(buffer, samplesRead)

                for (i in 0 until samplesRead) {
                    val sample = buffer[i].toInt()
                    pcmStream.write(sample and 0xFF)
                    pcmStream.write((sample shr 8) and 0xFF)
                }

                capturedDurationMs += durationMs

                if (chunkRms >= SILENCE_RMS_THRESHOLD) {
                    hadLoud = true
                    consecutiveSilenceMs = 0
                } else {
                    consecutiveSilenceMs += durationMs
                }

                if (isUtteranceComplete(hadLoud, capturedDurationMs, consecutiveSilenceMs)) {
                    break
                }
            }

            val pcmBytes = pcmStream.toByteArray()
            if (pcmBytes.isEmpty()) {
                isCapturing = false
                postCallback { onError("No audio captured") }
                return
            }

            val wavBytes = buildWav(pcmBytes)
            isCapturing = false
            postCallback { onAudioCaptured(wavBytes) }
        } catch (e: Exception) {
            isCapturing = false
            postCallback { onError("Audio capture error: ${e.message}") }
        }
    }

    override fun cancel() {
        isCapturing = false
        captureThread?.run {
            try {
                join(500)
            } catch (_: InterruptedException) {
            }
        }
        captureThread = null
        audioSource.stop()
        audioSource.release()
    }

    companion object {
        const val SAMPLE_RATE = 16000

        // Ported from AndroidSpeechRecognizerEngine's real-device-tuned
        // constants (lines 143-145) — 3s minimum utterance + 3s trailing
        // silence; empirically prevents both premature cutoffs and
        // indefinite hangs on the target device (S20 FE / Android 13).
        const val COMPLETE_SILENCE_MS = 3000L
        const val MINIMUM_LENGTH_MS = 3000L

        // Threshold corresponding to roughly -40 dB full-scale for 16-bit PCM.
        // -40 dB FS = 20 * log10(rms / 32767) => rms / 32767 = 10^(-2) = 0.01
        // => rms ≈ 327.67.  This is low enough that room tone consistently
        // falls below it on the target device (S20 FE) while normal speech
        // reliably exceeds it — same real-device-finding comment style as
        // AndroidSpeechRecognizerEngine lines 143-145.
        const val SILENCE_RMS_THRESHOLD = 328.0

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
                MediaRecorder.AudioSource.MIC,
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
    minimumLengthMs: Long = AndroidAudioCaptureEngine.MINIMUM_LENGTH_MS,
    completeSilenceMs: Long = AndroidAudioCaptureEngine.COMPLETE_SILENCE_MS,
): Boolean {
    return hadLoud && capturedDurationMs >= minimumLengthMs && consecutiveSilenceMs >= completeSilenceMs
}
