package com.jarvis.companion.attention

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class AttentionRepositoryTest {

    private lateinit var repository: AttentionRepository

    @Before
    fun setUp() {
        repository = AttentionRepository()
    }

    @Test
    fun `applyPendingAttention with mix keeps only non-terminal`() {
        val requests = listOf(
            AttentionRequest("ar_1", "QUESTION", "pending", "q1", "t1", "c1", "normal", null),
            AttentionRequest("ar_2", "PERMISSION", "resolved", null, null, null, null, null),
            AttentionRequest("ar_3", "TASK_FAILURE", "deferred", "q3", "t3", "c3", "high", null),
            AttentionRequest("ar_4", "QUESTION", "cancelled", null, null, null, null, null),
        )
        repository.applyPendingAttention(requests)
        val ids = repository.outstanding.value.map { it.attentionRequestId }
        assertEquals(setOf("ar_1", "ar_3"), ids.toSet())
    }

    @Test
    fun `applyAttentionEvent with non-terminal adds new item`() {
        repository.applyAttentionEvent(
            AttentionRequest("ar_new", "QUESTION", "pending", "new", "t1", null, null, null)
        )
        assertEquals(1, repository.outstanding.value.size)
        assertEquals("ar_new", repository.outstanding.value[0].attentionRequestId)
    }

    @Test
    fun `applyAttentionEvent with terminal removes outstanding item`() {
        repository.applyAttentionEvent(
            AttentionRequest("ar_1", "QUESTION", "pending", "q", "t", null, null, null)
        )
        assertEquals(1, repository.outstanding.value.size)

        repository.applyAttentionEvent(
            AttentionRequest("ar_1", "QUESTION", "resolved", "q", "t", null, null, null)
        )
        assertTrue(repository.outstanding.value.isEmpty())
    }

    @Test
    fun `applyAttentionEvent updating existing non-terminal replaces and moves to front`() {
        repository.applyAttentionEvent(
            AttentionRequest("ar_1", "QUESTION", "pending", "first", "t1", null, null, null)
        )
        repository.applyAttentionEvent(
            AttentionRequest("ar_2", "PERMISSION", "deferred", "second", "t2", null, null, null)
        )
        assertEquals(listOf("ar_2", "ar_1"), repository.outstanding.value.map { it.attentionRequestId })

        repository.applyAttentionEvent(
            AttentionRequest("ar_1", "QUESTION", "contacting", "first", "t1", null, null, null)
        )
        assertEquals(2, repository.outstanding.value.size)
        assertEquals(listOf("ar_1", "ar_2"), repository.outstanding.value.map { it.attentionRequestId })
        assertEquals("contacting", repository.outstanding.value[0].status)
    }

    @Test
    fun `lastContactAtMs updates on applyPendingAttention`() {
        assertNull(repository.lastContactAtMs.value)
        repository.applyPendingAttention(emptyList())
        assertNotNull(repository.lastContactAtMs.value)
    }

    @Test
    fun `lastContactAtMs updates on applyAttentionEvent including terminal`() {
        repository.applyAttentionEvent(
            AttentionRequest("ar_1", "QUESTION", "pending", "q", "t", null, null, null)
        )
        val afterNonTerminal = repository.lastContactAtMs.value
        assertNotNull(afterNonTerminal)

        Thread.sleep(1)

        repository.applyAttentionEvent(
            AttentionRequest("ar_1", "QUESTION", "resolved", "q", "t", null, null, null)
        )
        val afterTerminal = repository.lastContactAtMs.value
        assertNotNull(afterTerminal)
        assertTrue("terminal event should update lastContactAtMs", afterTerminal!! > afterNonTerminal!!)
    }

    @Test
    fun `applyAttentionEvent survives concurrent calls without losing an update`() {
        val threadCount = 20
        val latch = java.util.concurrent.CountDownLatch(threadCount)
        val startBarrier = java.util.concurrent.CyclicBarrier(threadCount)
        val threads = (0 until threadCount).map { i ->
            Thread {
                startBarrier.await()
                repository.applyAttentionEvent(
                    AttentionRequest("ar_$i", "QUESTION", "pending", "q$i", "t$i", null, null, null)
                )
                latch.countDown()
            }
        }
        threads.forEach { it.start() }
        latch.await()

        assertEquals(threadCount, repository.outstanding.value.size)
        assertEquals((0 until threadCount).map { "ar_$it" }.toSet(), repository.outstanding.value.map { it.attentionRequestId }.toSet())
    }

    @Test
    fun `clear resets both outstanding and lastContactAtMs`() {
        repository.applyPendingAttention(
            listOf(
                AttentionRequest("ar_1", "QUESTION", "pending", "q", "t", "c", "normal", null)
            )
        )
        assertTrue(repository.outstanding.value.isNotEmpty())
        assertNotNull(repository.lastContactAtMs.value)

        repository.clear()
        assertTrue(repository.outstanding.value.isEmpty())
        assertNull(repository.lastContactAtMs.value)
    }
}
