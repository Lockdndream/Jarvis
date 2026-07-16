"""Milestone 9B.6 (ADR-017): voice_session_open/opened/error carry an
optional, generic client_request_id correlation token, echoed back
unchanged. Existing callers that never send it (the PWA) are unaffected.
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


def test_voice_session_opened_echoes_client_request_id(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({
            "type": "voice_session_open",
            "conversation_id": None,
            "attention_request_id": None,
            "client_request_id": "req-abc-123",
        })
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        assert opened["client_request_id"] == "req-abc-123"


def test_voice_session_opened_omits_client_request_id_when_absent(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({
            "type": "voice_session_open",
            "conversation_id": None,
            "attention_request_id": None,
        })
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        assert opened.get("client_request_id") is None


def test_voice_session_error_echoes_client_request_id(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        _drain_initial(ws1)
        _drain_initial(ws2)
        # Bind the first session to a real attention_request_id so the
        # second open against the same id hits VoiceSessionError (TD-002
        # lease contention) — the actual, real rejection path this
        # correlation mechanism exists to make identifiable.
        db.create_attention_request(
            attention_request_id="ar_test1",
            conversation_id=None,
            task_id=None,
            source_type="test",
            source_id="s1",
            attention_type="question",
            urgency="normal",
            summary="test",
            context_json=None,
            contact_policy=None,
            dedup_key="dedup_test1",
        )
        row_id = "ar_test1"
        ws1.send_json({
            "type": "voice_session_open",
            "conversation_id": None,
            "attention_request_id": row_id,
            "client_request_id": "req-first",
        })
        first_opened = ws1.receive_json()
        assert first_opened["type"] == "voice_session_opened"

        ws2.send_json({
            "type": "voice_session_open",
            "conversation_id": None,
            "attention_request_id": row_id,
            "client_request_id": "req-second",
        })
        error = ws2.receive_json()
        assert error["type"] == "voice_session_error"
        assert error["client_request_id"] == "req-second"


def test_two_concurrent_opens_are_independently_correlated(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        _drain_initial(ws1)
        _drain_initial(ws2)
        ws1.send_json({
            "type": "voice_session_open", "conversation_id": None,
            "attention_request_id": None, "client_request_id": "req-1",
        })
        ws2.send_json({
            "type": "voice_session_open", "conversation_id": None,
            "attention_request_id": None, "client_request_id": "req-2",
        })
        opened1 = ws1.receive_json()
        opened2 = ws2.receive_json()
        assert opened1["client_request_id"] == "req-1"
        assert opened2["client_request_id"] == "req-2"
        assert opened1["voice_session_id"] != opened2["voice_session_id"]
