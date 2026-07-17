package com.jarvis.companion.wakeword

import com.jarvis.companion.core.SecureConfigStore
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

private class FakeWakeWordEngine(private val detectOnCall: Int? = null) : WakeWordEngine {
    var callCount = 0
    var closed = false
    var throwOnProcessAudio = false
    override fun processAudio(samples: ShortArray): Boolean {
        callCount++
        if (throwOnProcessAudio) throw RuntimeException("simulated native failure")
        return detectOnCall == callCount
    }
    override fun reset() {}
    override fun close() { closed = true }
}

private class FakeWakeWordAudioFocus : WakeWordAudioFocus {
    var requested = false
    var abandoned = false
    private var callback: ((Boolean) -> Unit)? = null
    override fun request(onFocusChange: (Boolean) -> Unit): Boolean {
        requested = true
        callback = onFocusChange
        return true
    }
    override fun abandon() { abandoned = true }
    fun simulateFocusLost() { callback?.invoke(false) }
    fun simulateFocusRegained() { callback?.invoke(true) }
}

/** Never produces data on its own -- tests drive detection exclusively via
 * feedAudioForTest(). Blocks in read() until stop() is called, simulating
 * "waiting for audio" without racing the test's own assertions the way the
 * real AudioRecord-backed source would (whose APIs return meaningless
 * stubbed defaults in a JVM unit test). */
private class FakeAudioCaptureSource : AudioCaptureSource {
    @Volatile private var stopped = false
    var startCalled = false
    var released = false
    override fun start(): Boolean {
        startCalled = true
        return true
    }
    override fun read(buffer: ShortArray): Int {
        while (!stopped) {
            Thread.sleep(20)
        }
        return -1
    }
    override fun stop() { stopped = true }
    override fun release() { released = true }
}

@OptIn(ExperimentalCoroutinesApi::class)
class WakeWordManagerTest {

    private lateinit var config: WakeWordConfigRepository
    private lateinit var store: SecureConfigStore

    private val createdManagers = mutableListOf<WakeWordManager>()

    @Before
    fun setUp() {
        store = mock(SecureConfigStore::class.java)
        `when`(store.getString("wakeword_enabled")).thenReturn("true")
        config = WakeWordConfigRepository(store)
    }

    @After
    fun tearDown() {
        // FakeAudioCaptureSource.read() blocks a real Dispatchers.Default
        // thread until stop() unblocks it -- without this, a test that
        // starts a manager and never explicitly stops it leaks that thread
        // into every subsequent test (Dispatchers.Default is a small fixed
        // pool), a real flake source found and fixed during independent
        // review, not merely theoretical.
        createdManagers.forEach { it.stop() }
        createdManagers.clear()
    }

    private fun manager(
        engine: () -> WakeWordEngine = { FakeWakeWordEngine() },
        audioFocus: WakeWordAudioFocus = NoOpWakeWordAudioFocus,
    ) = WakeWordManager(
        config = config,
        engineFactory = engine,
        audioFocus = audioFocus,
        audioSourceFactory = { FakeAudioCaptureSource() },
    ).also { createdManagers.add(it) }

    @Test
    fun `initial state is STOPPED`() {
        assertEquals(WakeWordManager.State.STOPPED, manager().state.value)
    }

    @Test
    fun `start is a no-op when config is disabled`() = runTest {
        `when`(store.getString("wakeword_enabled")).thenReturn("false")
        val m = manager()
        m.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.STOPPED, m.state.value)
        assertEquals(false, m.engineLoaded.value)
    }

    @Test
    fun `start creates the engine and transitions to LISTENING`() = runTest {
        val m = manager()
        m.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
        assertEquals(true, m.engineLoaded.value)
    }

    @Test
    fun `start ignored when already running (illegal transition)`() = runTest {
        val m = manager()
        m.start()
        advanceUntilIdle()
        val firstDetectionCount = m.detectionCount.value
        m.start() // second call while already LISTENING
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
        assertEquals(firstDetectionCount, m.detectionCount.value)
    }

    @Test
    fun `engine creation failure transitions to ERROR, not a crash`() = runTest {
        val m = manager(engine = { throw IllegalStateException("model missing") })
        m.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.ERROR, m.state.value)
        assertEquals(false, m.engineLoaded.value)
    }

    @Test
    fun `pauseForVoiceSession transitions from LISTENING to PAUSED_VOICE_SESSION`() = runTest {
        val m = manager()
        m.start()
        advanceUntilIdle()

        m.pauseForVoiceSession()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.PAUSED_VOICE_SESSION, m.state.value)
    }

    @Test
    fun `resumeAfterVoiceSession transitions back to LISTENING`() = runTest {
        val m = manager()
        m.start()
        advanceUntilIdle()
        m.pauseForVoiceSession()
        advanceUntilIdle()

        m.resumeAfterVoiceSession()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
    }

    @Test
    fun `resumeAfterVoiceSession is a no-op from LISTENING (illegal transition)`() = runTest {
        val m = manager()
        m.start()
        advanceUntilIdle()

        m.resumeAfterVoiceSession() // never paused
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
    }

    @Test
    fun `audio focus loss pauses and regain resumes listening`() = runTest {
        val audioFocus = FakeWakeWordAudioFocus()
        val m = manager(audioFocus = audioFocus)
        m.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
        assertEquals(true, audioFocus.requested)

        audioFocus.simulateFocusLost()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.PAUSED_AUDIO_FOCUS, m.state.value)

        audioFocus.simulateFocusRegained()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
    }

    @Test
    fun `voice session pause while audio focus already lost resumes into PAUSED_AUDIO_FOCUS`() = runTest {
        // The composite case: focus lost first, then a VoiceSession opens
        // and closes entirely while focus is still gone. Resuming must NOT
        // land in LISTENING -- that would mean capturing without focus.
        val audioFocus = FakeWakeWordAudioFocus()
        val m = manager(audioFocus = audioFocus)
        m.start()
        advanceUntilIdle()

        audioFocus.simulateFocusLost()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.PAUSED_AUDIO_FOCUS, m.state.value)

        m.pauseForVoiceSession()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.PAUSED_VOICE_SESSION, m.state.value)

        m.resumeAfterVoiceSession()
        advanceUntilIdle()
        // Focus was never regained -- must resume into PAUSED_AUDIO_FOCUS, not LISTENING.
        assertEquals(WakeWordManager.State.PAUSED_AUDIO_FOCUS, m.state.value)

        audioFocus.simulateFocusRegained()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
    }

    @Test
    fun `voice session pause while audio focus is fine resumes into LISTENING`() = runTest {
        val audioFocus = FakeWakeWordAudioFocus()
        val m = manager(audioFocus = audioFocus)
        m.start()
        advanceUntilIdle()

        m.pauseForVoiceSession()
        advanceUntilIdle()
        m.resumeAfterVoiceSession()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
    }

    @Test
    fun `detection self-pauses, increments detectionCount, and emits onDetected`() = runTest {
        val m = manager(engine = { FakeWakeWordEngine(detectOnCall = 1) })
        val detections = mutableListOf<WakeWordDetection>()
        val job = launch { m.onDetected.collect { detections.add(it) } }
        advanceUntilIdle() // let the collector actually subscribe before anything emits (replay=0 -- a late subscriber gets nothing)

        m.start()
        m.feedAudioForTest(ShortArray(160))
        advanceUntilIdle()

        assertEquals(1, detections.size)
        assertEquals(1, m.detectionCount.value)
        assertEquals(WakeWordManager.State.PAUSED_VOICE_SESSION, m.state.value)
        assertTrue(m.lastDetectionAtMs.value != null)
        job.cancel()
    }

    @Test
    fun `no detection while paused for voice session`() = runTest {
        // detectOnCall=1 would fire on the very next chunk, but we're paused.
        val m = manager(engine = { FakeWakeWordEngine(detectOnCall = 1) })
        m.start()
        advanceUntilIdle()
        m.pauseForVoiceSession()
        advanceUntilIdle()

        m.feedAudioForTest(ShortArray(160))
        advanceUntilIdle()

        assertEquals(0, m.detectionCount.value)
    }

    @Test
    fun `framesProcessed and latency stats accumulate across chunks`() = runTest {
        val m = manager()
        m.start()
        advanceUntilIdle()

        m.feedAudioForTest(ShortArray(160))
        m.feedAudioForTest(ShortArray(160))
        m.feedAudioForTest(ShortArray(160))
        advanceUntilIdle()

        assertEquals(3L, m.framesProcessed.value)
        assertTrue(m.avgLatencyMs.value >= 0.0)
    }

    @Test
    fun `processAudio exception increments inferenceErrorCount and stays LISTENING`() = runTest {
        val engine = FakeWakeWordEngine().apply { throwOnProcessAudio = true }
        val m = manager(engine = { engine })
        m.start()
        advanceUntilIdle()

        m.feedAudioForTest(ShortArray(160))
        advanceUntilIdle()

        assertEquals(1, m.inferenceErrorCount.value)
        assertEquals(0L, m.framesProcessed.value)
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
    }

    @Test
    fun `stop tears down cleanly, closes engine, abandons focus, and can be started again`() = runTest {
        val audioFocus = FakeWakeWordAudioFocus()
        val engine = FakeWakeWordEngine()
        val m = manager(engine = { engine }, audioFocus = audioFocus)
        m.start()
        advanceUntilIdle()

        m.stop()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.STOPPED, m.state.value)
        assertEquals(true, engine.closed)
        assertEquals(true, audioFocus.abandoned)
        assertEquals(false, m.engineLoaded.value)

        m.start()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
    }

    @Test
    fun `stop from STOPPED is a no-op`() = runTest {
        val m = manager()
        m.stop()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.STOPPED, m.state.value)
    }

    @Test
    fun `stop abandons audio focus`() = runTest {
        val audioFocus = FakeWakeWordAudioFocus()
        val m = manager(audioFocus = audioFocus)
        m.start()
        advanceUntilIdle()
        m.stop()
        advanceUntilIdle()
        assertEquals(true, audioFocus.abandoned)
    }

    @Test
    fun `engineStartedAtMs is set on start and cleared on stop`() = runTest {
        val m = manager()
        m.start()
        advanceUntilIdle()
        assertTrue(m.engineStartedAtMs.value != null)

        m.stop()
        advanceUntilIdle()
        assertEquals(null, m.engineStartedAtMs.value)
    }
}
