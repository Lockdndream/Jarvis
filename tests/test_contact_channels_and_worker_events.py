"""Milestone 8 Phase 15/16: ContactChannel abstraction + worker-event
normalization.

Every channel reuses app.notifications.notify() — this suite verifies the
orchestration/branching (delivered vs attempted vs failed, the S24 Doze
PUSH_ACCEPTED_BY_PUSH_SERVICE distinction) without duplicating
test_notification_integration.py's coverage of notify() itself.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app import contact_channels as cc
from app import worker_events


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
    monkeypatch.delenv("JARVIS_VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("JARVIS_VAPID_PRIVATE_KEY", raising=False)


class RecordingWebSocket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def _attention_row(attention_type="QUESTION", source_type="local_question", source_id="q1"):
    return {
        "attention_request_id": "attn_1",
        "conversation_id": "c1",
        "task_id": "t1",
        "source_type": source_type,
        "source_id": source_id,
        "attention_type": attention_type,
        "summary": "Which approach?",
    }


# ── get_channel factory ─────────────────────────────────────────────

def test_get_channel_returns_correct_implementations():
    assert isinstance(cc.get_channel(cc.CHANNEL_IN_APP), cc.InAppChannel)
    assert isinstance(cc.get_channel(cc.CHANNEL_PUSH), cc.PushChannel)
    assert isinstance(cc.get_channel(cc.CHANNEL_VOICE_SESSION), cc.VoiceInvitationChannel)


def test_get_channel_raises_for_reserved_unimplemented_channels():
    for reserved in (cc.CHANNEL_NATIVE_ANDROID, cc.CHANNEL_PHONE_CALL, cc.CHANNEL_SMS):
        with pytest.raises(ValueError):
            cc.get_channel(reserved)


def test_get_channel_raises_for_unknown_channel():
    with pytest.raises(ValueError):
        cc.get_channel("NOT_A_CHANNEL")


# ── InAppChannel ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_in_app_channel_delivers_and_creates_a_notification():
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    channel = cc.InAppChannel()
    result = await channel.attempt_contact(cm, _attention_row(), "cta_1")
    assert result["status"] == "delivered"
    assert result["notification_id"] is not None
    notif_types = [m["notification_type"] for m in ws.sent if m.get("type") == "notification"]
    assert "QUESTION_REQUIRED" in notif_types


@pytest.mark.asyncio
async def test_in_app_channel_declines_for_unmapped_attention_type():
    cm = ConnectionManager()
    channel = cc.InAppChannel()
    result = await channel.attempt_contact(cm, _attention_row(attention_type="TASK_COMPLETION"), "cta_1")
    assert result["status"] == "failed"
    assert result["result"] == "policy_declined"


# ── PushChannel: honest delivery-certainty distinction (S24 finding) ──

@pytest.mark.asyncio
async def test_push_channel_without_vapid_or_subscriptions_reports_in_app_only():
    cm = ConnectionManager()
    channel = cc.PushChannel()
    result = await channel.attempt_contact(cm, _attention_row(), "cta_1")
    assert result["status"] == "delivered"
    assert "in_app_broadcast_only" in result["result"]


@pytest.mark.asyncio
async def test_push_channel_with_vapid_and_subscription_never_claims_confirmed_delivery(monkeypatch):
    monkeypatch.setenv("JARVIS_VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("JARVIS_VAPID_PRIVATE_KEY", "priv")
    db.save_push_subscription("https://example.com/ep", "p256dh", "auth", "c1")
    cm = ConnectionManager()
    channel = cc.PushChannel()
    result = await channel.attempt_contact(cm, _attention_row(), "cta_1")
    assert result["status"] == "attempted"  # not "delivered" — platform can't confirm a human saw it
    assert "not confirmed" in result["result"]


# ── VoiceInvitationChannel: never auto-starts mic, just broadcasts ────

@pytest.mark.asyncio
async def test_voice_invitation_channel_broadcasts_invitation_and_notifies():
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)
    channel = cc.VoiceInvitationChannel()
    result = await channel.attempt_contact(cm, _attention_row(), "cta_1")
    assert result["status"] == "attempted"
    types = [m["type"] for m in ws.sent]
    assert "voice_session_invitation" in types
    invitation = [m for m in ws.sent if m["type"] == "voice_session_invitation"][0]
    assert invitation["attention_request_id"] == "attn_1"
    # No field here instructs a client to auto-start recognition — the
    # payload is purely descriptive (Phase 13 explicit constraint).
    assert "auto_start" not in invitation


# ── worker_events normalization ─────────────────────────────────────

def test_question_required_source_type_local_vs_opencode():
    local_ev = worker_events.WorkerAttentionEvent(
        worker_type="mock_worker", worker_task_id="t1", event_type=worker_events.QUESTION_REQUIRED,
        source_id="q1", summary="s",
    )
    oc_ev = worker_events.WorkerAttentionEvent(
        worker_type="opencode", worker_task_id="t1", event_type=worker_events.QUESTION_REQUIRED,
        source_id="q1", summary="s",
    )
    assert local_ev.source_type == "local_question"
    assert oc_ev.source_type == "opencode_question"
    assert local_ev.attention_type == "QUESTION"


def test_permission_required_source_type_is_always_opencode():
    ev = worker_events.WorkerAttentionEvent(
        worker_type="opencode", worker_task_id="t1", event_type=worker_events.PERMISSION_REQUIRED,
        source_id="p1", summary="s",
    )
    assert ev.source_type == "opencode_permission"
    assert ev.attention_type == "PERMISSION"


def test_task_failed_source_type_local_vs_opencode():
    local_ev = worker_events.WorkerAttentionEvent(
        worker_type="mock_worker", worker_task_id="t1", event_type=worker_events.TASK_FAILED,
        source_id="t1", summary="s",
    )
    oc_ev = worker_events.WorkerAttentionEvent(
        worker_type="opencode", worker_task_id="t1", event_type=worker_events.TASK_FAILED,
        source_id="t1", summary="s",
    )
    assert local_ev.source_type == "local_task"
    assert oc_ev.source_type == "opencode_task"
    assert local_ev.attention_type == "TASK_FAILURE"


@pytest.mark.asyncio
async def test_create_attention_delegates_through_to_attention_manager():
    cm = ConnectionManager()
    ev = worker_events.WorkerAttentionEvent(
        worker_type="opencode", worker_task_id="t1", event_type=worker_events.QUESTION_REQUIRED,
        source_id="q1", summary="Which approach?", conversation_id="c1", urgency="HIGH",
    )
    row = await worker_events.create_attention(cm, ev)
    assert row["source_type"] == "opencode_question"
    assert row["source_id"] == "q1"
    assert row["attention_type"] == "QUESTION"
    assert db.get_attention_request_by_source("opencode_question", "q1") is not None
