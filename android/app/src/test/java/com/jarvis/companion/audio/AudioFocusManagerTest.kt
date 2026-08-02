package com.jarvis.companion.audio

import android.content.Context
import android.media.AudioFocusRequest
import android.media.AudioManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito
import org.mockito.Mockito.any
import org.mockito.Mockito.never
import org.mockito.Mockito.times
import org.mockito.Mockito.verify

class AudioFocusManagerTest {

    private lateinit var context: Context
    private lateinit var audioManager: AudioManager
    private lateinit var audioFocusManager: TestableAudioFocusManager

    @Before
    fun setUp() {
        context = Mockito.mock(Context::class.java)
        audioManager = Mockito.mock(AudioManager::class.java)
        Mockito.`when`(context.getSystemService(Context.AUDIO_SERVICE)).thenReturn(audioManager)
        Mockito.`when`(audioManager.requestAudioFocus(any())).thenReturn(AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
        audioFocusManager = TestableAudioFocusManager(context)
    }

    private class TestableAudioFocusManager(context: Context) : AudioFocusManager(context) {
        var requestFocusCallCount = 0

        override fun createAudioFocusRequest(): AudioFocusRequest {
            requestFocusCallCount++
            return Mockito.mock(AudioFocusRequest::class.java)
        }
    }

    // ── mapFocusChange ────────────────────────────────────────────────

    @Test
    fun `mapFocusChange maps AUDIOFOCUS_GAIN to GAINED`() {
        assertEquals(AudioFocusManager.FocusState.GAINED, mapFocusChange(FOCUS_GAIN))
    }

    @Test
    fun `mapFocusChange maps AUDIOFOCUS_LOSS to LOST`() {
        assertEquals(AudioFocusManager.FocusState.LOST, mapFocusChange(FOCUS_LOSS))
    }

    @Test
    fun `mapFocusChange maps AUDIOFOCUS_LOSS_TRANSIENT to LOST_TRANSIENT`() {
        assertEquals(AudioFocusManager.FocusState.LOST_TRANSIENT, mapFocusChange(FOCUS_LOSS_TRANSIENT))
    }

    @Test
    fun `mapFocusChange maps AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK to LOST_TRANSIENT_CAN_DUCK`() {
        assertEquals(
            AudioFocusManager.FocusState.LOST_TRANSIENT_CAN_DUCK,
            mapFocusChange(FOCUS_LOSS_TRANSIENT_CAN_DUCK),
        )
    }

    @Test
    fun `mapFocusChange maps unknown int to NONE`() {
        assertEquals(AudioFocusManager.FocusState.NONE, mapFocusChange(999))
    }

    @Test
    fun `mapFocusChange maps zero to NONE`() {
        assertEquals(AudioFocusManager.FocusState.NONE, mapFocusChange(0))
    }

    // ── mapDeviceTypesToRoute ─────────────────────────────────────────

    @Test
    fun `mapDeviceTypesToRoute returns SPEAKER for built-in speaker only`() {
        assertEquals(AudioFocusManager.AudioRoute.SPEAKER, mapDeviceTypesToRoute(listOf(DEVICE_TYPE_SPEAKER)))
    }

    @Test
    fun `mapDeviceTypesToRoute returns EARPIECE for earpiece only`() {
        assertEquals(AudioFocusManager.AudioRoute.EARPIECE, mapDeviceTypesToRoute(listOf(DEVICE_TYPE_EARPIECE)))
    }

    @Test
    fun `mapDeviceTypesToRoute returns WIRED_HEADSET for wired headset`() {
        assertEquals(
            AudioFocusManager.AudioRoute.WIRED_HEADSET,
            mapDeviceTypesToRoute(listOf(DEVICE_TYPE_WIRED_HEADSET)),
        )
    }

    @Test
    fun `mapDeviceTypesToRoute returns WIRED_HEADSET for wired headphones`() {
        assertEquals(
            AudioFocusManager.AudioRoute.WIRED_HEADSET,
            mapDeviceTypesToRoute(listOf(DEVICE_TYPE_WIRED_HEADPHONES)),
        )
    }

    @Test
    fun `mapDeviceTypesToRoute returns BLUETOOTH for A2DP device`() {
        assertEquals(
            AudioFocusManager.AudioRoute.BLUETOOTH,
            mapDeviceTypesToRoute(listOf(DEVICE_TYPE_BLUETOOTH_A2DP)),
        )
    }

    @Test
    fun `mapDeviceTypesToRoute returns BLUETOOTH for SCO device`() {
        assertEquals(
            AudioFocusManager.AudioRoute.BLUETOOTH,
            mapDeviceTypesToRoute(listOf(DEVICE_TYPE_BLUETOOTH_SCO)),
        )
    }

    @Test
    fun `mapDeviceTypesToRoute prioritises wired over bluetooth when both present`() {
        assertEquals(
            AudioFocusManager.AudioRoute.WIRED_HEADSET,
            mapDeviceTypesToRoute(listOf(DEVICE_TYPE_BLUETOOTH_A2DP, DEVICE_TYPE_WIRED_HEADSET)),
        )
    }

    @Test
    fun `mapDeviceTypesToRoute prioritises bluetooth over speaker when both present`() {
        assertEquals(
            AudioFocusManager.AudioRoute.BLUETOOTH,
            mapDeviceTypesToRoute(listOf(DEVICE_TYPE_SPEAKER, DEVICE_TYPE_BLUETOOTH_A2DP)),
        )
    }

    @Test
    fun `mapDeviceTypesToRoute returns UNKNOWN for empty list`() {
        assertEquals(AudioFocusManager.AudioRoute.UNKNOWN, mapDeviceTypesToRoute(emptyList()))
    }

    @Test
    fun `mapDeviceTypesToRoute returns UNKNOWN for unrecognised device types`() {
        assertEquals(AudioFocusManager.AudioRoute.UNKNOWN, mapDeviceTypesToRoute(listOf(99, 100)))
    }

    @Test
    fun `mapDeviceTypesToRoute handles multiple devices with speaker fallback`() {
        assertEquals(
            AudioFocusManager.AudioRoute.SPEAKER,
            mapDeviceTypesToRoute(listOf(DEVICE_TYPE_SPEAKER, 99)),
        )
    }

    // ── requestFocus / abandonFocus instance tests ───────────────────

    @Test
    fun `requestFocus when not holding focus acquires normally`() {
        val result = audioFocusManager.requestFocus()

        assertTrue(result)
        verify(audioManager, times(1)).requestAudioFocus(any())
        assertEquals(1, audioFocusManager.requestFocusCallCount)
    }

    @Test
    fun `requestFocus when already holding focus returns true without new system call`() {
        audioFocusManager.requestFocus()
        Mockito.reset(audioManager)

        val result = audioFocusManager.requestFocus()

        assertTrue(result)
        verify(audioManager, never()).requestAudioFocus(any())
    }

    @Test
    fun `requestFocus returns false when system denies focus`() {
        Mockito.`when`(audioManager.requestAudioFocus(any())).thenReturn(AudioManager.AUDIOFOCUS_REQUEST_FAILED)
        val mgr = TestableAudioFocusManager(context)

        val result = mgr.requestFocus()

        assertFalse(result)
        verify(audioManager, times(1)).requestAudioFocus(any())
    }

    @Test
    fun `requestFocus after denial retries and can succeed`() {
        Mockito.`when`(audioManager.requestAudioFocus(any()))
            .thenReturn(AudioManager.AUDIOFOCUS_REQUEST_FAILED)
            .thenReturn(AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
        val mgr = TestableAudioFocusManager(context)

        val first = mgr.requestFocus()
        val second = mgr.requestFocus()

        assertFalse(first)
        assertTrue(second)
        verify(audioManager, times(2)).requestAudioFocus(any())
    }

    @Test
    fun `abandonFocus when holding focus releases with system`() {
        audioFocusManager.requestFocus()
        Mockito.reset(audioManager)

        audioFocusManager.abandonFocus()

        verify(audioManager, times(1)).abandonAudioFocusRequest(any())
        assertEquals(AudioFocusManager.FocusState.NONE, audioFocusManager.focusState.value)
    }

    @Test
    fun `abandonFocus when not holding focus still releases system focus if a request exists`() {
        audioFocusManager.requestFocus()
        audioFocusManager.abandonFocus()
        Mockito.reset(audioManager)

        audioFocusManager.abandonFocus()

        verify(audioManager, never()).abandonAudioFocusRequest(any())
        assertEquals(AudioFocusManager.FocusState.NONE, audioFocusManager.focusState.value)
    }

    @Test
    fun `AUDIOFOCUS_LOSS callback clears held flag and subsequent requestFocus acquires`() {
        audioFocusManager.requestFocus()
        Mockito.reset(audioManager)
        Mockito.`when`(audioManager.requestAudioFocus(any())).thenReturn(AudioManager.AUDIOFOCUS_REQUEST_GRANTED)

        audioFocusManager.focusChangeListener.onAudioFocusChange(FOCUS_LOSS)

        assertEquals(AudioFocusManager.FocusState.LOST, audioFocusManager.focusState.value)

        val result = audioFocusManager.requestFocus()

        assertTrue(result)
        verify(audioManager, times(1)).requestAudioFocus(any())
    }

    @Test
    fun `AUDIOFOCUS_LOSS_TRANSIENT callback does NOT clear held flag and subsequent requestFocus is no-op`() {
        audioFocusManager.requestFocus()
        Mockito.reset(audioManager)

        audioFocusManager.focusChangeListener.onAudioFocusChange(FOCUS_LOSS_TRANSIENT)

        assertEquals(AudioFocusManager.FocusState.LOST_TRANSIENT, audioFocusManager.focusState.value)

        val result = audioFocusManager.requestFocus()

        assertTrue(result)
        verify(audioManager, never()).requestAudioFocus(any())
    }

    @Test
    fun `AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK callback does NOT clear held flag and subsequent requestFocus is no-op`() {
        audioFocusManager.requestFocus()
        Mockito.reset(audioManager)

        audioFocusManager.focusChangeListener.onAudioFocusChange(FOCUS_LOSS_TRANSIENT_CAN_DUCK)

        assertEquals(AudioFocusManager.FocusState.LOST_TRANSIENT_CAN_DUCK, audioFocusManager.focusState.value)

        val result = audioFocusManager.requestFocus()

        assertTrue(result)
        verify(audioManager, never()).requestAudioFocus(any())
    }

    @Test
    fun `AUDIOFOCUS_GAIN callback sets held flag`() {
        audioFocusManager.focusChangeListener.onAudioFocusChange(FOCUS_GAIN)

        assertEquals(AudioFocusManager.FocusState.GAINED, audioFocusManager.focusState.value)

        Mockito.reset(audioManager)
        val result = audioFocusManager.requestFocus()

        assertTrue(result)
        verify(audioManager, never()).requestAudioFocus(any())
    }

    @Test
    fun `abandonFocus then requestFocus re-acquires normally`() {
        audioFocusManager.requestFocus()
        audioFocusManager.abandonFocus()
        Mockito.reset(audioManager)
        Mockito.`when`(audioManager.requestAudioFocus(any())).thenReturn(AudioManager.AUDIOFOCUS_REQUEST_GRANTED)

        val result = audioFocusManager.requestFocus()

        assertTrue(result)
        verify(audioManager, times(1)).requestAudioFocus(any())
    }
}