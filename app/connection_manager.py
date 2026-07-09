import json
import logging

logger = logging.getLogger("jarvis")


class ConnectionManager:
    def __init__(self):
        self._connections: set = set()

    async def connect(self, ws):
        await ws.accept()
        self._connections.add(ws)
        logger.info("WebSocket client connected (%d total)", len(self._connections))

    def disconnect(self, ws):
        self._connections.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(self._connections))

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
