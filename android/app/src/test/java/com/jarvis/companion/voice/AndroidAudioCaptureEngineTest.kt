package com.jarvis.companion.voice

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class AndroidAudioCaptureEngineTest {

    // --- computeRms ---

    @Test
    fun `computeRms returns zero for silent buffer`() {
        val samples = ShortArray(1600)
        val rms = computeRms(samples, samples.size)
        assertEquals(0.0, rms, 0.001)
    }

    @Test
    fun `computeRms returns non-zero for loud buffer`() {
        val samples = ShortArray(1600) { 20000 }
        val rms = computeRms(samples, samples.size)
        assertTrue(rms > AndroidAudioCaptureEngine.SILENCE_RMS_THRESHOLD)
    }

    @Test
    fun `computeRms uses only sampleCount not full array`() {
        val samples = ShortArray(1600) { 20000 }
        val rms = computeRms(samples, 800)
        assertTrue(rms > 0.0)
    }

    // --- computeNoiseFloor ---

    @Test
    fun `computeNoiseFloor empty list returns noiseFloorMin`() {
        assertEquals(50.0, computeNoiseFloor(emptyList(), 50.0), 0.001)
    }

    @Test
    fun `computeNoiseFloor single value returns that value when above min`() {
        assertEquals(100.0, computeNoiseFloor(listOf(100.0), 50.0), 0.001)
    }

    @Test
    fun `computeNoiseFloor floors at noiseFloorMin`() {
        assertEquals(50.0, computeNoiseFloor(listOf(10.0, 10.0, 10.0, 10.0, 10.0), 50.0), 0.001)
    }

    @Test
    fun `computeNoiseFloor uses lowest 20th percentile`() {
        val values = listOf(1.0, 2.0, 3.0, 4.0, 5.0, 100.0, 200.0, 300.0, 400.0, 500.0)
        assertEquals(1.5, computeNoiseFloor(values, 0.0), 0.001)
    }

    @Test
    fun `computeNoiseFloor with many identical values`() {
        val values = List(50) { 800.0 }
        assertEquals(800.0, computeNoiseFloor(values, 50.0), 0.001)
    }

    // --- buildWav ---

    @Test
    fun `buildWav produces valid RIFF WAVE header followed by PCM data`() {
        val pcm = ByteArray(480) // 10ms at 16kHz mono 16-bit = 320 samples * 2 bytes
        val wav = buildWav(pcm)

        assertEquals(44 + pcm.size, wav.size)

        val header = ByteBuffer.wrap(wav, 0, 44).apply { order(ByteOrder.LITTLE_ENDIAN) }

        val riff = ByteArray(4)
        header.get(riff)
        assertArrayEquals("RIFF".toByteArray(), riff)

        header.getInt() // totalDataLen
        val wave = ByteArray(4)
        header.get(wave)
        assertArrayEquals("WAVE".toByteArray(), wave)

        val fmt = ByteArray(4)
        header.get(fmt)
        assertArrayEquals("fmt ".toByteArray(), fmt)

        assertEquals(16, header.getInt()) // PCM chunk size
        assertEquals(1.toShort(), header.getShort()) // PCM format
        assertEquals(1.toShort(), header.getShort()) // mono
        assertEquals(AndroidAudioCaptureEngine.SAMPLE_RATE, header.getInt())
        assertEquals(AndroidAudioCaptureEngine.SAMPLE_RATE * 2, header.getInt()) // byte rate
        assertEquals(2.toShort(), header.getShort()) // block align
        assertEquals(16.toShort(), header.getShort()) // bits per sample

        val dataHeader = ByteArray(4)
        header.get(dataHeader)
        assertArrayEquals("data".toByteArray(), dataHeader)

        assertEquals(pcm.size, header.getInt())

        // Verify PCM data is appended
        val wavData = wav.sliceArray(44 until wav.size)
        assertArrayEquals(pcm, wavData)
    }

    // --- isUtteranceComplete ---

    @Test
    fun `isUtteranceComplete false when below minimum length`() {
        assertFalse(
            isUtteranceComplete(
                hadLoud = true,
                capturedDurationMs = 500,
                consecutiveSilenceMs = AndroidAudioCaptureEngine.SILENCE_DURATION_MS,
            )
        )
    }

    @Test
    fun `isUtteranceComplete false when never goes quiet`() {
        assertFalse(
            isUtteranceComplete(
                hadLoud = true,
                capturedDurationMs = 10000,
                consecutiveSilenceMs = 0,
            )
        )
    }

    @Test
    fun `isUtteranceComplete true when loud then silence after minimum length`() {
        assertTrue(
            isUtteranceComplete(
                hadLoud = true,
                capturedDurationMs = 4000,
                consecutiveSilenceMs = AndroidAudioCaptureEngine.SILENCE_DURATION_MS,
            )
        )
    }

    @Test
    fun `isUtteranceComplete false when no loud has been detected yet`() {
        assertFalse(
            isUtteranceComplete(
                hadLoud = false,
                capturedDurationMs = 4000,
                consecutiveSilenceMs = AndroidAudioCaptureEngine.SILENCE_DURATION_MS,
            )
        )
    }

    @Test
    fun `isUtteranceComplete false when silence not yet long enough`() {
        assertFalse(
            isUtteranceComplete(
                hadLoud = true,
                capturedDurationMs = 4000,
                consecutiveSilenceMs = 2000,
            )
        )
    }

    // --- Adaptive speech detection (TD-029 regression tests) ---

    enum class CaptureOutcome { COMPLETED, IDLE_TIMEOUT, MAX_DURATION, STILL_CAPTURING }

    data class CaptureSimResult(
        val outcome: CaptureOutcome,
        val capturedDurationMs: Long,
        val hadLoud: Boolean,
        val finalNoiseFloor: Double,
    )

    /**
     * Simulates the adaptive captureLoop logic over a sequence of per-chunk
     * RMS values.  Used to validate the adaptive noise-floor, speech detection,
     * and timeout behaviour without needing an AudioRecord mock.
     */
    private fun simulateAdaptiveCapture(
        rmsSequence: List<Double>,
        noiseFloorMin: Double = AndroidAudioCaptureEngine.NOISE_FLOOR_MIN,
        speechMultiplier: Double = AndroidAudioCaptureEngine.SPEECH_MULTIPLIER,
        silenceDurationMs: Long = AndroidAudioCaptureEngine.SILENCE_DURATION_MS,
        minCaptureDurationMs: Long = AndroidAudioCaptureEngine.MIN_CAPTURE_DURATION_MS,
        maxCaptureDurationMs: Long = AndroidAudioCaptureEngine.MAX_CAPTURE_DURATION_MS,
        idleTimeoutMs: Long = AndroidAudioCaptureEngine.IDLE_TIMEOUT_MS,
        primeChunks: Int = AndroidAudioCaptureEngine.NOISE_FLOOR_PRIME_CHUNKS,
        windowChunks: Int = AndroidAudioCaptureEngine.NOISE_FLOOR_WINDOW_CHUNKS,
        chunkDurationMs: Long = 100L,
    ): CaptureSimResult {
        var capturedDurationMs = 0L
        var consecutiveSilenceMs = 0L
        var hadLoud = false
        var primingComplete = false
        var noiseFloorFrozen = false
        val rmsWindow = ArrayDeque<Double>()
        var noiseFloor = noiseFloorMin

        for (chunkRms in rmsSequence) {
            capturedDurationMs += chunkDurationMs

            if (!primingComplete) {
                rmsWindow.addLast(chunkRms)
                while (rmsWindow.size > windowChunks) {
                    rmsWindow.removeFirst()
                }
                noiseFloor = computeNoiseFloor(rmsWindow.toList(), noiseFloorMin)
                if (rmsWindow.size >= primeChunks) {
                    primingComplete = true
                }
            } else {
                val threshold = noiseFloor * speechMultiplier

                if (chunkRms > threshold) {
                    hadLoud = true
                    consecutiveSilenceMs = 0
                    noiseFloorFrozen = true
                } else {
                    consecutiveSilenceMs += chunkDurationMs
                    if (!noiseFloorFrozen) {
                        rmsWindow.addLast(chunkRms)
                        while (rmsWindow.size > windowChunks) {
                            rmsWindow.removeFirst()
                        }
                        noiseFloor = computeNoiseFloor(rmsWindow.toList(), noiseFloorMin)
                    }
                }

                if (!hadLoud && capturedDurationMs >= idleTimeoutMs) {
                    return CaptureSimResult(CaptureOutcome.IDLE_TIMEOUT, capturedDurationMs, hadLoud, noiseFloor)
                }

                if (capturedDurationMs >= maxCaptureDurationMs) {
                    return CaptureSimResult(CaptureOutcome.MAX_DURATION, capturedDurationMs, hadLoud, noiseFloor)
                }

                if (isUtteranceComplete(hadLoud, capturedDurationMs, consecutiveSilenceMs,
                        minCaptureDurationMs, silenceDurationMs)) {
                    return CaptureSimResult(CaptureOutcome.COMPLETED, capturedDurationMs, hadLoud, noiseFloor)
                }
            }
        }

        return CaptureSimResult(CaptureOutcome.STILL_CAPTURING, capturedDurationMs, hadLoud, noiseFloor)
    }

    // TD-029 case 1: Quiet room — floor ~60, speech ~300+
    @Test
    fun `adaptive detection quiet room floor 60 speech 300`() {
        val rms = mutableListOf<Double>()
        repeat(10) { rms.add(60.0) }
        repeat(15) { rms.add(350.0) }
        repeat(30) { rms.add(60.0) }

        val result = simulateAdaptiveCapture(rms)
        assertEquals(CaptureOutcome.COMPLETED, result.outcome)
        assertTrue(result.hadLoud)
        assertTrue(result.finalNoiseFloor in 50.0..100.0)
        // Priming (5 chunks) + 5 noise + 15 speech + 30 silence = 55 chunks = 5500ms
        assertTrue(result.capturedDurationMs in 5000L..6000L)
    }

    // TD-029 case 2: Fan/wind noise — floor ~800, speech ~2500
    // With the OLD fixed threshold (328), RMS 800 is always "speech",
    // so consecutiveSilenceMs would never accumulate — capture would hang.
    @Test
    fun `adaptive detection fan noise floor 800 speech 2500`() {
        val rms = mutableListOf<Double>()
        repeat(10) { rms.add(800.0) }
        repeat(15) { rms.add(2500.0) }
        repeat(30) { rms.add(800.0) }

        val result = simulateAdaptiveCapture(rms)
        assertEquals(CaptureOutcome.COMPLETED, result.outcome)
        assertTrue(result.hadLoud)
        // Floor should converge around 800
        assertTrue(result.finalNoiseFloor in 700.0..900.0)
    }

    @Test
    fun `old fixed threshold would hang on fan noise sequence`() {
        // Demonstrate why the old fixed-328 threshold is broken:
        // With wind RMS at 800, every chunk is >= 328, so the old logic
        // (chunkRms >= 328 → reset consecutiveSilenceMs) never accumulates
        // silence — isUtteranceComplete never returns true.
        val rms = listOf(800.0, 800.0, 800.0, 2500.0, 2500.0, 800.0, 800.0, 800.0)

        var hadLoud = false
        var consecutiveSilenceMs = 0L
        var capturedMs = 0L
        for (chunk in rms) {
            capturedMs += 100
            if (chunk >= AndroidAudioCaptureEngine.SILENCE_RMS_THRESHOLD) {
                hadLoud = true
                consecutiveSilenceMs = 0
            } else {
                consecutiveSilenceMs += 100
            }
        }
        // After the whole sequence, no chunk was below 328, so no silence accumulated
        assertEquals(0L, consecutiveSilenceMs)
        assertTrue(hadLoud)
        // isUtteranceComplete would return false — the old code would hang
        assertFalse(isUtteranceComplete(hadLoud, capturedMs, consecutiveSilenceMs,
            AndroidAudioCaptureEngine.MIN_CAPTURE_DURATION_MS,
            AndroidAudioCaptureEngine.SILENCE_DURATION_MS))
    }

    // TD-029 case 3: Fan only — no speech, idle timeout fires at 10s
    @Test
    fun `adaptive detection fan only triggers idle timeout`() {
        val rms = mutableListOf<Double>()
        repeat(100) { rms.add(800.0) } // 10s of fan noise

        val result = simulateAdaptiveCapture(rms)
        assertEquals(CaptureOutcome.IDLE_TIMEOUT, result.outcome)
        assertFalse(result.hadLoud)
        assertEquals(10_000L, result.capturedDurationMs)
    }

    // TD-029 case 4: Pause-then-resume — short pause (< 3s) does not terminate
    @Test
    fun `adaptive detection pause then resume does not terminate early`() {
        val rms = mutableListOf<Double>()
        repeat(10) { rms.add(200.0) }
        repeat(10) { rms.add(8000.0) }
        repeat(15) { rms.add(200.0) } // 1500ms pause (< 3000ms silence threshold)
        repeat(10) { rms.add(8000.0) } // resumed speech
        repeat(30) { rms.add(200.0) }  // final silence

        val result = simulateAdaptiveCapture(rms)
        assertEquals(CaptureOutcome.COMPLETED, result.outcome)
        assertTrue(result.hadLoud)
        // Completion should happen during final silence, not during the 1.5s pause.
        // 10 noise + 10 speech + 15 pause + 10 resumed + 30 silence = 75 chunks = 7500ms
        assertEquals(7500L, result.capturedDurationMs)
    }

    // TD-029 case 5: Long continuous speech — floor must not drift up
    @Test
    fun `adaptive detection long speech does not corrupt noise floor`() {
        val rms = mutableListOf<Double>()
        repeat(10) { rms.add(60.0) }
        repeat(150) { rms.add(5000.0) } // 15s of sustained speech
        repeat(30) { rms.add(60.0) }    // silence

        val result = simulateAdaptiveCapture(rms)
        assertEquals(CaptureOutcome.COMPLETED, result.outcome)
        assertTrue(result.hadLoud)
        // Floor must NOT have drifted up to speech level. If it had, the
        // threshold would be too high and silence after speech would never
        // be detected.  Floor should stay at the pre-speech ambient level.
        assertTrue(
            "noise floor drifted to ${result.finalNoiseFloor} — should be near pre-speech level ~60",
            result.finalNoiseFloor < 200.0
        )
    }

    // TD-029 case 6: MAX_CAPTURE_DURATION_MS backstop (60s)
    @Test
    fun `adaptive detection max duration backstop at 60s`() {
        val rms = mutableListOf<Double>()
        repeat(10) { rms.add(200.0) }
        // Sustained speech with no silence gaps — would never naturally terminate
        repeat(600) { rms.add(8000.0) }

        val result = simulateAdaptiveCapture(rms)
        assertEquals(CaptureOutcome.MAX_DURATION, result.outcome)
        assertTrue(result.hadLoud)
        assertTrue(result.capturedDurationMs >= 60_000L)
    }

    // TD-029 case 7: MIN_CAPTURE_DURATION_MS — cannot complete before 1000ms
    @Test
    fun `adaptive detection min capture duration blocks premature completion`() {
        // Even if silence and speech conditions are met, below 1000ms it's blocked
        assertFalse(
            isUtteranceComplete(
                hadLoud = true,
                capturedDurationMs = 500,
                consecutiveSilenceMs = 3000,
                minimumLengthMs = AndroidAudioCaptureEngine.MIN_CAPTURE_DURATION_MS,
                completeSilenceMs = AndroidAudioCaptureEngine.SILENCE_DURATION_MS,
            )
        )
        // At 1500ms it should fire
        assertTrue(
            isUtteranceComplete(
                hadLoud = true,
                capturedDurationMs = 1500,
                consecutiveSilenceMs = 3000,
                minimumLengthMs = AndroidAudioCaptureEngine.MIN_CAPTURE_DURATION_MS,
                completeSilenceMs = AndroidAudioCaptureEngine.SILENCE_DURATION_MS,
            )
        )
    }

    // TD-029 case 8: NOISE_FLOOR_MIN — near-silent room produces sane behaviour
    @Test
    fun `adaptive detection near silent room floors at NOISE_FLOOR_MIN`() {
        val rms = mutableListOf<Double>()
        repeat(10) { rms.add(0.5) }   // ambient near zero
        repeat(15) { rms.add(300.0) }  // speech
        repeat(30) { rms.add(0.5) }    // silence

        val result = simulateAdaptiveCapture(rms)
        assertEquals(CaptureOutcome.COMPLETED, result.outcome)
        assertTrue(result.hadLoud)
        // Floor must be >= NOISE_FLOOR_MIN (50), not near-zero
        assertTrue(
            "noise floor is ${result.finalNoiseFloor}, expected >= 50.0",
            result.finalNoiseFloor >= 50.0
        )
    }

    // --- AndroidAudioCaptureEngine integration ---

    @Test
    fun `startCapture when not available calls onError`() {
        val fakeSource = FakeAudioSource(emptyList()).apply { available = false }
        val engine = AndroidAudioCaptureEngine(fakeSource)
        val errors = mutableListOf<String>()
        val captured = mutableListOf<ByteArray>()

        engine.startCapture(
            onAudioCaptured = { captured.add(it) },
            onError = { errors.add(it) },
            onIdleTimeout = {},
        )

        assertEquals(1, errors.size)
        assertTrue(errors[0].contains("not available"))
        assertEquals(0, captured.size)
    }

    @Test
    fun `startCapture when recording fails calls onError`() {
        val fakeSource = FakeAudioSource(emptyList()).apply { startRecordingResult = false }
        val engine = AndroidAudioCaptureEngine(fakeSource)
        val errors = mutableListOf<String>()
        val captured = mutableListOf<ByteArray>()

        engine.startCapture(
            onAudioCaptured = { captured.add(it) },
            onError = { errors.add(it) },
            onIdleTimeout = {},
        )

        assertEquals(1, errors.size)
        assertTrue(errors[0].contains("Failed to start"))
        assertEquals(0, captured.size)
    }

    @Test
    fun `loud then silence completes and calls onAudioCaptured exactly once`() {
        val chunks = mutableListOf<ShortArray>()
        // 500ms priming silence so the noise floor stabilises before speech
        repeat(5) {
            chunks.add(generateSilenceChunk())
        }
        // 3000ms of loud (30 chunks of 100ms = 1600 samples)
        repeat(30) {
            chunks.add(generateLoudChunk())
        }
        // 3000ms of silence (30 chunks)
        repeat(30) {
            chunks.add(generateSilenceChunk())
        }
        // Extra silence beyond threshold
        repeat(5) {
            chunks.add(generateSilenceChunk())
        }

        val fakeSource = FakeAudioSource(chunks)
        val engine = AndroidAudioCaptureEngine(fakeSource) { it() }
        val latch = CountDownLatch(1)
        val captured = mutableListOf<ByteArray>()
        val errors = mutableListOf<String>()

        engine.startCapture(
            onAudioCaptured = {
                captured.add(it)
                latch.countDown()
            },
            onError = { errors.add(it) },
            onIdleTimeout = {},
        )

        assertTrue(latch.await(5, TimeUnit.SECONDS))
        assertEquals(0, errors.size)
        assertEquals(1, captured.size)
        val wav = captured[0]
        assertTrue(wav.size > 44)
        // Verify RIFF header
        val header = ByteBuffer.wrap(wav, 0, 4)
        val riff = ByteArray(4)
        header.get(riff)
        assertArrayEquals("RIFF".toByteArray(), riff)
    }

    @Test
    fun `onAudioCaptured callback does not run on the capture thread`() {
        val chunks = mutableListOf<ShortArray>()
        // 500ms priming silence
        repeat(5) {
            chunks.add(generateSilenceChunk())
        }
        // 3000ms of loud (30 chunks of 100ms = 1600 samples)
        repeat(30) {
            chunks.add(generateLoudChunk())
        }
        // 3000ms of silence (30 chunks)
        repeat(30) {
            chunks.add(generateSilenceChunk())
        }
        // Extra silence beyond threshold
        repeat(5) {
            chunks.add(generateSilenceChunk())
        }

        val fakeSource = FakeAudioSource(chunks)
        val callbackExecutor = java.util.concurrent.Executors.newSingleThreadExecutor()
        val engine = AndroidAudioCaptureEngine(fakeSource) { action ->
            callbackExecutor.execute(action)
        }
        val latch = CountDownLatch(1)
        val callbackThreadIds = mutableListOf<Long>()
        val errors = mutableListOf<String>()

        engine.startCapture(
            onAudioCaptured = {
                callbackThreadIds.add(Thread.currentThread().id)
                latch.countDown()
            },
            onError = { errors.add(it) },
            onIdleTimeout = {},
        )

        assertTrue(latch.await(5, TimeUnit.SECONDS))
        callbackExecutor.shutdown()
        assertEquals(0, errors.size)
        assertEquals(1, callbackThreadIds.size)

        val callbackThreadId = callbackThreadIds[0]
        val captureThreadId = fakeSource.lastReadThreadId
        assertNotNull(captureThreadId)
        assertTrue(
            "onAudioCaptured ran on the capture thread ($callbackThreadId == $captureThreadId)",
            callbackThreadId != captureThreadId,
        )
    }

    @Test
    fun `cancel during capture calls audioSource stop and release`() {
        val chunks = mutableListOf<ShortArray>()
        // Feed loud audio that keeps going
        repeat(100) {
            chunks.add(generateLoudChunk())
        }

        val fakeSource = FakeAudioSource(chunks)
        val engine = AndroidAudioCaptureEngine(fakeSource)
        val captured = mutableListOf<ByteArray>()
        val errors = mutableListOf<String>()

        engine.startCapture(
            onAudioCaptured = { captured.add(it) },
            onError = { errors.add(it) },
            onIdleTimeout = {},
        )

        // Give it time to start reading
        Thread.sleep(100)
        engine.cancel()

        assertTrue(fakeSource.wasStopped)
        assertTrue(fakeSource.wasReleased)
    }

    // --- Bug regression tests ---

    @Test
    fun `audioSource stop and release called on normal completion`() {
        val chunks = mutableListOf<ShortArray>()
        // 500ms priming silence
        repeat(5) {
            chunks.add(generateSilenceChunk())
        }
        repeat(30) {
            chunks.add(generateLoudChunk())
        }
        repeat(30) {
            chunks.add(generateSilenceChunk())
        }
        repeat(5) {
            chunks.add(generateSilenceChunk())
        }

        val fakeSource = FakeAudioSource(chunks)
        val engine = AndroidAudioCaptureEngine(fakeSource) { it() }
        val latch = CountDownLatch(1)
        val captured = mutableListOf<ByteArray>()

        engine.startCapture(
            onAudioCaptured = {
                captured.add(it)
                latch.countDown()
            },
            onError = {},
            onIdleTimeout = {},
        )

        assertTrue(latch.await(5, TimeUnit.SECONDS))
        assertEquals(1, captured.size)
        assertTrue(fakeSource.releasedLatch.await(5, TimeUnit.SECONDS))
        assertTrue(fakeSource.wasStopped)
        assertTrue(fakeSource.wasReleased)
    }

    @Test
    fun `cancel prevents onAudioCaptured and onError callbacks`() {
        val chunks = mutableListOf<ShortArray>()
        repeat(100) {
            chunks.add(generateLoudChunk())
        }

        val fakeSource = BlockableFakeAudioSource(chunks)
        val engine = AndroidAudioCaptureEngine(fakeSource) { it() }
        val captured = mutableListOf<ByteArray>()
        val errors = mutableListOf<String>()

        engine.startCapture(
            onAudioCaptured = { captured.add(it) },
            onError = { errors.add(it) },
            onIdleTimeout = {},
        )

        Thread.sleep(100)
        engine.cancel()

        assertTrue(captured.isEmpty())
        assertTrue(errors.isEmpty())
    }

    // --- helpers ---

    private class BlockableFakeAudioSource(
        private val chunks: List<ShortArray>,
    ) : AudioSource {

        var available = true
        var startRecordingResult = true
        var wasStarted = false
        var wasStopped = false
        var wasReleased = false
        var lastReadThreadId: Long? = null

        private val readLatch = CountDownLatch(1)
        private var chunkIndex = 0
        private var sampleOffset = 0

        override fun isAvailable(): Boolean = available

        override fun startRecording(): Boolean {
            wasStarted = true
            return startRecordingResult
        }

        override fun read(buffer: ShortArray): Int {
            lastReadThreadId = Thread.currentThread().id
            try {
                readLatch.await()
            } catch (_: InterruptedException) {
            }
            if (chunkIndex >= chunks.size) return -1
            var destOffset = 0
            while (destOffset < buffer.size && chunkIndex < chunks.size) {
                val chunk = chunks[chunkIndex]
                val remaining = chunk.size - sampleOffset
                val toCopy = minOf(remaining, buffer.size - destOffset)
                System.arraycopy(chunk, sampleOffset, buffer, destOffset, toCopy)
                destOffset += toCopy
                sampleOffset += toCopy
                if (sampleOffset >= chunk.size) {
                    chunkIndex++
                    sampleOffset = 0
                }
            }
            return destOffset
        }

        override fun stop() {
            wasStopped = true
            readLatch.countDown()
        }

        override fun release() {
            wasReleased = true
        }
    }

    private fun generateLoudChunk(): ShortArray {
        return ShortArray(AndroidAudioCaptureEngine.READ_BUFFER_SIZE_SAMPLES) {
            (20000 * Math.sin(2.0 * Math.PI * 440.0 * it / AndroidAudioCaptureEngine.SAMPLE_RATE)).toInt()
                .toShort()
        }
    }

    private fun generateSilenceChunk(): ShortArray {
        return ShortArray(AndroidAudioCaptureEngine.READ_BUFFER_SIZE_SAMPLES)
    }

    private class FakeAudioSource(
        private val chunks: List<ShortArray>,
    ) : AudioSource {

        var available = true
        var startRecordingResult = true
        var wasStarted = false
        var wasStopped = false
        var wasReleased = false
        var lastReadThreadId: Long? = null

        // stop()/release() run on the capture thread, inside captureLoop()'s
        // finally block, which executes AFTER the onAudioCaptured/onError
        // callback (a CountDownLatch.countDown() in that callback only
        // establishes happens-before for actions BEFORE it in program order
        // — not for the finally block that runs after). A test thread that
        // only awaits the callback's latch can observe wasStopped/wasReleased
        // as still false, since nothing guarantees the finally block has run
        // yet. This latch gives release() its own happens-before edge.
        val releasedLatch = java.util.concurrent.CountDownLatch(1)

        private var chunkIndex = 0
        private var sampleOffset = 0

        override fun isAvailable(): Boolean = available

        override fun startRecording(): Boolean {
            wasStarted = true
            return startRecordingResult
        }

        override fun read(buffer: ShortArray): Int {
            lastReadThreadId = Thread.currentThread().id
            if (chunkIndex >= chunks.size) return -1
            var destOffset = 0
            while (destOffset < buffer.size && chunkIndex < chunks.size) {
                val chunk = chunks[chunkIndex]
                val remaining = chunk.size - sampleOffset
                val toCopy = minOf(remaining, buffer.size - destOffset)
                System.arraycopy(chunk, sampleOffset, buffer, destOffset, toCopy)
                destOffset += toCopy
                sampleOffset += toCopy
                if (sampleOffset >= chunk.size) {
                    chunkIndex++
                    sampleOffset = 0
                }
            }
            return destOffset
        }

        override fun stop() {
            wasStopped = true
        }

        override fun release() {
            wasReleased = true
            releasedLatch.countDown()
        }
    }
}
