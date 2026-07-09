"""Milestone 7 Phases 6/7/8/14 fake integration: verified OpenCode events
(question created, permission created, verified failure, verified
completion) produce exactly one logical notification each, delivered over
the same ConnectionManager broadcast used for every other WebSocket event —
and replaying the same underlying event never produces a second one.

Follows the same harness pattern as tests/test_opencode_lifecycle.py
(OpenCodeSupervisor.__new__ + a real ConnectionManager), extended with the
attributes the Milestone 7 notification wiring also reads.
"""
import collections
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app.integrations.opencode_supervisor import OpenCodeSupervisor


@pytest.fixture(autouse=True)
def test_db():
    old_path = db.DB_PATH
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = path
    db.init_db()
    yield
    db.DB_PATH = old_path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("JARVIS_NOTIFY_ON_COMPLETION", raising=False)


class RecordingWebSocket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def make_supervisor():
    sv = OpenCodeSupervisor.__new__(OpenCodeSupervisor)
    sv.cm = ConnectionManager()
    sv._completion_events = {}
    sv._error_since_prompt = {}
    sv._seen_event_ids = collections.deque(maxlen=500)
    sv._seen_event_ids_set = set()
    return sv


def make_task(task_id="oc_task1", session_id="ses_1", project_dir="/tmp/proj", status="running"):
    db.create_task_record(task_id, "Test Task", "do the thing")
    db.create_opencode_task_record(task_id, session_id, project_dir, "do the thing")
    if status != "running":
        db.update_task_status(task_id, status)
        db.update_opencode_task_status(task_id, status)
    return task_id, session_id


def _notification_types(sent):
    return [m["notification_type"] for m in sent if m.get("type") == "notification"]


# ── One verified event → one notification ────────────────────────────

@pytest.mark.asyncio
async def test_question_created_produces_one_notification():
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    task_id, _ = make_task()

    await sv._emit_question({
        "task_id": task_id, "request_id": "q1", "question": "Which approach?", "options": ["A", "B"], "context": "",
    })

    assert _notification_types(ws.sent) == ["QUESTION_REQUIRED"]
    rows = db.get_recent_notifications(50)
    assert len(rows) == 1
    assert rows[0]["source_id"] == "q1"
    assert rows[0]["priority"] == "HIGH"


@pytest.mark.asyncio
async def test_permission_created_produces_one_notification():
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    task_id, _ = make_task()

    await sv._emit_permission({
        "task_id": task_id, "request_id": "p1", "action": "write", "path": "/tmp/proj/secret.txt",
    })

    assert _notification_types(ws.sent) == ["PERMISSION_REQUIRED"]
    rows = db.get_recent_notifications(50)
    assert len(rows) == 1
    # Phase 15: never leak the raw path into the notification body.
    assert "/tmp/proj/secret.txt" not in rows[0]["body"]


@pytest.mark.asyncio
async def test_verified_failure_produces_one_notification():
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    task_id, _ = make_task()

    await sv._handle_session_failed({"task_id": task_id, "error": {"message": "boom"}})

    assert _notification_types(ws.sent) == ["TASK_FAILED"]
    rows = db.get_recent_notifications(50)
    assert len(rows) == 1
    assert rows[0]["priority"] == "HIGH"


@pytest.mark.asyncio
async def test_verified_completion_produces_notification_by_default():
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    task_id, _ = make_task()

    await sv._handle_session_idle({"task_id": task_id})

    assert _notification_types(ws.sent) == ["TASK_COMPLETED"]
    rows = db.get_recent_notifications(50)
    assert len(rows) == 1
    assert rows[0]["priority"] == "NORMAL"


@pytest.mark.asyncio
async def test_completion_notification_suppressed_when_policy_disabled():
    db.set_setting("notify_on_completion", "false")
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    task_id, _ = make_task()

    await sv._handle_session_idle({"task_id": task_id})

    assert _notification_types(ws.sent) == []
    assert db.get_recent_notifications(50) == []


# ── Phase 14: replay/reconnect/restart must not duplicate ─────────────

@pytest.mark.asyncio
async def test_duplicate_failure_evidence_does_not_duplicate_notification():
    """The task's own terminal-state guard (already-terminal tasks are
    ignored) is the primary defense; the notification dedup_key is the
    second line of defense. Verify the end result: exactly one row."""
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    task_id, _ = make_task()

    await sv._handle_session_failed({"task_id": task_id, "error": {}})
    await sv._handle_session_failed({"task_id": task_id, "error": {}})  # SSE replay / duplicate delivery

    assert len(db.get_recent_notifications(50)) == 1
    assert _notification_types(ws.sent) == ["TASK_FAILED"]  # broadcast only once


@pytest.mark.asyncio
async def test_restart_reprocessing_does_not_duplicate_completion_notification():
    """Simulates a Jarvis restart re-delivering the same terminal SSE event
    against a task that is already marked completed — must not re-notify."""
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    task_id, _ = make_task()

    await sv._handle_session_idle({"task_id": task_id})
    assert len(db.get_recent_notifications(50)) == 1

    sv2 = make_supervisor()  # fresh supervisor instance, as after a restart
    ws2 = RecordingWebSocket()
    await sv2.cm.connect(ws2)
    await sv2._handle_session_idle({"task_id": task_id})  # task already terminal in DB

    assert len(db.get_recent_notifications(50)) == 1  # still exactly one
    assert _notification_types(ws2.sent) == []  # nothing (re-)broadcast on the new connection


@pytest.mark.asyncio
async def test_notify_idempotent_call_does_not_reduplicate_or_rebroadcast():
    """Directly exercises app.notifications.notify()'s own idempotency —
    the mechanism the two tests above rely on — independent of any upstream
    guard, covering Phase 14's "push delivery retry" / "multiple browser
    tabs" style re-entry."""
    from app import notifications

    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    task_id, _ = make_task()

    from app import attention_policy as ap

    kwargs = dict(
        conversation_id=None, task_id=task_id, source_type="opencode_task",
        source_id=task_id, title="Jarvis task failed", body="Test Task failed.",
    )
    first = await notifications.notify(cm, ap.KIND_TASK_FAILED, **kwargs)
    second = await notifications.notify(cm, ap.KIND_TASK_FAILED, **kwargs)

    assert first["notification_id"] == second["notification_id"]
    assert len(db.get_recent_notifications(50)) == 1
    assert len(_notification_types(ws.sent)) == 1  # only the first call broadcast


@pytest.mark.asyncio
async def test_task_started_never_produces_a_notification():
    """Routine progress must not become a proactive notification by default
    (Milestone 7 principle 6). No handler in opencode_supervisor.py emits
    task_started through the notification path at all — verified here by
    confirming AttentionPolicy itself rejects it, since that's the single
    gate every producer is required to go through."""
    from app import attention_policy as ap

    d = ap.decide(ap.KIND_TASK_STARTED)
    assert "notify" not in d["actions"]
