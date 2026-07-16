package com.jarvis.companion.audio

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Android AudioManager focus-change constants reproduced as plain ints so
 * [mapFocusChange] is a pure function testable without a real AudioManager
 * or Robolectric — the production listener callback receives the same raw
 * ints from the framework.
 */
internal const val FOCUS_GAIN = 1
internal const val FOCUS_LOSS = -1
internal const val FOCUS_LOSS_TRANSIENT = -2
internal const val FOCUS_LOSS_TRANSIENT_CAN_DUCK = -3

/**
 * AudioDeviceInfo.type constants reproduced as plain ints so
 * [mapDeviceTypesToRoute] is a pure function testable without a real
 * AudioManager or Robolectric.
 */
internal const val DEVICE_TYPE_EARPIECE = 1
internal const val DEVICE_TYPE_SPEAKER = 2
internal const val DEVICE_TYPE_WIRED_HEADPHONES = 3
internal const val DEVICE_TYPE_WIRED_HEADSET = 4
internal const val DEVICE_TYPE_BLUETOOTH_SCO = 7
internal const val DEVICE_TYPE_BLUETOOTH_A2DP = 8

/**
 * Pure function mapping an AudioManager focus-change int to [FocusState].
 * Extracted from [AudioManager.OnAudioFocusChangeListener] so callers can
 * unit-test this logic without a real AudioManager — the listener callback
 * delegates here immediately.
 */
internal fun mapFocusChange(focusChange: Int): AudioFocusManager.FocusState = when (focusChange) {
    FOCUS_GAIN -> AudioFocusManager.FocusState.GAINED
    FOCUS_LOSS -> AudioFocusManager.FocusState.LOST
    FOCUS_LOSS_TRANSIENT -> AudioFocusManager.FocusState.LOST_TRANSIENT
    FOCUS_LOSS_TRANSIENT_CAN_DUCK -> AudioFocusManager.FocusState.LOST_TRANSIENT_CAN_DUCK
    else -> AudioFocusManager.FocusState.NONE
}

/**
 * Pure function mapping a collection of [AudioDeviceInfo.type] ints to
 * [AudioRoute]. Priority order: wired > bluetooth > earpiece > speaker
 * > unknown. Extracted from [refreshRoute] so callers can unit-test this
 * logic without a real AudioManager — the production code collects device
 * type ints from [AudioManager.getDevices] and delegates here.
 */
internal fun mapDeviceTypesToRoute(deviceTypes: Collection<Int>): AudioFocusManager.AudioRoute {
    if (deviceTypes.any { it == DEVICE_TYPE_WIRED_HEADSET || it == DEVICE_TYPE_WIRED_HEADPHONES }) {
        return AudioFocusManager.AudioRoute.WIRED_HEADSET
    }
    if (deviceTypes.any { it == DEVICE_TYPE_BLUETOOTH_A2DP || it == DEVICE_TYPE_BLUETOOTH_SCO }) {
        return AudioFocusManager.AudioRoute.BLUETOOTH
    }
    if (DEVICE_TYPE_EARPIECE in deviceTypes) {
        return AudioFocusManager.AudioRoute.EARPIECE
    }
    if (DEVICE_TYPE_SPEAKER in deviceTypes) {
        return AudioFocusManager.AudioRoute.SPEAKER
    }
    return AudioFocusManager.AudioRoute.UNKNOWN
}

/**
 * Owns this app's Android audio focus and output-route observation for
 * voice interaction. Plain class, not a Service — the caller (a playback
 * manager built separately, or PresenceService) drives [start]/[stop]
 * explicitly, matching the pattern used by [CompanionWebSocketClient].
 *
 * No wake word, no microphone, no SpeechRecognizer, no RECORD_AUDIO
 * permission — this is pure focus/routing observation and control, zero
 * microphone involvement, per Milestone 9B.4 scope boundary.
 */
class AudioFocusManager(private val context: Context) {

    enum class FocusState { NONE, GAINED, LOST, LOST_TRANSIENT, LOST_TRANSIENT_CAN_DUCK }

    enum class AudioRoute { SPEAKER, WIRED_HEADSET, BLUETOOTH, EARPIECE, UNKNOWN }

    private val audioManager: AudioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    private val _focusState = MutableStateFlow(FocusState.NONE)
    val focusState: StateFlow<FocusState> = _focusState.asStateFlow()

    private val _currentRoute = MutableStateFlow(AudioRoute.UNKNOWN)
    val currentRoute: StateFlow<AudioRoute> = _currentRoute.asStateFlow()

    private val focusChangeListener = AudioManager.OnAudioFocusChangeListener { change ->
        _focusState.value = mapFocusChange(change)
    }

    private var focusRequest: AudioFocusRequest? = null

    private val headsetReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            refreshRoute()
        }
    }

    private var started = false

    fun requestFocus(): Boolean {
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setOnAudioFocusChangeListener(focusChangeListener)
            .build()
        focusRequest = request
        val result = audioManager.requestAudioFocus(request)
        return result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }

    fun abandonFocus() {
        focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
        focusRequest = null
        _focusState.value = FocusState.NONE
    }

    /**
     * Queries [AudioManager.getDevices] for the current output route and
     * updates [currentRoute]. Called on [start] and whenever the wired
     * headset receiver fires.
     */
    private fun refreshRoute() {
        val devices = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        val types = devices.map { it.type }
        _currentRoute.value = mapDeviceTypesToRoute(types)
    }

    fun start() {
        if (started) return
        started = true
        refreshRoute()
        val filter = IntentFilter(AudioManager.ACTION_HEADSET_PLUG)
        context.registerReceiver(headsetReceiver, filter)
    }

    fun stop() {
        if (!started) return
        started = false
        abandonFocus()
        try {
            context.unregisterReceiver(headsetReceiver)
        } catch (_: IllegalArgumentException) {
            // Already unregistered — safe to ignore during rapid teardown,
            // matching the same pattern used by PresenceService for its
            // screen-state receiver.
        }
        _focusState.value = FocusState.NONE
        _currentRoute.value = AudioRoute.UNKNOWN
    }
}