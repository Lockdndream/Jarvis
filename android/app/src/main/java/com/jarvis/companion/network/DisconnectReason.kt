package com.jarvis.companion.network

/**
 * Why the last connection ended, classified into a fixed, small vocabulary
 * so telemetry and diagnostics never fall back to a generic "Disconnected"
 * when a more specific cause is knowable.
 *
 * [isPermanent] reasons will never succeed by simply retrying with the
 * same pairing config — CompanionWebSocketClient stops backing off and
 * retrying for these, transitioning to ConnectionState.FAILED_PERMANENT
 * instead (see DisconnectClassifier, TD-021).
 *
 * [needsTokenRefresh] reasons ARE retryable, but not by reusing the same
 * cached token — CompanionWebSocketClient invalidates WsTokenClient's
 * cache before the next reconnect attempt (Milestone 9B.2, ADR-014).
 */
enum class DisconnectReason {
    /** No disconnect has happened yet this connection attempt. */
    NONE,
    /** ConnectivityManager reported the network itself is unreachable, or
     * the socket-level failure is consistent with no route existing
     * (UnknownHostException, general SocketException without a TLS/refused
     * signature). */
    NETWORK,
    /** Server rejected the handshake because no credential was presented
     * at all (WS close 4003, no ?token= and no legacy Authorization
     * header), or an Authorization header was presented but wrong
     * (WS close 4004). Both indicate a config/pairing problem, not
     * something a token refresh alone fixes. */
    AUTH,
    /** PinnedTrustManager rejected the server's certificate — it no longer
     * matches the fingerprint confirmed during pairing (ADR-011). */
    CERTIFICATE,
    /** TCP connection actively refused (ConnectException) while the
     * network itself is reachable — the process at host:port isn't
     * listening (server down, wrong port, firewall). */
    SERVER_DOWN,
    /** WS close code 4001 — the signed token's signature was valid but its
     * `exp` claim has passed (ADR-014). Retryable: fetch a fresh token
     * before the next attempt, no human action needed. */
    TOKEN_EXPIRED,
    /** WS close code 4002 — the signed token failed verification for any
     * reason other than expiry (bad signature, malformed, tampered).
     * Retryable the same way as TOKEN_EXPIRED: whatever caused this
     * (e.g. a corrupted cache entry), a freshly-fetched token from the
     * still-valid pairing credentials should resolve it without human
     * action. */
    TOKEN_INVALID,
    /** The client itself initiated the disconnect (stop() called, e.g.
     * "Stop companion" or unpairing) — not a failure at all. */
    USER_STOPPED,
    /** A real disconnect occurred but its cause didn't match any of the
     * above — still classified explicitly rather than silently defaulting,
     * so "UNKNOWN" is visible and searchable in telemetry/diagnostics. */
    UNKNOWN;

    /** Stop retrying entirely — needs a human to re-pair, not just a
     * fresh token or a network coming back. */
    val isPermanent: Boolean
        get() = this == AUTH || this == CERTIFICATE

    /** Keep retrying, but the cached WsTokenClient token must be
     * invalidated first — reusing it would just reproduce the same
     * rejection. */
    val needsTokenRefresh: Boolean
        get() = this == TOKEN_EXPIRED || this == TOKEN_INVALID
}
