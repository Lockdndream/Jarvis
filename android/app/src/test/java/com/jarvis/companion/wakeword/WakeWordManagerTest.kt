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

private class FakeWakeWordEngine(
    private val detectOnCall: Int? = null,
    private val alwaysDetect: Boolean = false,
) : WakeWordEngine {
    var callCount = 0
    var closed = false
    var throwOnProcessAudio = false
    override fun processAudio(samples: ShortArray): Boolean {
        callCount++
        if (throwOnProcessAudio) throw RuntimeException("simulated native failure")
        return alwaysDetect || detectOnCall == callCount
    }
    override fun reset() {}
    override fun close() { closed = true }
}

private class FakeWakeWordAudioFocus : WakeWordAudioFocus {
    var requested = false
    var abandoned = false
    var requestCount = 0
    // Milestone 9B.10: lets a test simulate a re-request finding focus
    // still contested by another app (false) vs. already free (true,
    // the default) -- see resumeAfterVoiceSession()'s re-request fix.
    var grantOnRequest = true
    private var callback: ((Boolean) -> Unit)? = null
    override fun request(onFocusChange: (Boolean) -> Unit): Boolean {
        requested = true
        requestCount++
        callback = onFocusChange
        return grantOnRequest
    }
    override fun abandon() { abandoned = true }
    fun simulateFocusLost() { callback?.invoke(false) }
    fun simulateFocusRegained() { callback?.invoke(true) }
}

/** Never produces data on its own -- tests drive detection exclusively via
 * feedAudioForTest(). read() returns "no data yet" (0) every ~10ms rather
 * than blocking indefinitely, matching a real AudioRecord.read() for a
 * 10ms chunk (bounded by how fast the hardware actually produces that
 * much audio) -- this is what lets runCaptureLoop's own top-of-loop pause
 * check run promptly without needing to be unblocked from another thread
 * (Milestone 9B.10 mic-release fix). Returning 0 never triggers
 * onAudioChunk, so this still can't race the test's own feedAudioForTest
 * assertions. */
private class FakeAudioCaptureSource : AudioCaptureSource {
    var startCalled = false
    var released = false
    override fun start(): Boolean {
        startCalled = true
        return true
    }
    override fun read(buffer: ShortArray): Int {
        Thread.sleep(10)
        return 0
    }
    override fun stop() {}
    override fun release() { released = true }
}

/** Polls a real wall-clock condition, for asserting on WakeWordManager's
 * capture loop -- it runs on Dispatchers.Default (real threads, real
 * time), so runTest's virtual-time advanceUntilIdle() does not control or
 * observe it. */
private fun waitUntil(timeoutMs: Long = 2000, intervalMs: Long = 20, condition: () -> Boolean) {
    val deadline = System.currentTimeMillis() + timeoutMs
    while (!condition()) {
        if (System.currentTimeMillis() > deadline) {
            throw AssertionError("condition not met within ${timeoutMs}ms")
        }
        Thread.sleep(intervalMs)
    }
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
        // A manager's capture loop runs on a real Dispatchers.Default
        // thread until its captureJob is cancelled -- without this, a
        // test that starts a manager and never explicitly stops it leaks
        // a live polling loop into every subsequent test (Dispatchers.Default
        // is a small fixed pool), a real flake source found and fixed
        // during independent review, not merely theoretical.
        createdManagers.forEach { it.stop() }
        createdManagers.clear()
    }

    private fun manager(
        engine: () -> WakeWordEngine = { FakeWakeWordEngine() },
        audioFocus: WakeWordAudioFocus = NoOpWakeWordAudioFocus,
        audioSources: MutableList<FakeAudioCaptureSource>? = null,
    ) = WakeWordManager(
        config = config,
        engineFactory = engine,
        audioFocus = audioFocus,
        audioSourceFactory = { FakeAudioCaptureSource().also { audioSources?.add(it) } },
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
        // The composite case: focus lost first (still contested by another
        // app), then a VoiceSession opens and closes entirely while the
        // interruption is still active. Resuming must NOT land in
        // LISTENING -- that would mean capturing without focus.
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

        // Milestone 9B.10: resumeAfterVoiceSession() now re-requests focus
        // rather than trusting a possibly-stale flag -- deny the
        // re-request to simulate the interruption still being active when
        // the session closes.
        audioFocus.grantOnRequest = false
        m.resumeAfterVoiceSession()
        advanceUntilIdle()
        // Focus is still contested -- must resume into PAUSED_AUDIO_FOCUS, not LISTENING.
        assertEquals(WakeWordManager.State.PAUSED_AUDIO_FOCUS, m.state.value)

        audioFocus.simulateFocusRegained()
        advanceUntilIdle()
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
    }

    @Test
    fun `resumeAfterVoiceSession re-requests focus and recovers from a permanent loss that never sent a regain callback`() = runTest {
        // Milestone 9B.10 real-device finding: a *permanent* AUDIOFOCUS_LOSS
        // (e.g. VoiceActivity's own TTS playback requesting exclusive
        // focus) is never followed by an automatic regain callback from
        // Android -- simulateFocusLost() here with no matching
        // simulateFocusRegained() models exactly that. Before this fix,
        // audioFocusLost stayed stuck true forever and every subsequent
        // resumeAfterVoiceSession() call routed to PAUSED_AUDIO_FOCUS,
        // permanently disabling wake-word detection until the app was
        // killed and restarted -- reproduced on a real device (mic never
        // recorded again across 20+ minutes and several "Hey Jarvis"
        // attempts after the first conversation completed).
        val audioFocus = FakeWakeWordAudioFocus()
        val m = manager(audioFocus = audioFocus)
        m.start()
        advanceUntilIdle()

        m.pauseForVoiceSession()
        advanceUntilIdle()
        audioFocus.simulateFocusLost() // permanent loss; no regain callback will ever follow
        advanceUntilIdle()

        m.resumeAfterVoiceSession()
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
    fun `rapid repeated wake words only register the first detection`() = runTest {
        // Milestone 9B.9 Item 2: an engine that reports "detected" on
        // every single chunk simulates the user repeating the wake word
        // (or a sustained match) faster than any handoff could complete.
        // onAudioChunk() self-transitions to PAUSED_VOICE_SESSION on the
        // very first detection and early-returns for any state other than
        // LISTENING -- so every later chunk in the same burst must be
        // suppressed structurally, not by engine behavior.
        val engine = FakeWakeWordEngine(alwaysDetect = true)
        val m = manager(engine = { engine })
        val detections = mutableListOf<WakeWordDetection>()
        val job = launch { m.onDetected.collect { detections.add(it) } }
        advanceUntilIdle()

        m.start()
        advanceUntilIdle()
        repeat(5) { m.feedAudioForTest(ShortArray(160)) }
        advanceUntilIdle()

        assertEquals(1, detections.size)
        assertEquals(1, m.detectionCount.value)
        assertEquals(WakeWordManager.State.PAUSED_VOICE_SESSION, m.state.value)
        job.cancel()
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
    fun `pausing for a voice session releases the microphone and resuming reacquires it`() {
        // Milestone 9B.10 real-device finding: WakeWordManager's own
        // AudioRecord never released the microphone hardware while
        // paused, starving a concurrent SpeechRecognizer of real audio.
        // Not wrapped in runTest -- the capture loop runs on real
        // Dispatchers.Default threads, so this asserts against real
        // wall-clock state via waitUntil(), not virtual time.
        val sources = mutableListOf<FakeAudioCaptureSource>()
        val m = manager(audioSources = sources)
        m.start()
        waitUntil { sources.isNotEmpty() && sources[0].startCalled }

        m.pauseForVoiceSession()
        waitUntil { sources[0].released }
        assertEquals(WakeWordManager.State.PAUSED_VOICE_SESSION, m.state.value)
        assertEquals(WakeWordManager.AudioRecordState.NONE, m.audioRecordState.value)

        m.resumeAfterVoiceSession()
        waitUntil { sources.size >= 2 && sources[1].startCalled }
        assertEquals(WakeWordManager.State.LISTENING, m.state.value)
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
