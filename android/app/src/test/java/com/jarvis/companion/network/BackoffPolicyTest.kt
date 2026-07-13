package com.jarvis.companion.network

import kotlin.random.Random
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BackoffPolicyTest {

    @Test
    fun firstDelayIsAtLeastInitialAndWithinJitterBudget() {
        val policy = BackoffPolicy(initialMs = 2_000L, maxMs = 60_000L, jitterMaxMs = 500L, random = Random(0))
        val delay = policy.nextDelayMs()
        assertTrue("delay=$delay should be >= initial 2000", delay >= 2_000L)
        assertTrue("delay=$delay should be < initial+jitter 2500", delay < 2_500L)
    }

    @Test
    fun delayDoublesEachCall() {
        val policy = BackoffPolicy(initialMs = 1_000L, maxMs = 60_000L, jitterMaxMs = 0L, random = Random(0))
        assertEquals(1_000L, policy.nextDelayMs())
        assertEquals(2_000L, policy.nextDelayMs())
        assertEquals(4_000L, policy.nextDelayMs())
        assertEquals(8_000L, policy.nextDelayMs())
    }

    @Test
    fun neverExceedsMaxEvenWithJitter() {
        // Regression test for the spikes/android-presence bug: jitter was
        // added *after* the cap was applied, so the effective delay could
        // exceed maxMs by up to jitterMaxMs. Here it must not.
        val policy = BackoffPolicy(initialMs = 50_000L, maxMs = 60_000L, jitterMaxMs = 500L, random = Random(0))
        repeat(20) {
            val delay = policy.nextDelayMs()
            assertTrue("delay=$delay must never exceed maxMs=60000", delay <= 60_000L)
        }
    }

    @Test
    fun resetReturnsToInitialDelay() {
        val policy = BackoffPolicy(initialMs = 1_000L, maxMs = 60_000L, jitterMaxMs = 0L, random = Random(0))
        policy.nextDelayMs()
        policy.nextDelayMs()
        policy.reset()
        assertEquals(1_000L, policy.nextDelayMs())
    }
}
