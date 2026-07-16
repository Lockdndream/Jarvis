package com.jarvis.companion.voice

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PlaybackManagerTest {

    private val testDispatcher = StandardTestDispatcher()
    private val fakeTts = FakeTtsEngine()
    private val fakeAudioFocus = FakeAudioFocusOwner()
    private lateinit var playbackManager: PlaybackManager

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        playbackManager = PlaybackManager(
            audioFocus = fakeAudioFocus,
            ttsEngine = fakeTts,
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `speak queues utterances and plays in FIFO order`() = runTest {
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.speak("first")
        playbackManager.speak("second")
        advanceUntilIdle()

        assertEquals(listOf("first", "second"), fakeTts.spokenTexts)
        assertFalse(playbackManager.isSpeaking.value)
    }

    @Test
    fun `highPriority utterance jumps ahead of already queued normal items`() = runTest {
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.speak("normal")
        playbackManager.speak("normal two")
        playbackManager.speak("urgent", highPriority = true)
        advanceUntilIdle()

        assertEquals(listOf("normal", "urgent", "normal two"), fakeTts.spokenTexts)
    }

    @Test
    fun `cancel stops current speech and clears queue`() = runTest {
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.speak("first")
        playbackManager.speak("second")
        playbackManager.cancel()
        advanceUntilIdle()

        assertEquals(listOf("first"), fakeTts.spokenTexts)
        assertTrue(fakeTts.wasStopped)
        assertFalse(playbackManager.isSpeaking.value)
    }

    @Test
    fun `focus is requested before speaking and abandoned after queue drains`() = runTest {
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.speak("single")
        advanceUntilIdle()

        assertTrue(fakeAudioFocus.requestCount >= 1)
        // After queue drains, focus is abandoned
        assertEquals(1, fakeAudioFocus.abandonCount)
        assertFalse(playbackManager.isSpeaking.value)
    }

    @Test
    fun `focus is not abandoned between consecutive queued utterances`() = runTest {
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.speak("first")
        playbackManager.speak("second")
        advanceUntilIdle()

        // Focus requested before each utterance, but only abandoned once
        // when the final utterance finishes and the queue is empty.
        assertEquals(2, fakeAudioFocus.requestCount)
        assertEquals(1, fakeAudioFocus.abandonCount)
    }

    @Test
    fun `cancel when idle does not trigger focus abandon`() = runTest {
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.cancel()
        advanceUntilIdle()

        assertEquals(0, fakeAudioFocus.abandonCount)
    }

    @Test
    fun `speak before init queues utterances and plays once ready`() = runTest {
        playbackManager.speak("deferred")
        playbackManager.speak("also deferred")
        advanceUntilIdle()

        // Nothing spoken yet — engine not ready
        assertTrue(fakeTts.spokenTexts.isEmpty())

        playbackManager.init()
        advanceUntilIdle()

        assertEquals(listOf("deferred", "also deferred"), fakeTts.spokenTexts)
    }

    @Test
    fun `setVolume is passed to TTS engine on speak`() = runTest {
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.setVolume(0.5f)
        playbackManager.speak("quiet")
        advanceUntilIdle()

        assertEquals(1, fakeTts.spokenVolumes.size)
        assertEquals(0.5f, fakeTts.spokenVolumes[0], 0.001f)
    }

    @Test
    fun `setVolume clamps to 0-1 range`() = runTest {
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.setVolume(-0.5f)
        playbackManager.speak("below min")
        advanceUntilIdle()
        assertEquals(0.0f, fakeTts.spokenVolumes[0], 0.001f)

        playbackManager.setVolume(2.0f)
        playbackManager.speak("above max")
        advanceUntilIdle()
        assertEquals(1.0f, fakeTts.spokenVolumes[1], 0.001f)
    }

    @Test
    fun `speak with focus denied still attempts TTS`() = runTest {
        fakeAudioFocus.requestFocusResult = false
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.speak("denied focus")
        advanceUntilIdle()

        assertEquals(listOf("denied focus"), fakeTts.spokenTexts)
        assertEquals(1, fakeAudioFocus.requestCount)
    }

    @Test
    fun `TTS engine failure moves to next utterance`() = runTest {
        fakeTts.shouldFailOnce = true
        playbackManager.init()
        advanceUntilIdle()

        playbackManager.speak("fails")
        playbackManager.speak("recovers")
        advanceUntilIdle()

        // First speak returns false, onUtteranceDone is called
        // manually by speakNext, which advances to next item.
        assertEquals(listOf("recovers"), fakeTts.spokenTexts)
    }

    @Test
    fun `shutdown stops engine abandons focus and resets ready`() = runTest {
        playbackManager.init()
        advanceUntilIdle()
        assertTrue(playbackManager.isReady.value)

        playbackManager.shutdown()

        assertTrue(fakeTts.wasStopped)
        assertEquals(1, fakeAudioFocus.abandonCount)
        assertFalse(playbackManager.isReady.value)
        assertFalse(playbackManager.isSpeaking.value)
    }

    @Test
    fun `speak after init does nothing if ready is false`() = runTest {
        // Never call init — engine not ready
        playbackManager.speak("ghost")
        advanceUntilIdle()

        assertTrue(fakeTts.spokenTexts.isEmpty())
    }

    @Test
    fun `init is idempotent`() = runTest {
        playbackManager.init()
        advanceUntilIdle()
        assertTrue(playbackManager.isReady.value)

        playbackManager.init()
        advanceUntilIdle()
        assertTrue(playbackManager.isReady.value)
    }
}

private class FakeTtsEngine : PlaybackManager.TtsEngine {

    override val isReady: Boolean = true

    override var onUtteranceDone: (String) -> Unit = {}

    val spokenTexts = mutableListOf<String>()
    val spokenVolumes = mutableListOf<Float>()
    var shouldFail = false
    var shouldFailOnce = false
    var wasStopped = false

    override fun init(onReady: () -> Unit) {
        onReady()
    }

    override fun speak(text: String, volume: Float, utteranceId: String): Boolean {
        if (shouldFail) {
            return false
        }
        if (shouldFailOnce) {
            shouldFailOnce = false
            return false
        }
        spokenTexts.add(text)
        spokenVolumes.add(volume)
        onUtteranceDone(utteranceId)
        return true
    }

    override fun stop() {
        wasStopped = true
    }

    override fun shutdown() {
        wasStopped = true
    }
}

private class FakeAudioFocusOwner : AudioFocusOwner {

    var requestFocusResult = true
    var requestCount = 0
    var abandonCount = 0

    override fun requestFocus(): Boolean {
        requestCount++
        return requestFocusResult
    }

    override fun abandonFocus(): Boolean {
        abandonCount++
        return true
    }
}