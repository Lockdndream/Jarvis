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
                capturedDurationMs = 2000,
                consecutiveSilenceMs = AndroidAudioCaptureEngine.COMPLETE_SILENCE_MS,
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
                consecutiveSilenceMs = AndroidAudioCaptureEngine.COMPLETE_SILENCE_MS,
            )
        )
    }

    @Test
    fun `isUtteranceComplete false when no loud has been detected yet`() {
        assertFalse(
            isUtteranceComplete(
                hadLoud = false,
                capturedDurationMs = 4000,
                consecutiveSilenceMs = AndroidAudioCaptureEngine.COMPLETE_SILENCE_MS,
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
        )

        assertEquals(1, errors.size)
        assertTrue(errors[0].contains("Failed to start"))
        assertEquals(0, captured.size)
    }

    @Test
    fun `loud then silence completes and calls onAudioCaptured exactly once`() {
        val chunks = mutableListOf<ShortArray>()
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
        )

        // Give it time to start reading
        Thread.sleep(100)
        engine.cancel()

        assertTrue(fakeSource.wasStopped)
        assertTrue(fakeSource.wasReleased)
    }

    // --- helpers ---

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
        }
    }
}
