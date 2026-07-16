package com.jarvis.wakewordspike

import android.Manifest
import android.app.ActivityManager
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Debug
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Button
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import kotlin.math.max
import kotlin.math.min

private const val TAG = "WakeWordSpike"
private const val SAMPLE_RATE = 16000
private const val FEATURE_STEP_SIZE_MS = 10
private const val PROBABILITY_CUTOFF = 0.97f
private const val SLIDING_WINDOW_SIZE = 5
private const val CHUNK_SAMPLES = SAMPLE_RATE / 1000 * FEATURE_STEP_SIZE_MS // 160 @ 16kHz/10ms

/**
 * Milestone 9B.5 disposable feasibility spike (D5, deferred since 9A/9B.0).
 *
 * NOT production code — proves only whether the home-assistant/android
 * microWakeWord reference architecture (NDK/CMake + TFLite Micro JNI) can be
 * vendored, built, and actually run inference on the real S20 FE. See
 * README.md and app/src/main/cpp/NOTICE.md for provenance/scope.
 */
class WakeWordSpikeActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private var audioRecord: AudioRecord? = null
    private var captureThread: Thread? = null
    @Volatile private var running = false

    private var detectionCount = 0
    private var lastDetectionAtMs: Long? = null
    private var callCount = 0L
    private var totalCallNanos = 0L
    private var maxCallNanos = 0L
    private var startedAtMs = 0L
    private var initError: String? = null

    private val uiHandler = Handler(Looper.getMainLooper())
    private val uiUpdateRunnable = object : Runnable {
        override fun run() {
            renderStatus()
            uiHandler.postDelayed(this, 1000)
        }
    }

    private val recordPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startCapture() else statusText.text = "RECORD_AUDIO denied"
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_wakeword_spike)
        statusText = findViewById(R.id.statusText)
        findViewById<Button>(R.id.startButton).setOnClickListener { onStartTapped() }
        findViewById<Button>(R.id.stopButton).setOnClickListener { stopCapture() }
    }

    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }

    private fun onStartTapped() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startCapture()
        } else {
            recordPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun loadModelBuffer(): MappedByteBuffer {
        val afd = assets.openFd("hey_jarvis.tflite")
        FileInputStream(afd.fileDescriptor).use { input ->
            return input.channel.map(FileChannel.MapMode.READ_ONLY, afd.startOffset, afd.declaredLength)
        }
    }

    @Suppress("MissingPermission") // checked by caller
    private fun startCapture() {
        if (running) return
        detectionCount = 0
        lastDetectionAtMs = null
        callCount = 0
        totalCallNanos = 0
        maxCallNanos = 0
        initError = null
        startedAtMs = System.currentTimeMillis()

        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = max(minBuf, CHUNK_SAMPLES * 2 * 4) // a few chunks of headroom
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferSize,
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            initError = "AudioRecord failed to initialize"
            statusText.text = initError
            record.release()
            return
        }
        audioRecord = record

        running = true
        captureThread = Thread {
            var detector: MicroWakeWord? = null
            try {
                val modelBuffer: ByteBuffer = loadModelBuffer()
                detector = MicroWakeWord(
                    modelBuffer = modelBuffer,
                    featureStepSizeMs = FEATURE_STEP_SIZE_MS,
                    probabilityCutoff = PROBABILITY_CUTOFF,
                    slidingWindowSize = SLIDING_WINDOW_SIZE,
                )
                Log.i(TAG, "MicroWakeWord engine initialized, model loaded, starting capture loop")

                record.startRecording()
                val buf = ShortArray(CHUNK_SAMPLES)
                while (running) {
                    val read = record.read(buf, 0, buf.size)
                    if (read <= 0) continue
                    val samples = if (read == buf.size) buf else buf.copyOf(read)

                    val t0 = System.nanoTime()
                    val detected = detector.processAudio(samples)
                    val elapsed = System.nanoTime() - t0

                    callCount++
                    totalCallNanos += elapsed
                    if (elapsed > maxCallNanos) maxCallNanos = elapsed

                    if (detected) {
                        detectionCount++
                        lastDetectionAtMs = System.currentTimeMillis()
                        Log.i(TAG, "WAKE WORD DETECTED (#$detectionCount)")
                        detector.reset()
                    }
                }
            } catch (e: Exception) {
                initError = "${e.javaClass.simpleName}: ${e.message}"
                Log.e(TAG, "Capture loop failed", e)
            } finally {
                detector?.close()
                record.stop()
                record.release()
            }
        }.also { it.start() }

        uiHandler.post(uiUpdateRunnable)
    }

    private fun stopCapture() {
        running = false
        captureThread?.join(2000)
        captureThread = null
        uiHandler.removeCallbacks(uiUpdateRunnable)
        renderStatus()
    }

    private fun renderStatus() {
        val elapsedS = if (startedAtMs > 0) (System.currentTimeMillis() - startedAtMs) / 1000 else 0
        val avgMs = if (callCount > 0) (totalCallNanos.toDouble() / callCount) / 1_000_000.0 else 0.0
        val maxMs = maxCallNanos / 1_000_000.0
        val lastDetAgoS = lastDetectionAtMs?.let { (System.currentTimeMillis() - it) / 1000 } ?: -1

        val nativeHeapKb = Debug.getNativeHeapAllocatedSize() / 1024
        val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memInfo = ActivityManager.MemoryInfo()
        am.getMemoryInfo(memInfo)
        val procMemInfo = Debug.MemoryInfo()
        Debug.getMemoryInfo(procMemInfo)
        val pssKb = procMemInfo.totalPss

        statusText.text = buildString {
            appendLine("running=$running elapsed=${elapsedS}s")
            appendLine("init_error=${initError ?: "none"}")
            appendLine("inference_calls=$callCount")
            appendLine("avg_latency_ms=${"%.2f".format(avgMs)}")
            appendLine("max_latency_ms=${"%.2f".format(maxMs)}")
            appendLine("detections=$detectionCount")
            appendLine("last_detection_ago_s=${if (lastDetAgoS >= 0) lastDetAgoS else "n/a"}")
            appendLine("native_heap_kb=$nativeHeapKb")
            append("proc_pss_kb=$pssKb")
        }
    }
}
