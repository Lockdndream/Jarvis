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
