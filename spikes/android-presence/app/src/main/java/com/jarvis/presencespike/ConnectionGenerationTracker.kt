package com.jarvis.presencespike

/**
 * Tracks which WebSocket connection attempt is the current one, so a
 * callback arriving from a superseded (stale) connection can be
 * recognized and ignored instead of corrupting the state of a newer
 * connection.
 *
 * Pure, no Android/OkHttp dependency — this is the actual correctness
 * property under test: once generation N+1 has started, generation N's
 * callbacks must never again be treated as current, no matter how late
 * they arrive.
 */
class ConnectionGenerationTracker {
    @Volatile
    private var current: Int = 0

    /** Call when starting a new connection attempt. Returns its generation number. */
    fun startNewGeneration(): Int {
        current += 1
        return current
    }

    /** True only if [generation] is still the tracker's current (latest-started) generation. */
    fun isCurrent(generation: Int): Boolean = generation == current

    fun currentGeneration(): Int = current
}
