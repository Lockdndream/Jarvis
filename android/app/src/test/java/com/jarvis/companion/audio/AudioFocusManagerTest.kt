package com.jarvis.companion.audio

import org.junit.Assert.assertEquals
import org.junit.Test

class AudioFocusManagerTest {

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
}