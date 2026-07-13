"""Milestone 9B.2 (ADR-012): /ws accepts a device_status message (the
Android companion's capability-advertisement/state-sync frame) without
error, storing it via ConnectionManager, and does not reply to it
(fire-and-forget, same shape as the existing heartbeat frame).
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db

_ENV_BEFORE_IMPORT = dict(os.environ)
import app.main as _main_module
for _leaked_var in set(os.environ) - set(_ENV_BEFORE_IMPORT):
    if _leaked_var.startswith("JARVIS_LLM") or _leaked_var.startswith("OPENCODE_"):
        del os.environ[_leaked_var]


@pytest.fixture
def client(monkeypatch):
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    main_module = _main_module

    async def noop_start(self):
        pass

    async def noop_stop(self):
        pass

    monkeypatch.setattr(main_module.opencode_supervisor, "start", noop_start.__get__(main_module.opencode_supervisor))
    monkeypatch.setattr(main_module.opencode_supervisor, "stop", noop_stop.__get__(main_module.opencode_supervisor))

    async def fake_get_status():
        return {"server_alive": False, "server_url": "", "running_tasks": [], "pending_questions": []}
    monkeypatch.setattr(main_module.opencode_supervisor, "get_status", fake_get_status)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        yield c

    if os.path.exists(path):
        os.unlink(path)


def _drain_initial(ws):
    seen_types = set()
    for _ in range(4):
        try:
            msg = ws.receive_json()
        except Exception:
            break
        seen_types.add(msg.get("type"))
        if "opencode_status" in seen_types:
            break
    return seen_types


def test_device_status_accepted_without_reply(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({
            "type": "device_status",
            "device_id": "test-device-1",
            "capabilities": {"voice": False, "notifications": True},
            "pairing_state": "paired",
            "battery_optimization_exempt": True,
            "notification_permission_granted": True,
            "connection_generation": 1,
        })
        # No reply is expected; a subsequent, real message must still be
        # answered normally — proves the server didn't get stuck/confused
        # processing the device_status frame.
        ws.send_json({"type": "conversation_init", "conversation_id": None})
        ready = ws.receive_json()
        assert ready["type"] == "conversation_ready"


def test_device_status_stored_in_connection_manager(client):
    import app.main as main_module

    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({
            "type": "device_status",
            "device_id": "test-device-2",
            "capabilities": {"voice": False},
        })
        ws.send_json({"type": "conversation_init", "conversation_id": None})
        ws.receive_json()

        stored = [
            status for status in main_module.conn_manager._device_status.values()
            if status.get("device_id") == "test-device-2"
        ]
        assert len(stored) == 1
        assert stored[0]["capabilities"] == {"voice": False}


def test_malformed_device_status_does_not_crash_the_connection(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({"type": "device_status"})  # missing every optional field
        ws.send_json({"type": "conversation_init", "conversation_id": None})
        ready = ws.receive_json()
        assert ready["type"] == "conversation_ready"
