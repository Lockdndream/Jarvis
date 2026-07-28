"""Milestone 6, Phase 7: conversation_id continuity across WebSocket turns
and reconnects.

Exercises the real app.main WebSocket endpoint via FastAPI's TestClient, with
opencode_supervisor.start()/stop() monkeypatched to no-ops so no real
`opencode serve` process is spawned — this project's existing tests always
construct components directly rather than importing app.main, specifically
to avoid this kind of app-level side effect; here we need the real endpoint
wiring, so isolation is done explicitly instead.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db

# Importing app.main triggers its module-level load_dotenv() call, which —
# the first time this happens anywhere in the process — pulls the real
# .env (including a real JARVIS_LLM_API_KEY) into os.environ via a direct
# mutation. monkeypatch cannot see or auto-revert that (it only tracks
# changes made through monkeypatch itself), so doing this inside a
# per-test fixture would permanently leak a real key into every test that
# runs afterward in the same pytest session — silently switching
# Supervisor() from FakeLLMProvider to a real LLMProvider anywhere else in
# the suite. Import once here, at module load, and immediately strip
# exactly what load_dotenv() added, with plain os.environ mutation (not
# monkeypatch) so nothing "restores" the leaked value later.
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
    """Consume the fixed sequence of connect-time messages (history,
    opencode_status; running_tasks/pending_questions only sent if non-empty)."""
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


def test_first_connection_gets_conversation_id(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({"type": "conversation_init", "conversation_id": None})
        ready = ws.receive_json()
        assert ready["type"] == "conversation_ready"
        assert db.is_valid_conversation_id(ready["conversation_id"])
        assert ready["history"] == []


def test_subsequent_turn_reuses_conversation_id(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({"type": "conversation_init", "conversation_id": None})
        ready = ws.receive_json()
        cid = ready["conversation_id"]

        ws.send_json({"type": "user_message", "content": "hello", "conversation_id": cid})
        echoed = ws.receive_json()
        assert echoed["type"] == "user_message"
        thinking = ws.receive_json()
        assert thinking["type"] == "supervisor_thinking"
        reply = ws.receive_json()
        assert reply["type"] == "supervisor_message"
        assert reply["conversation_id"] == cid

        msgs = db.get_conversation_messages(cid)
        assert any(m["role"] == "user" and m["content"] == "hello" for m in msgs)


def test_reconnect_preserves_conversation_id_and_restores_history(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({"type": "conversation_init", "conversation_id": None})
        cid = ws.receive_json()["conversation_id"]
        ws.send_json({"type": "user_message", "content": "remember this", "conversation_id": cid})
        ws.receive_json()  # echo
        ws.receive_json()  # thinking
        ws.receive_json()  # supervisor_message

    with client.websocket_connect("/ws") as ws2:
        _drain_initial(ws2)
        ws2.send_json({"type": "conversation_init", "conversation_id": cid})
        ready2 = ws2.receive_json()
        assert ready2["conversation_id"] == cid
        assert any(h["role"] == "assistant" for h in ready2["history"])


def test_second_conversation_is_isolated(client):
    with client.websocket_connect("/ws") as ws_a:
        _drain_initial(ws_a)
        ws_a.send_json({"type": "conversation_init", "conversation_id": None})
        cid_a = ws_a.receive_json()["conversation_id"]
        ws_a.send_json({"type": "user_message", "content": "conversation A secret", "conversation_id": cid_a})
        ws_a.receive_json()
        ws_a.receive_json()
        ws_a.receive_json()

    with client.websocket_connect("/ws") as ws_b:
        _drain_initial(ws_b)
        ws_b.send_json({"type": "conversation_init", "conversation_id": None})
        ready_b = ws_b.receive_json()
        cid_b = ready_b["conversation_id"]
        assert cid_b != cid_a
        assert ready_b["history"] == []

        history_b = db.get_conversation_messages(cid_b)
        assert not any("conversation A secret" in m["content"] for m in history_b)


def test_malformed_conversation_id_is_rejected_and_replaced(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        for bad_id in ["'; DROP TABLE conversations; --", "not-a-real-id", "", None, 12345, "conv_short", "conv_" + "a" * 40]:
            ws.send_json({"type": "conversation_init", "conversation_id": bad_id})
            ready = ws.receive_json()
            assert db.is_valid_conversation_id(ready["conversation_id"]), f"expected a fresh valid id for input {bad_id!r}"


def test_bounded_history_respected(client):
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)
        ws.send_json({"type": "conversation_init", "conversation_id": None})
        cid = ws.receive_json()["conversation_id"]

    # Seed far more messages than the bounded restore limit.
    import app.main as main_module
    for i in range(main_module.CONVERSATION_HISTORY_LIMIT + 15):
        db.save_conversation_message(cid, "user", f"msg {i}")
        db.save_conversation_message(cid, "assistant", f"reply {i}")

    with client.websocket_connect("/ws") as ws2:
        _drain_initial(ws2)
        ws2.send_json({"type": "conversation_init", "conversation_id": cid})
        ready = ws2.receive_json()
        assert len(ready["history"]) <= main_module.CONVERSATION_HISTORY_LIMIT
