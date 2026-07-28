package com.jarvis.companion.opencode

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class OpenCodeTaskRepositoryTest {

    private lateinit var repository: OpenCodeTaskRepository

    @Before
    fun setUp() {
        repository = OpenCodeTaskRepository()
    }

    @Test
    fun `starts with no current task`() {
        assertNull(repository.current.value)
    }

    @Test
    fun `applyCreated sets current task`() {
        val task = OpenCodeTask("oc_1", OpenCodeTaskStatus.RUNNING, "Create a file")
        repository.applyCreated(task)
        assertEquals(task, repository.current.value)
    }

    @Test
    fun `applyCompleted for the same taskId preserves the instruction from created`() {
        repository.applyCreated(OpenCodeTask("oc_1", OpenCodeTaskStatus.RUNNING, "Create a file"))
        repository.applyCompleted(OpenCodeTask("oc_1", OpenCodeTaskStatus.COMPLETED, instruction = null))

        val current = repository.current.value
        assertEquals("oc_1", current?.taskId)
        assertEquals(OpenCodeTaskStatus.COMPLETED, current?.status)
        assertEquals("Create a file", current?.instruction)
    }

    @Test
    fun `applyCompleted for a different taskId than the tracked one surfaces it anyway, without an instruction`() {
        repository.applyCreated(OpenCodeTask("oc_1", OpenCodeTaskStatus.RUNNING, "Create a file"))
        // e.g. the client (re)connected mid-task and never saw this one's
        // created event -- still worth surfacing its completion.
        repository.applyCompleted(OpenCodeTask("oc_2", OpenCodeTaskStatus.FAILED, instruction = null))

        val current = repository.current.value
        assertEquals("oc_2", current?.taskId)
        assertEquals(OpenCodeTaskStatus.FAILED, current?.status)
        assertNull(current?.instruction)
    }

    @Test
    fun `applyCompleted with no prior created event still surfaces the task`() {
        repository.applyCompleted(OpenCodeTask("oc_1", OpenCodeTaskStatus.COMPLETED, instruction = null))
        assertEquals("oc_1", repository.current.value?.taskId)
    }

    @Test
    fun `clear resets to no current task`() {
        repository.applyCreated(OpenCodeTask("oc_1", OpenCodeTaskStatus.RUNNING, "Create a file"))
        repository.clear()
        assertNull(repository.current.value)
    }
}
