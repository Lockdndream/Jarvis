import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket

logger = logging.getLogger("jarvis")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set = set()
        # Milestone 9B.0 Phase 2: a small per-connection ID makes it
        # possible to tell concurrent connections apart in the log (a real
        # need — the S20 FE spike briefly held two overlapping connections
        # during a reconnect cycle, and the plain "N total"/"N remaining"
        # counts alone couldn't disambiguate them after the fact).
        self._ids: dict = {}
        self._next_id = 1
        # Milestone 9B.2 (ADR-012): the Android companion's capability-
        # advertisement/state-sync message, keyed the same way as _ids.
        # In-memory only, never persisted — this is live connection
        # metadata, not durable state; a reconnect simply re-sends it. Not
        # yet read by anything (no multi-device routing exists), but
        # visible for diagnostics via ConnectionManager.get_device_status().
        self._device_status: dict = {}
        # Control Center dashboard support (additive, in-memory only —
        # never persisted, mirrors the device_status pattern above):
        # a device_id seen more than once is a reconnect, not a first
        # pairing. Kept process-wide (not per-ws) because the whole point
        # is counting occurrences *across* distinct connections.
        self._seen_device_ids: set = set()
        self._reconnect_count = 0
        self._last_heartbeat_at: str | None = None
        self._last_heartbeat_conn_id: int | None = None
        # Control Center dashboard support. A real-phone/multi-connection
        # finding while building the dashboard (verified against this
        # repo's own test suite, not theoretical): ConnectionManager.
        # broadcast() fans out to *every* connection, and existing tests
        # (and, by the same logic, the real Android/PWA clients) read
        # frames off their own connection in the order the server sends
        # them. Fanning new dashboard-only observability events (a
        # Supervisor tool call, a voice-session lifecycle phase, a
        # device_status echo) into that same general broadcast interleaves
        # them with the specific unicast reply a connection is mid-flight
        # waiting for on totally unrelated traffic (e.g. one phone's
        # voice_session_open success showing up as an unexpected frame on
        # a second, unrelated connection's read). Observers (dashboard
        # connections, opted in via a "register_observer" frame — never
        # sent by Android/PWA) get a *separate* fan-out list so ordinary
        # protocol connections never see these frames at all, unchanged
        # from before this feature existed.
        self._observers: set = set()

    async def connect(self, ws: WebSocket) -> int:
        await ws.accept()
        self._connections.add(ws)
        conn_id = self._next_id
        self._next_id += 1
        self._ids[ws] = conn_id
        logger.info("WebSocket client connected (%d total) conn_id=%d", len(self._connections), conn_id)
        return conn_id

    def disconnect(self, ws: WebSocket, code: int | None = None, reason: str | None = None) -> None:
        conn_id = self._ids.pop(ws, None)
        self._connections.discard(ws)
        self._device_status.pop(ws, None)
        self._observers.discard(ws)
        logger.info(
            "WebSocket client disconnected (%d remaining) conn_id=%s code=%s reason=%s",
            len(self._connections), conn_id, code, reason,
        )

    def mark_observer(self, ws: WebSocket) -> None:
        """Opts this connection into the dashboard-only event fan-out (see
        broadcast_observers). Idempotent; safe to call more than once."""
        self._observers.add(ws)
        logger.info("Connection conn_id=%s registered as a dashboard observer", self._ids.get(ws))

    def has_observers(self) -> bool:
        """True if at least one connection has opted into the dashboard-
        only fan-out. Callers on the phone-facing hot path (Supervisor's
        tool-call loop, VoiceSessionManager's lifecycle events) use this
        to skip their own persistence/broadcast work entirely when no
        dashboard is open — those calls exist only to feed the Control
        Center; there is nothing for them to do with zero observers."""
        return bool(self._observers)

    def has_user_surfaces(self) -> bool:
        """True when at least one NON-observer connection is attached.

        ADR-018/019: the Control Center is a read-only observer. Its
        connections must never make Jarvis believe a user-facing surface
        (phone / PWA) is present, because InterruptionPolicy uses that to
        decide whether an URGENT item can be delivered now. TD-029.
        """
        return bool(self._connections - self._observers)

    def set_device_status(self, ws: WebSocket, status: dict) -> None:
        self._device_status[ws] = status
        device_id = status.get("device_id")
        if device_id:
            if device_id in self._seen_device_ids:
                self._reconnect_count += 1
            else:
                self._seen_device_ids.add(device_id)
        logger.info(
            "Device status received conn_id=%s device_id=%s capabilities=%s",
            self._ids.get(ws), status.get("device_id"), status.get("capabilities"),
        )

    def get_device_status(self, ws: WebSocket) -> dict | None:
        return self._device_status.get(ws)

    def get_latest_device_status(self) -> dict | None:
        """Most recently received device_status across all live connections
        (Control Center's Connectivity Monitor: "what is the phone doing").
        There is at most one real Android companion connected in today's
        deployment, so "most recent" and "the phone's" coincide; this
        never assumes single-device in a way that would misbehave with
        more than one (it simply reports the latest write)."""
        if not self._device_status:
            return None
        return next(iter(self._device_status.values()))

    def record_heartbeat(self, ws: WebSocket) -> None:
        """A companion heartbeat frame ({"type":"heartbeat"}, sent every 30s
        by CompanionWebSocketClient) arrived on this connection. Recorded
        globally (not per-ws) — today's deployment has one phone, and the
        Connectivity Monitor only needs "how long since we last heard from
        it", not per-connection bookkeeping."""
        self._last_heartbeat_at = _utcnow()
        self._last_heartbeat_conn_id = self._ids.get(ws)

    def get_stats(self) -> dict:
        """Snapshot for the Control Center dashboard's Connectivity
        Monitor / System Health panels. Every field here is derived from
        state this class already tracks for other reasons — nothing new
        is invented, just exposed."""
        return {
            "connections_now": len(self._connections),
            "observers_now": len(self._observers),
            "total_connections_since_start": self._next_id - 1,
            "reconnect_count": self._reconnect_count,
            "last_heartbeat_at": self._last_heartbeat_at,
            "device_status": self.get_latest_device_status(),
        }

    async def broadcast(self, data: dict) -> None:
        dead = set()
        for ws in list(self._connections):
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections.discard(ws)

    async def broadcast_observers(self, data: dict) -> None:
        """Fan-out for Control Center-only events (see the comment on
        self._observers in __init__). Never reaches a connection that
        hasn't explicitly opted in, so it can never interleave with an
        ordinary protocol connection's own request/response frames."""
        if not self._observers:
            return
        dead = set()
        encoded = json.dumps(data)
        for ws in list(self._observers):
            try:
                await ws.send_text(encoded)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._observers.discard(ws)
            self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)
