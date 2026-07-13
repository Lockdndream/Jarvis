package com.jarvis.companion.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression test for the stale-WebSocket-callback bug found in Milestone
 * 9B.0: without generation tracking, a callback from a superseded
 * connection could be misattributed to the current one. Ported unchanged
 * from spikes/android-presence.
 */
class ConnectionGenerationTrackerTest {

    @Test
    fun firstGenerationStartsAtOneAndIsCurrent() {
        val tracker = ConnectionGenerationTracker()
        val gen = tracker.startNewGeneration()
        assertEquals(1, gen)
        assertTrue(tracker.isCurrent(gen))
    }

    @Test
    fun startingANewGenerationInvalidatesThePrevious() {
        val tracker = ConnectionGenerationTracker()
        val genN = tracker.startNewGeneration()
        val genNPlus1 = tracker.startNewGeneration()

        assertFalse(
            "generation N must not be current once N+1 has started",
            tracker.isCurrent(genN),
        )
        assertTrue(tracker.isCurrent(genNPlus1))
    }

    @Test
    fun lateCallbackFromStaleGenerationIsRejected() {
        val tracker = ConnectionGenerationTracker()
        val attempt1 = tracker.startNewGeneration()
        val attempt2 = tracker.startNewGeneration()

        assertFalse(
            "a callback from attempt 1 must be ignored once attempt 2 is current",
            tracker.isCurrent(attempt1),
        )
        assertTrue(tracker.isCurrent(attempt2))
    }

    @Test
    fun manyGenerationsOnlyTheLatestIsCurrent() {
        val tracker = ConnectionGenerationTracker()
        val generations = (1..10).map { tracker.startNewGeneration() }

        generations.dropLast(1).forEach { staleGen ->
            assertFalse("generation $staleGen must be stale", tracker.isCurrent(staleGen))
        }
        assertTrue(tracker.isCurrent(generations.last()))
        assertEquals(10, tracker.currentGeneration())
    }
}
