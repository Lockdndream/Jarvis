package com.jarvis.companion.core

/** Shared across network/service/ui/diagnostics — one definition of what
 * "connected" means, rather than each layer inventing its own status enum. */
enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    RECONNECTING,
    /** A disconnect happened for a reason retrying can never fix on its
     * own (wrong/rejected token, pinned-certificate mismatch) — the client
     * has stopped retrying and is waiting for user action (re-pair).
     * See DisconnectReason.isPermanent and TD-021. */
    FAILED_PERMANENT,
}
