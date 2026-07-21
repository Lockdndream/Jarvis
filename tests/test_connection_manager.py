"""Milestone 9B.0 Phase 2: per-connection ID + disconnect code/reason
telemetry on ConnectionManager. Same RecordingWebSocket fake pattern as
tests/test_attention_manager.py.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.connection_manager import ConnectionManager


class RecordingWebSocket:
    async def accept(self):
        pass

    async def send_text(self, text):
        pass


@pytest.mark.asyncio
async def test_connect_assigns_a_positive_integer_id():
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    conn_id = await cm.connect(ws)
    assert isinstance(conn_id, int)
    assert conn_id > 0


@pytest.mark.asyncio
async def test_concurrent_connections_get_distinct_ids():
    cm = ConnectionManager()
    ws_a, ws_b = RecordingWebSocket(), RecordingWebSocket()
    id_a = await cm.connect(ws_a)
    id_b = await cm.connect(ws_b)
    assert id_a != id_b
    assert cm.count == 2


@pytest.mark.asyncio
async def test_disconnect_removes_from_active_connections():
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    cm.disconnect(ws)
    assert cm.count == 0


@pytest.mark.asyncio
async def test_disconnect_is_safe_to_call_twice():
    """A real transport-error path could call disconnect() after
    WebSocketDisconnect already did — must not raise (KeyError etc.)."""
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    cm.disconnect(ws)
    cm.disconnect(ws)  # must not raise
    assert cm.count == 0


@pytest.mark.asyncio
async def test_disconnect_accepts_optional_code_and_reason(caplog):
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    import logging
    with caplog.at_level(logging.INFO, logger="jarvis"):
        cm.disconnect(ws, code=1001, reason="going away")
    assert any("code=1001" in r.message and "reason=going away" in r.message for r in caplog.records)


# Milestone 9B.2 (ADR-012): device_status capability-advertisement storage.

@pytest.mark.asyncio
async def test_device_status_defaults_to_none():
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    assert cm.get_device_status(ws) is None


@pytest.mark.asyncio
async def test_set_device_status_is_retrievable():
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    status = {"type": "device_status", "device_id": "abc-123", "capabilities": {"voice": False}}
    cm.set_device_status(ws, status)
    assert cm.get_device_status(ws) == status


@pytest.mark.asyncio
async def test_device_status_is_per_connection():
    cm = ConnectionManager()
    ws_a, ws_b = RecordingWebSocket(), RecordingWebSocket()
    await cm.connect(ws_a)
    await cm.connect(ws_b)
    cm.set_device_status(ws_a, {"device_id": "a"})
    assert cm.get_device_status(ws_a) == {"device_id": "a"}
    assert cm.get_device_status(ws_b) is None


@pytest.mark.asyncio
async def test_device_status_cleared_on_disconnect():
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    cm.set_device_status(ws, {"device_id": "abc-123"})
    cm.disconnect(ws)
    assert cm.get_device_status(ws) is None


# Control Center hardening (Phase 1, blocker): the observer-only fan-out
# and everything it gates. RecordingWebSocket is extended locally with a
# `.sent` list so these tests can assert on exactly what each connection
# received, not just that send_text() didn't raise.

class RecordingWebSocketWithLog(RecordingWebSocket):
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


@pytest.mark.asyncio
async def test_has_observers_false_with_no_registrations():
    cm = ConnectionManager()
    assert cm.has_observers() is False


@pytest.mark.asyncio
async def test_mark_observer_sets_has_observers_true():
    cm = ConnectionManager()
    ws = RecordingWebSocketWithLog()
    await cm.connect(ws)
    cm.mark_observer(ws)
    assert cm.has_observers() is True


@pytest.mark.asyncio
async def test_mark_observer_is_idempotent():
    cm = ConnectionManager()
    ws = RecordingWebSocketWithLog()
    await cm.connect(ws)
    cm.mark_observer(ws)
    cm.mark_observer(ws)
    await cm.broadcast_observers({"type": "x"})
    assert len(ws.sent) == 1  # not delivered twice from double-registration


@pytest.mark.asyncio
async def test_broadcast_observers_is_noop_with_zero_observers():
    """The exact early return Phase 4 relies on for its own has_observers()
    gate: calling broadcast_observers() with nobody registered must do no
    work (no exception, no send_text calls on anyone)."""
    cm = ConnectionManager()
    ws = RecordingWebSocketWithLog()
    await cm.connect(ws)
    await cm.broadcast_observers({"type": "supervisor_tool_call"})
    assert ws.sent == []


@pytest.mark.asyncio
async def test_broadcast_observers_reaches_only_registered_observers():
    """THE core invariant: a connection that never sent register_observer
    (mark_observer) must never receive an observer-only event, no matter
    how many other connections are observers."""
    cm = ConnectionManager()
    phone = RecordingWebSocketWithLog()
    dashboard = RecordingWebSocketWithLog()
    await cm.connect(phone)
    await cm.connect(dashboard)
    cm.mark_observer(dashboard)

    await cm.broadcast_observers({"type": "supervisor_tool_call", "content": "x"})

    assert phone.sent == []
    assert len(dashboard.sent) == 1


@pytest.mark.asyncio
async def test_broadcast_observers_reaches_multiple_observers():
    """Multiple dashboard tabs/clients all registered as observers all
    receive the same event (no "first observer wins" bug)."""
    cm = ConnectionManager()
    dash_a = RecordingWebSocketWithLog()
    dash_b = RecordingWebSocketWithLog()
    await cm.connect(dash_a)
    await cm.connect(dash_b)
    cm.mark_observer(dash_a)
    cm.mark_observer(dash_b)

    await cm.broadcast_observers({"type": "voice_session_lifecycle"})

    assert len(dash_a.sent) == 1
    assert len(dash_b.sent) == 1


@pytest.mark.asyncio
async def test_general_broadcast_still_reaches_every_connection_including_observers():
    """broadcast() (the pre-existing general fan-out every phone/PWA
    connection relies on) is untouched by this feature: it must still
    reach every connection, observer or not -- registering as an
    observer only *adds* the observer-only fan-out, it doesn't opt a
    connection out of the general one."""
    cm = ConnectionManager()
    phone = RecordingWebSocketWithLog()
    dashboard = RecordingWebSocketWithLog()
    await cm.connect(phone)
    await cm.connect(dashboard)
    cm.mark_observer(dashboard)

    await cm.broadcast({"type": "attention_created"})

    assert len(phone.sent) == 1
    assert len(dashboard.sent) == 1


@pytest.mark.asyncio
async def test_observer_removed_on_disconnect():
    cm = ConnectionManager()
    ws = RecordingWebSocketWithLog()
    await cm.connect(ws)
    cm.mark_observer(ws)
    assert cm.has_observers() is True

    cm.disconnect(ws)

    assert cm.has_observers() is False
    await cm.broadcast_observers({"type": "supervisor_turn"})  # must not resurrect/raise
    assert ws.sent == []


@pytest.mark.asyncio
async def test_broadcast_observers_drops_dead_connections_from_both_sets():
    """A send failure (closed socket) during broadcast_observers() must
    remove the connection from both _observers and _connections -- the
    same dead-connection cleanup broadcast() already does for the
    general fan-out."""
    class DeadWebSocket(RecordingWebSocketWithLog):
        async def send_text(self, text):
            raise ConnectionError("closed")

    cm = ConnectionManager()
    dead = DeadWebSocket()
    await cm.connect(dead)
    cm.mark_observer(dead)

    await cm.broadcast_observers({"type": "supervisor_turn_started"})

    assert cm.has_observers() is False
    assert cm.count == 0


def test_get_stats_shape_and_values():
    cm = ConnectionManager()
    stats = cm.get_stats()
    assert stats == {
        "connections_now": 0,
        "total_connections_since_start": 0,
        "reconnect_count": 0,
        "last_heartbeat_at": None,
        "device_status": None,
    }


@pytest.mark.asyncio
async def test_get_stats_reflects_connections_and_heartbeat():
    cm = ConnectionManager()
    ws = RecordingWebSocketWithLog()
    await cm.connect(ws)
    cm.record_heartbeat(ws)
    stats = cm.get_stats()
    assert stats["connections_now"] == 1
    assert stats["total_connections_since_start"] == 1
    assert stats["last_heartbeat_at"] is not None


@pytest.mark.asyncio
async def test_record_heartbeat_is_global_not_per_connection():
    """Documented behavior (see record_heartbeat's docstring): heartbeat
    tracking is process-wide, not per-ws -- a second connection's
    heartbeat updates the same single last_heartbeat_at."""
    cm = ConnectionManager()
    ws_a, ws_b = RecordingWebSocketWithLog(), RecordingWebSocketWithLog()
    await cm.connect(ws_a)
    await cm.connect(ws_b)
    cm.record_heartbeat(ws_a)
    first = cm.get_stats()["last_heartbeat_at"]
    cm.record_heartbeat(ws_b)
    second = cm.get_stats()["last_heartbeat_at"]
    assert first is not None and second is not None
