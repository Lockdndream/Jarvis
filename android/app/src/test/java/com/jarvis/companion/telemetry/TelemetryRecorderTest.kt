package com.jarvis.companion.telemetry

import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito
import java.io.File

class TelemetryRecorderTest {

    private lateinit var tempDir: File
    private lateinit var recorder: TelemetryRecorder

    @Before
    fun setUp() {
        tempDir = createTempDir("telemetry_test_")
        val context = Mockito.mock(android.content.Context::class.java)
        Mockito.`when`(context.filesDir).thenReturn(tempDir)
        recorder = TelemetryRecorder(context)
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    @Test
    fun `wakeword event produces android_wakeword component`() {
        recorder.record("WAKEWORD_DETECTED", "confidence=0.91")
        val line = recorder.readAll().trim()
        val json = JSONObject(line)
        assertEquals("android.wakeword", json.getString("component"))
    }

    @Test
    fun `unrecognized event produces android_other component`() {
        recorder.record("UNKNOWN_EVENT", "something")
        val line = recorder.readAll().trim()
        val json = JSONObject(line)
        assertEquals("android.other", json.getString("component"))
    }

    @Test
    fun `legacy two_argument call produces valid json with defaults`() {
        recorder.record("APP_CREATED")
        val line = recorder.readAll().trim()
        assertTrue("line should not be empty", line.isNotEmpty())
        val json = JSONObject(line)
        assertEquals("INFO", json.getString("severity"))
        assertTrue(json.isNull("trace_id"))
        assertTrue(json.isNull("conversation_id"))
        assertTrue(json.isNull("task_id"))
        assertNotNull(json.getJSONObject("fields"))
        assertEquals(0, json.getJSONObject("fields").length())
        assertEquals("APP_CREATED", json.getString("message"))
    }

    @Test
    fun `explicit traceId wins over provider`() {
        val context = Mockito.mock(android.content.Context::class.java)
        Mockito.`when`(context.filesDir).thenReturn(createTempDir("telemetry_test_provider_"))
        val recorderWithProvider = TelemetryRecorder(
            context = context,
            traceIdProvider = { "trace_from_provider" },
        )
        recorderWithProvider.record("WS_CONNECTED", traceId = "trace_explicit")
        val line = recorderWithProvider.readAll().trim()
        val json = JSONObject(line)
        assertEquals("trace_explicit", json.getString("trace_id"))
    }

    @Test
    fun `traceIdProvider supplies default trace_id`() {
        val context = Mockito.mock(android.content.Context::class.java)
        Mockito.`when`(context.filesDir).thenReturn(createTempDir("telemetry_test_provider2_"))
        val recorderWithProvider = TelemetryRecorder(
            context = context,
            traceIdProvider = { "trace_from_provider" },
        )
        recorderWithProvider.record("WS_CONNECTED")
        val line = recorderWithProvider.readAll().trim()
        val json = JSONObject(line)
        assertEquals("trace_from_provider", json.getString("trace_id"))
    }

    @Test
    fun `rotation creates numbered backup file`() {
        // Write until rotation demonstrably occurs (telemetry.log.1
        // appears), rather than trying to predict exactly how many writes
        // that takes -- rotateIfOversized() fires as soon as the active
        // file exceeds MAX_LOG_BYTES, on the *next* record() call, so the
        // most robust check is simply "keep writing until it happened."
        val payload = "x".repeat(500)
        val rotatedLog1 = tempDir.resolve("telemetry.log.1")
        var iterations = 0
        while (!rotatedLog1.exists() && iterations < 5000) {
            recorder.record("APP_CREATED", payload)
            iterations++
        }
        assertTrue(
            "telemetry.log.1 should exist after rotation (took $iterations writes)",
            rotatedLog1.exists(),
        )
        val activeLog = tempDir.resolve("telemetry.log")
        assertTrue(
            "active telemetry.log should be small again right after rotation",
            activeLog.length() < 1_000_000L,
        )
    }

    private fun createTempDir(prefix: String): File {
        val dir = File(System.getProperty("java.io.tmpdir"), prefix + System.nanoTime())
        dir.mkdirs()
        return dir
    }
}