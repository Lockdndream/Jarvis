import json
import logging

logger = logging.getLogger("jarvis")


class ConnectionManager:
    def __init__(self):
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

    async def connect(self, ws) -> int:
        await ws.accept()
        self._connections.add(ws)
        conn_id = self._next_id
        self._next_id += 1
        self._ids[ws] = conn_id
        logger.info("WebSocket client connected (%d total) conn_id=%d", len(self._connections), conn_id)
        return conn_id

    def disconnect(self, ws, code: int | None = None, reason: str | None = None):
        conn_id = self._ids.pop(ws, None)
        self._connections.discard(ws)
        self._device_status.pop(ws, None)
        logger.info(
            "WebSocket client disconnected (%d remaining) conn_id=%s code=%s reason=%s",
            len(self._connections), conn_id, code, reason,
        )

    def set_device_status(self, ws, status: dict):
        self._device_status[ws] = status
        logger.info(
            "Device status received conn_id=%s device_id=%s capabilities=%s",
            self._ids.get(ws), status.get("device_id"), status.get("capabilities"),
        )

    def get_device_status(self, ws) -> dict | None:
        return self._device_status.get(ws)

    async def broadcast(self, data: dict):
        dead = set()
        for ws in list(self._connections):
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)
