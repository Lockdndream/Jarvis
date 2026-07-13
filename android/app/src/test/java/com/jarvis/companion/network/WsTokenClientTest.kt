package com.jarvis.companion.network

import com.jarvis.companion.pairing.PairingConfig
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.IOException

class WsTokenClientTest {
    private lateinit var server: MockWebServer
    private lateinit var client: WsTokenClient

    private val testConfig = PairingConfig(
        host = "localhost",
        port = 0,
        apiToken = "test-api-token",
        pinnedFingerprint = "00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00",
        pairedAtEpochMs = 0L,
    )

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = WsTokenClient(
            pairingConfig = testConfig,
            deviceId = "test-device-uuid",
            baseUrl = server.url("/").toString().trimEnd('/'),
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `fresh fetch succeeds and returns token`() = runBlocking {
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"test.jwt.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )

        val token = client.getValidToken()

        assertEquals("test.jwt.token", token)
    }

    @Test
    fun `cached token is returned without second request`() = runBlocking {
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"test.jwt.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )

        client.getValidToken()
        val result = client.getValidToken()

        assertEquals("test.jwt.token", result)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `near-expiry token triggers fresh fetch`() = runBlocking {
        val nearExpiry = System.currentTimeMillis() / 1000 + 60
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"first.token","expires_at":$nearExpiry,"expires_in":60}""")
        )
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"fresh.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )

        client.getValidToken()
        val result = client.getValidToken()

        assertEquals("fresh.token", result)
        assertEquals(2, server.requestCount)
    }

    @Test
    fun `invalidateCache forces fresh fetch`() = runBlocking {
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"first.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"fresh.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )

        client.getValidToken()
        client.invalidateCache()
        val result = client.getValidToken()

        assertEquals("fresh.token", result)
        assertEquals(2, server.requestCount)
    }

    @Test
    fun `authorization header is sent with apiToken`() = runBlocking {
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"test.jwt.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )

        client.getValidToken()

        val recordedRequest = server.takeRequest()
        assertEquals("Bearer test-api-token", recordedRequest.getHeader("Authorization"))
    }

    @Test
    fun `http error throws IOException`() = runBlocking {
        // Real compile bug found here (Milestone 9B.2 review): JUnit's
        // assertThrows() takes a plain Java functional interface, and a
        // suspend function cannot be called from inside one even when
        // textually nested inside runBlocking — "Suspension functions can
        // be called only within coroutine body". Confirmed as a real
        // compile error (the whole test file failed to build), not a
        // style preference — plain try/catch works because it runs
        // directly in this suspend test body's coroutine context.
        server.enqueue(MockResponse().setResponseCode(401))

        try {
            client.getValidToken()
            fail("Expected IOException")
        } catch (e: IOException) {
            // expected
        }
    }

    @Test
    fun `malformed 200 response throws IOException not a crash`() = runBlocking {
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"error":"unexpected shape, no token field"}""")
        )

        try {
            client.getValidToken()
            fail("Expected IOException")
        } catch (e: IOException) {
            // expected
        }
    }

    @Test
    fun `invalidateCache is safe to call without holding any lock`() = runBlocking {
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"first.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"second.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )

        client.getValidToken()
        client.invalidateCache() // plain synchronous call, no coroutine needed
        val result = client.getValidToken()

        assertEquals("second.token", result)
    }

    @Test
    fun `concurrent calls produce only one request`() = runBlocking {
        server.enqueue(MockResponse()
            .setResponseCode(200)
            .setBody("""{"token":"test.jwt.token","expires_at":${Long.MAX_VALUE},"expires_in":3600}""")
        )

        val freshClient = WsTokenClient(
            pairingConfig = testConfig,
            deviceId = "test-device-uuid",
            baseUrl = server.url("/").toString().trimEnd('/'),
        )

        val deferred1 = async { freshClient.getValidToken() }
        val deferred2 = async { freshClient.getValidToken() }
        val deferred3 = async { freshClient.getValidToken() }

        val result1 = deferred1.await()
        val result2 = deferred2.await()
        val result3 = deferred3.await()

        assertEquals("test.jwt.token", result1)
        assertEquals("test.jwt.token", result2)
        assertEquals("test.jwt.token", result3)
        assertEquals(1, server.requestCount)
    }
}