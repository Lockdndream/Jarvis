"""Milestone 9B.1: /ws handshake honors JARVIS_API_TOKEN when set, and stays
a no-op when unset (same shared-secret gate as _require_api_token, applied
to the WebSocket endpoint that previously ignored it entirely — the Android
companion's pairing token needs a server side that actually checks it).
"""
import os
import sys
import tempfile

import pytest
from starlette.websockets import WebSocketDisconnect

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


def test_ws_unauthenticated_by_default_when_token_unset(client, monkeypatch):
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "history"


def test_ws_rejects_missing_authorization_when_token_set(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()


def test_ws_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"authorization": "Bearer wrong"}) as ws:
            ws.receive_json()


def test_ws_accepts_correct_bearer_token(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    with client.websocket_connect("/ws", headers={"authorization": "Bearer secret-token"}) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "history"


# Milestone 9B.2 (ADR-014): unified WebSocket authentication via ?token=.

def test_ws_accepts_valid_signed_token_when_api_token_unset(client, monkeypatch):
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    from app.integrations.ws_tokens import issue_ws_token
    token = issue_ws_token("test-device")["token"]
    with client.websocket_connect(f"/ws?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "history"


def test_ws_accepts_valid_signed_token_when_api_token_set(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    from app.integrations.ws_tokens import issue_ws_token
    token = issue_ws_token("test-device")["token"]
    with client.websocket_connect(f"/ws?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "history"


def test_ws_rejects_expired_token_with_close_code_4001(client, monkeypatch):
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    from app.integrations.ws_tokens import issue_ws_token
    token = issue_ws_token("test-device", ttl_seconds=-1)["token"]
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4001


def test_ws_rejects_tampered_token_with_close_code_4002(client, monkeypatch):
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    from app.integrations.ws_tokens import issue_ws_token
    token = issue_ws_token("test-device")["token"]
    parts = token.split(".")
    tampered_sig = ("X" if parts[2][0] != "X" else "Y") + parts[2][1:]
    tampered = parts[0] + "." + parts[1] + "." + tampered_sig
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws?token={tampered}") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4002


def test_ws_rejects_garbage_token_with_close_code_4002(client, monkeypatch):
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws?token=not-a-real-token") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4002


def test_ws_missing_token_and_header_gives_close_code_4003_when_token_set(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4003


def test_ws_wrong_legacy_header_gives_close_code_4004(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws", headers={"authorization": "Bearer wrong"}) as ws:
            ws.receive_json()
    assert exc_info.value.code == 4004


def test_ws_token_takes_priority_over_legacy_header_when_both_present(client, monkeypatch):
    """A present but expired/invalid token must not silently fall back to
    the legacy header even if that header would otherwise be valid — the
    token path, once attempted, owns the outcome."""
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    from app.integrations.ws_tokens import issue_ws_token
    expired_token = issue_ws_token("test-device", ttl_seconds=-1)["token"]
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/ws?token={expired_token}", headers={"authorization": "Bearer secret-token"}
        ) as ws:
            ws.receive_json()
    assert exc_info.value.code == 4001


def test_legacy_authorization_header_still_works_migration_compatibility(client, monkeypatch):
    """The deprecated Authorization-header path (ADR-011) must keep
    working unmodified for already-paired clients that haven't migrated
    to fetching a signed token yet."""
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    with client.websocket_connect("/ws", headers={"authorization": "Bearer secret-token"}) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "history"


def test_ws_token_endpoint_issues_a_working_token(client, monkeypatch):
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    resp = client.post("/api/ws-token", json={"client_id": "integration-test-device"})
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body and "expires_at" in body and "expires_in" in body
    with client.websocket_connect(f"/ws?token={body['token']}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "history"


def test_ws_token_endpoint_requires_api_token_when_set(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    resp = client.post("/api/ws-token", json={"client_id": "integration-test-device"})
    assert resp.status_code == 401


def test_ws_token_endpoint_works_with_correct_api_token(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    resp = client.post(
        "/api/ws-token",
        json={"client_id": "integration-test-device"},
        headers={"authorization": "Bearer secret-token"},
    )
    assert resp.status_code == 200


def test_ws_token_endpoint_defaults_client_id_when_omitted(client, monkeypatch):
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    resp = client.post("/api/ws-token", json={})
    assert resp.status_code == 200
    assert "token" in resp.json()
