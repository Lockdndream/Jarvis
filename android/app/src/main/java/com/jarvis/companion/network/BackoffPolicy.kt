package com.jarvis.companion.network

import kotlin.random.Random

/**
 * Bounded exponential backoff with jitter, capped at [maxMs].
 *
 * Fixes a bug found in spikes/android-presence/TestConnectionClient.kt:
 * there, jitter was added *after* the min(cap, ...) clamp
 * (`backoffMs = min(BACKOFF_MAX_MS, backoffMs * 2) + Random.nextLong(0, 500)`),
 * so the delay actually used could exceed the intended cap by up to the
 * jitter amount. Here jitter is added first and the cap is applied to the
 * final value, so no returned delay ever exceeds [maxMs].
 */
class BackoffPolicy(
    private val initialMs: Long = 2_000L,
    private val maxMs: Long = 60_000L,
    private val jitterMaxMs: Long = 500L,
    private val random: Random = Random.Default,
) {
    private var nextBaseMs = initialMs

    fun reset() {
        nextBaseMs = initialMs
    }

    fun nextDelayMs(): Long {
        val jitter = if (jitterMaxMs > 0) random.nextLong(0, jitterMaxMs) else 0L
        val delay = minOf(maxMs, nextBaseMs + jitter)
        nextBaseMs = minOf(maxMs, nextBaseMs * 2)
        return delay
    }
}
