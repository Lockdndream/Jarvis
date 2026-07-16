package com.jarvis.companion.voice

/**
 * Minimal interface for audio focus ownership, depended on by
 * [PlaybackManager] rather than the concrete AudioFocusManager class in
 * com.jarvis.companion.audio — avoids a hard file-level dependency across
 * two engineers' parallel work. A later integration step will make the
 * real AudioFocusManager conform to this interface or adapt between them.
 */
interface AudioFocusOwner {
    fun requestFocus(): Boolean
    fun abandonFocus(): Boolean
}