package com.jarvis.presencespike

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Phase 1 (Milestone 9B.0): regression test for the stale-WebSocket-
 * callback bug. Without generation tracking, a callback from a
 * superseded connection could be misattributed to the current one —
 * this is exactly the ~41s "phantom disconnect" observed on the real
 * S20 FE during Procedure A (a reconnect's own listener callback firing
 * late, misread as the new connection failing).
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
        // Simulates the real bug: connection attempt 1 starts, then a
        // reconnect (attempt 2) starts before attempt 1's delayed
        // onFailure/onClosed callback finally arrives.
        val tracker = ConnectionGenerationTracker()
        val attempt1 = tracker.startNewGeneration()
        val attempt2 = tracker.startNewGeneration()

        // attempt 1's late callback arrives after attempt 2 already exists.
        assertFalse(
            "a callback from attempt 1 must be ignored once attempt 2 is current",
            tracker.isCurrent(attempt1),
        )
        // attempt 2's own callback must still be honored.
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
