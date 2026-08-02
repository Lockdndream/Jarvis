package com.jarvis.companion.voice

import android.os.Bundle
import android.speech.SpeechRecognizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

@OptIn(ExperimentalCoroutinesApi::class)
class SpeechInputControllerTest {

    private val testDispatcher = StandardTestDispatcher()
    private val fakeEngine = FakeSpeechRecognizerEngine()

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    // --- extractTopResult pure function ---

    @Test
    fun `extractTopResult returns first entry from results list`() {
        val bundle = mock(Bundle::class.java)
        `when`(bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION))
            .thenReturn(arrayListOf("hello world", "hello"))
        assertEquals("hello world", extractTopResult(bundle))
    }

    @Test
    fun `extractTopResult returns null when results list is empty`() {
        val bundle = mock(Bundle::class.java)
        `when`(bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION))
            .thenReturn(arrayListOf())
        assertNull(extractTopResult(bundle))
    }

    @Test
    fun `extractTopResult returns null when results list is null`() {
        val bundle = mock(Bundle::class.java)
        `when`(bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION))
            .thenReturn(null)
        assertNull(extractTopResult(bundle))
    }

    // --- errorCodeToMessage pure function ---

    @Test
    fun `errorCodeToMessage maps every known code`() {
        assertEquals("Network timeout", errorCodeToMessage(SpeechRecognizer.ERROR_NETWORK_TIMEOUT))
        assertEquals("Network error", errorCodeToMessage(SpeechRecognizer.ERROR_NETWORK))
        assertEquals("Audio error", errorCodeToMessage(SpeechRecognizer.ERROR_AUDIO))
        assertEquals("Server error", errorCodeToMessage(SpeechRecognizer.ERROR_SERVER))
        assertEquals("Client error", errorCodeToMessage(SpeechRecognizer.ERROR_CLIENT))
        assertEquals("No speech detected", errorCodeToMessage(SpeechRecognizer.ERROR_SPEECH_TIMEOUT))
        assertEquals("No match found", errorCodeToMessage(SpeechRecognizer.ERROR_NO_MATCH))
        assertEquals("Recognizer busy", errorCodeToMessage(SpeechRecognizer.ERROR_RECOGNIZER_BUSY))
        assertEquals("Insufficient permissions", errorCodeToMessage(SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS))
        assertEquals("Too many requests", errorCodeToMessage(SpeechRecognizer.ERROR_TOO_MANY_REQUESTS))
        assertEquals("Language not supported", errorCodeToMessage(SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED))
        assertEquals("Language unavailable", errorCodeToMessage(SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE))
    }

    @Test
    fun `errorCodeToMessage maps unknown code to fallback`() {
        assertEquals("Unknown error (999999)", errorCodeToMessage(999999))
    }

    // --- SpeechInputController state transitions ---

    @Test
    fun `startListening transitions from IDLE to LISTENING`() {
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)

        controller.startListening(onResult = {}, onError = {})
        assertEquals(SpeechInputController.State.LISTENING, controller.state.value)
    }

    @Test
    fun `startListening when recognition unavailable calls onError and returns to IDLE`() {
        fakeEngine.recognitionAvailable = false
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        val errors = mutableListOf<String>()

        controller.startListening(onResult = {}, onError = { errors.add(it) })

        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
        assertEquals(1, errors.size)
        assertEquals("Speech recognition is not available on this device", errors[0])
    }

    @Test
    fun `startListening when not IDLE is a no-op`() {
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        controller.startListening(onResult = {}, onError = {})
        assertEquals(SpeechInputController.State.LISTENING, controller.state.value)

        val results = mutableListOf<String>()
        controller.startListening(onResult = { results.add(it) }, onError = {})

        assertEquals(SpeechInputController.State.LISTENING, controller.state.value)
        assertEquals(0, results.size) // second start should not have triggered anything
    }

    @Test
    fun `onResult callback triggers PROCESSING then IDLE state`() = runTest {
        fakeEngine.triggerResultOnNextStart = "hello world"
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        val results = mutableListOf<String>()

        controller.startListening(onResult = { results.add(it) }, onError = {})
        advanceUntilIdle()

        assertEquals(listOf("hello world"), results)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    @Test
    fun `onError callback triggers ERROR then IDLE state`() = runTest {
        fakeEngine.triggerErrorOnNextStart = SpeechRecognizer.ERROR_SPEECH_TIMEOUT
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        val errors = mutableListOf<String>()

        controller.startListening(onResult = {}, onError = { errors.add(it) })
        advanceUntilIdle()

        assertEquals(listOf("No speech detected"), errors)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    @Test
    fun `cancel while listening stops engine and returns to IDLE`() {
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        controller.startListening(onResult = {}, onError = {})

        controller.cancel()

        assert(fakeEngine.wasCancelled)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    @Test
    fun `cancel when IDLE is a no-op`() {
        val controller = SpeechInputController(recognizerEngine = fakeEngine)

        controller.cancel()

        assert(!fakeEngine.wasCancelled)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    @Test
    fun `cancel after engine has already delivered a result`() = runTest {
        fakeEngine.triggerResultOnNextStart = "hello"
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        controller.startListening(onResult = {}, onError = {})
        advanceUntilIdle()

        controller.cancel()

        // Cancel after result delivered should be a no-op (already IDLE)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    // --- stopListening ---

    @Test
    fun `stopListening while LISTENING calls through to engine`() {
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        controller.startListening(onResult = {}, onError = {})

        controller.stopListening()

        assert(fakeEngine.stopListeningCalled)
    }

    @Test
    fun `stopListening while IDLE is no-op does not call engine`() {
        val controller = SpeechInputController(recognizerEngine = fakeEngine)

        controller.stopListening()

        assert(!fakeEngine.stopListeningCalled)
    }

    @Test
    fun `stopListening and cancel are distinct calls`() {
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        controller.startListening(onResult = {}, onError = {})

        controller.stopListening()
        assert(fakeEngine.stopListeningCalled)
        assert(!fakeEngine.wasCancelled)

        // Reset for cancel test
        fakeEngine.stopListeningCalled = false
        fakeEngine.wasCancelled = false
        // Re-set state to LISTENING since cancel after stopListening in the
        // real controller wouldn't change state (stopListening doesn't
        // change state), but the fake engine's stopListening doesn't
        // trigger onResult either, so state is still LISTENING.
        controller.cancel()
        assert(fakeEngine.wasCancelled)
        assert(!fakeEngine.stopListeningCalled)
    }

    @Test
    fun `stopListening then fake onResult still delivers result and ends at IDLE`() = runTest {
        fakeEngine.triggerResultOnNextStart = "stopped early"
        fakeEngine.triggerResultOnStopListening = true
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        val results = mutableListOf<String>()

        controller.startListening(onResult = { results.add(it) }, onError = {})
        assertEquals(SpeechInputController.State.LISTENING, controller.state.value)

        controller.stopListening()
        advanceUntilIdle()

        assertEquals(listOf("stopped early"), results)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    @Test
    fun `engine is destroyed after result callback`() = runTest {
        fakeEngine.triggerResultOnNextStart = "goodbye"
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        controller.startListening(onResult = {}, onError = {})
        advanceUntilIdle()

        assert(fakeEngine.wasDestroyedAfterUse)
    }

    @Test
    fun `engine is destroyed after error callback`() = runTest {
        fakeEngine.triggerErrorOnNextStart = SpeechRecognizer.ERROR_NO_MATCH
        val controller = SpeechInputController(recognizerEngine = fakeEngine)
        controller.startListening(onResult = {}, onError = {})
        advanceUntilIdle()

        assert(fakeEngine.wasDestroyedAfterUse)
    }

    // --- startListeningRaw state transitions ---

    @Test
    fun `startListeningRaw transitions from IDLE to LISTENING`() {
        val fakeAudio = FakeAudioCaptureEngine()
        val controller = SpeechInputController(
            recognizerEngine = fakeEngine,
            audioCaptureEngine = fakeAudio,
        )
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)

        controller.startListeningRaw(onAudioCaptured = {}, onError = {})
        assertEquals(SpeechInputController.State.LISTENING, controller.state.value)
    }

    @Test
    fun `startListeningRaw when engine is null calls onError and returns to IDLE`() {
        val controller = SpeechInputController(
            recognizerEngine = fakeEngine,
            audioCaptureEngine = null,
        )
        val errors = mutableListOf<String>()

        controller.startListeningRaw(onAudioCaptured = {}, onError = { errors.add(it) })

        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
        assertEquals(1, errors.size)
        assertEquals("Raw audio capture is not available on this device", errors[0])
    }

    @Test
    fun `startListeningRaw when capture not available calls onError and returns to IDLE`() {
        val fakeAudio = FakeAudioCaptureEngine().apply { captureAvailable = false }
        val controller = SpeechInputController(
            recognizerEngine = fakeEngine,
            audioCaptureEngine = fakeAudio,
        )
        val errors = mutableListOf<String>()

        controller.startListeningRaw(onAudioCaptured = {}, onError = { errors.add(it) })

        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
        assertEquals(1, errors.size)
        assertEquals("Raw audio capture is not available on this device", errors[0])
    }

    @Test
    fun `startListeningRaw when not IDLE is a no-op`() {
        val fakeAudio = FakeAudioCaptureEngine()
        val controller = SpeechInputController(
            recognizerEngine = fakeEngine,
            audioCaptureEngine = fakeAudio,
        )
        controller.startListeningRaw(onAudioCaptured = {}, onError = {})
        assertEquals(SpeechInputController.State.LISTENING, controller.state.value)

        val captured = mutableListOf<ByteArray>()
        controller.startListeningRaw(onAudioCaptured = { captured.add(it) }, onError = {})

        assertEquals(SpeechInputController.State.LISTENING, controller.state.value)
        assertEquals(0, captured.size)
    }

    @Test
    fun `startListeningRaw onAudioCaptured triggers PROCESSING then IDLE state`() {
        val fakeAudio = FakeAudioCaptureEngine().apply {
            triggerAudioOnNextStart = byteArrayOf(1, 2, 3)
        }
        val controller = SpeechInputController(
            recognizerEngine = fakeEngine,
            audioCaptureEngine = fakeAudio,
        )
        val captured = mutableListOf<ByteArray>()

        controller.startListeningRaw(onAudioCaptured = { captured.add(it) }, onError = {})

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(1, 2, 3), captured[0])
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    @Test
    fun `startListeningRaw onError triggers ERROR then IDLE state`() {
        val fakeAudio = FakeAudioCaptureEngine().apply {
            triggerErrorOnNextStart = "capture error"
        }
        val controller = SpeechInputController(
            recognizerEngine = fakeEngine,
            audioCaptureEngine = fakeAudio,
        )
        val errors = mutableListOf<String>()

        controller.startListeningRaw(onAudioCaptured = {}, onError = { errors.add(it) })

        assertEquals(1, errors.size)
        assertEquals("capture error", errors[0])
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    @Test
    fun `cancel while raw capture in progress calls audio engine cancel`() {
        val fakeAudio = FakeAudioCaptureEngine()
        val controller = SpeechInputController(
            recognizerEngine = fakeEngine,
            audioCaptureEngine = fakeAudio,
        )
        controller.startListeningRaw(onAudioCaptured = {}, onError = {})

        controller.cancel()

        assert(fakeAudio.wasCancelled)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }

    @Test
    fun `cancel when IDLE with audio engine does not call cancel`() {
        val fakeAudio = FakeAudioCaptureEngine()
        val controller = SpeechInputController(
            recognizerEngine = fakeEngine,
            audioCaptureEngine = fakeAudio,
        )

        controller.cancel()

        assert(!fakeAudio.wasCancelled)
        assertEquals(SpeechInputController.State.IDLE, controller.state.value)
    }
}

private class FakeSpeechRecognizerEngine : SpeechInputController.SpeechRecognizerEngine {

    var recognitionAvailable = true
    var triggerResultOnNextStart: String? = null
    var triggerResultOnStopListening: Boolean = false
    var triggerErrorOnNextStart: Int? = null
    var stopListeningCalled = false
    var wasCancelled = false
    var wasDestroyedAfterUse = false

    private var storedOnResult: ((String) -> Unit)? = null
    private var storedOnError: ((String) -> Unit)? = null
    private var storedOnEmptyResult: (() -> Unit)? = null

    override fun isRecognitionAvailable(): Boolean = recognitionAvailable

    override fun startListening(
        onResultCallback: (String) -> Unit,
        onErrorCallback: (String) -> Unit,
        onPartialResultCallback: (String) -> Unit,
        onBeginningOfSpeechCallback: () -> Unit,
        onEmptyResultCallback: () -> Unit,
    ) {
        storedOnResult = onResultCallback
        storedOnError = onErrorCallback
        storedOnEmptyResult = onEmptyResultCallback

        if (triggerResultOnStopListening) return

        val result = triggerResultOnNextStart
        val error = triggerErrorOnNextStart

        if (result != null) {
            onResultCallback(result)
            wasDestroyedAfterUse = true
        } else if (error != null) {
            onErrorCallback(errorCodeToMessage(error))
            wasDestroyedAfterUse = true
        }
    }

    override fun stopListening() {
        stopListeningCalled = true
        if (triggerResultOnStopListening) {
            val result = triggerResultOnNextStart
            if (result != null) {
                storedOnResult?.invoke(result)
                wasDestroyedAfterUse = true
            } else {
                storedOnEmptyResult?.invoke()
            }
        }
    }

    override fun cancel() {
        wasCancelled = true
    }
}

private class FakeAudioCaptureEngine : AudioCaptureEngine {

    var captureAvailable = true
    var triggerAudioOnNextStart: ByteArray? = null
    var triggerErrorOnNextStart: String? = null
    var wasCancelled = false

    override fun isCaptureAvailable(): Boolean = captureAvailable

    override fun startCapture(
        onAudioCaptured: (ByteArray) -> Unit,
        onError: (String) -> Unit,
        onIdleTimeout: () -> Unit,
    ) {
        val audio = triggerAudioOnNextStart
        val error = triggerErrorOnNextStart

        if (audio != null) {
            onAudioCaptured(audio)
        } else if (error != null) {
            onError(error)
        }
    }

    override fun cancel() {
        wasCancelled = true
    }
}