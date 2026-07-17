package com.jarvis.companion.wakeword

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager

/**
 * WakeWordManager's own lightweight audio-focus request (ADR-017 Section
 * D — deliberately independent of com.jarvis.companion.audio.AudioFocusManager,
 * whose own doc comment scopes it away from microphone/wake-word
 * involvement; reusing it would silently violate a boundary Milestone
 * 9B.4 deliberately drew). AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK, not the
 * exclusive GAIN VoiceActivity's conversation flow uses — a real incoming
 * phone call correctly preempts wake-word listening without either
 * component needing to know about the other.
 */
interface WakeWordAudioFocus {
    fun request(onFocusChange: (gained: Boolean) -> Unit): Boolean
    fun abandon()
}

object NoOpWakeWordAudioFocus : WakeWordAudioFocus {
    override fun request(onFocusChange: (Boolean) -> Unit): Boolean = true
    override fun abandon() {}
}

class AndroidWakeWordAudioFocus(context: Context) : WakeWordAudioFocus {
    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var focusRequest: AudioFocusRequest? = null

    override fun request(onFocusChange: (Boolean) -> Unit): Boolean {
        val listener = AudioManager.OnAudioFocusChangeListener { change ->
            val gained = change == AudioManager.AUDIOFOCUS_GAIN
            onFocusChange(gained)
        }
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setOnAudioFocusChangeListener(listener)
            .build()
        focusRequest = request
        return audioManager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }

    override fun abandon() {
        focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
        focusRequest = null
    }
}
