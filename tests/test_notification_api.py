"""Milestone 7: REST endpoints for push subscription management, settings,
and deep-link resolution (GET /api/task, /api/question, /api/notification).

Exercises the real app.main FastAPI app via TestClient, with
opencode_supervisor.start()/stop() monkeypatched to no-ops so no real
`opencode serve` process is spawned — same pattern established in
tests/test_conversation_continuity.py.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db

# See tests/test_conversation_continuity.py for why this dotenv-leak guard
# is done at module import time rather than inside a fixture.
_ENV_BEFORE_IMPORT = dict(os.environ)
import app.main as _main_module
for _leaked_var in set(os.environ) - set(_ENV_BEFORE_IMPORT):
    if _leaked_var.startswith("JARVIS_LLM") or _leaked_var.startswith("OPENCODE_") or _leaked_var.startswith("JARVIS_VAPID"):
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

    monkeypatch.delenv("JARVIS_VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("JARVIS_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        yield c

    if os.path.exists(path):
        os.unlink(path)


# ── VAPID / push subscribe ────────────────────────────────────────

def test_vapid_public_key_unconfigured_by_default(client):
    resp = client.get("/api/vapid-public-key")
    assert resp.status_code == 200
    body = resp.json()
    assert body["push_configured"] is False
    assert body["vapid_public_key"] is None


def test_vapid_public_key_reports_configured(client, monkeypatch):
    monkeypatch.setenv("JARVIS_VAPID_PUBLIC_KEY", "fake-public-key")
    monkeypatch.setenv("JARVIS_VAPID_PRIVATE_KEY", "fake-private-key")
    resp = client.get("/api/vapid-public-key")
    body = resp.json()
    assert body["push_configured"] is True
    assert body["vapid_public_key"] == "fake-public-key"


def test_push_subscribe_and_unsubscribe(client):
    resp = client.post("/api/push/subscribe", json={
        "endpoint": "https://push.example/ep1",
        "keys": {"p256dh": "p-key", "auth": "a-key"},
        "conversation_id": None,
    })
    assert resp.status_code == 200
    assert len(db.get_push_subscriptions()) == 1

    resp = client.post("/api/push/unsubscribe", json={"endpoint": "https://push.example/ep1"})
    assert resp.status_code == 200
    assert db.get_push_subscriptions() == []


def test_push_subscribe_rejects_malformed(client):
    resp = client.post("/api/push/subscribe", json={"endpoint": "", "keys": {}})
    assert resp.status_code == 400


def test_push_subscribe_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "secret-token")
    resp = client.post("/api/push/subscribe", json={
        "endpoint": "https://push.example/ep1",
        "keys": {"p256dh": "p-key", "auth": "a-key"},
    })
    assert resp.status_code == 401

    resp = client.post(
        "/api/push/subscribe",
        json={"endpoint": "https://push.example/ep1", "keys": {"p256dh": "p-key", "auth": "a-key"}},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert resp.status_code == 200


# ── Settings ──────────────────────────────────────────────────────

def test_settings_default_notify_on_completion_true(client):
    resp = client.get("/api/settings")
    assert resp.json()["notify_on_completion"] is True


def test_settings_post_toggle(client):
    resp = client.post("/api/settings", json={"notify_on_completion": False})
    assert resp.json()["notify_on_completion"] is False
    resp = client.get("/api/settings")
    assert resp.json()["notify_on_completion"] is False


# ── Deep-link resolution endpoints ─────────────────────────────────

def test_get_task_404_when_missing(client):
    resp = client.get("/api/task/does-not-exist")
    assert resp.status_code == 404


def test_get_task_returns_summary(client):
    db.create_task_record("task1", "Demo Task", "cmd")
    resp = client.get("/api/task/task1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "task1"
    assert body["name"] == "Demo Task"
    assert body["status"] == "running"


def test_get_question_404_when_missing(client):
    resp = client.get("/api/question/does-not-exist")
    assert resp.status_code == 404


def test_get_question_returns_status_for_stale_detection(client):
    db.create_task_record("task1", "Demo Task", "cmd")
    db.create_question_record("q1", "task1", "Which approach?", "", "[]")
    resp = client.get("/api/question/q1")
    assert resp.json()["status"] == "pending"

    db.answer_question_record("q1", "B")
    resp = client.get("/api/question/q1")
    assert resp.json()["status"] == "answered"


def test_get_notification_marks_read_and_returns_routing(client):
    db.create_task_record("task1", "Demo Task", "cmd")
    row = db.create_notification(
        notification_id="notif_1", conversation_id="conv_abc123def456", task_id="task1",
        source_type="opencode_question", source_id="q1", notification_type="QUESTION_REQUIRED",
        title="Jarvis needs your answer", body="Demo Task is waiting for your answer.",
        priority="HIGH", dedup_key="question_created:q1",
    )
    assert row["status"] == "pending"

    resp = client.get("/api/notification/notif_1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "task1"
    assert body["source_id"] == "q1"
    assert body["status"] == "read"

    assert db.get_notification("notif_1")["status"] == "read"


def test_get_notification_404_when_missing(client):
    resp = client.get("/api/notification/does-not-exist")
    assert resp.status_code == 404


# ── Reconnect delivers pending notifications, read ones are not resent ──

def _drain_initial(ws, expect_type):
    for _ in range(6):
        msg = ws.receive_json()
        if msg.get("type") == expect_type:
            return msg
    raise AssertionError(f"never received a {expect_type} message")


def test_reconnect_resends_pending_notification(client):
    db.create_task_record("task1", "Demo Task", "cmd")
    db.create_notification(
        notification_id="notif_1", conversation_id=None, task_id="task1",
        source_type="opencode_question", source_id="q1", notification_type="QUESTION_REQUIRED",
        title="Jarvis needs your answer", body="Demo Task is waiting for your answer.",
        priority="HIGH", dedup_key="question_created:q1",
    )

    with client.websocket_connect("/ws") as ws:
        msg = _drain_initial(ws, "pending_notifications")
        ids = [n["notification_id"] for n in msg["notifications"]]
        assert ids == ["notif_1"]


def test_reconnect_does_not_resend_read_notification(client):
    db.create_task_record("task1", "Demo Task", "cmd")
    db.create_notification(
        notification_id="notif_1", conversation_id=None, task_id="task1",
        source_type="opencode_question", source_id="q1", notification_type="QUESTION_REQUIRED",
        title="Jarvis needs your answer", body="Demo Task is waiting for your answer.",
        priority="HIGH", dedup_key="question_created:q1",
    )
    db.mark_notification_read("notif_1")

    with client.websocket_connect("/ws") as ws:
        # No pending_notifications message should ever arrive — drain a
        # bounded number of the fixed initial messages and confirm.
        seen_types = set()
        for _ in range(4):
            msg = ws.receive_json()
            seen_types.add(msg.get("type"))
            if "opencode_status" in seen_types:
                break
        assert "pending_notifications" not in seen_types
