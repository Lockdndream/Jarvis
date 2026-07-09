"""Milestone 7 Phase 6: notification DB schema, CRUD, and dedup guarantees.

One underlying verified event must produce exactly one logical notification
row, no matter how many times create_notification() is called for it — this
is the core guarantee Phase 14 (deduplication) depends on, enforced here at
the DB layer via the dedup_key UNIQUE constraint.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db


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


def _make(dedup_key="question_created:q1", **overrides):
    kwargs = dict(
        notification_id="notif_1",
        conversation_id="conv_abc123def456",
        task_id="oc_task1",
        source_type="opencode_question",
        source_id="q1",
        notification_type="QUESTION_REQUIRED",
        title="Jarvis needs your answer",
        body="A task is waiting for your answer.",
        priority="HIGH",
        dedup_key=dedup_key,
    )
    kwargs.update(overrides)
    return db.create_notification(**kwargs)


# ── CRUD ──────────────────────────────────────────────────────────

def test_create_notification_creates_row():
    row = _make()
    assert row["created"] is True
    assert row["notification_id"] == "notif_1"
    assert row["status"] == "pending"
    assert row["delivered_at"] is None
    assert row["read_at"] is None


def test_get_notification_by_id():
    _make()
    row = db.get_notification("notif_1")
    assert row is not None
    assert row["dedup_key"] == "question_created:q1"


def test_get_notification_by_dedup_key():
    _make()
    row = db.get_notification_by_dedup_key("question_created:q1")
    assert row is not None
    assert row["notification_id"] == "notif_1"


def test_get_notification_missing_returns_none():
    assert db.get_notification("does-not-exist") is None
    assert db.get_notification_by_dedup_key("does-not-exist") is None


# ── Dedup: the central Phase 14 guarantee ──────────────────────────

def test_create_notification_idempotent_by_dedup_key():
    first = _make()
    second = _make(notification_id="notif_2")  # different id, same dedup_key
    assert first["created"] is True
    assert second["created"] is False
    assert second["notification_id"] == "notif_1"  # original row returned, not a new one

    all_rows = db.get_recent_notifications(50)
    matching = [r for r in all_rows if r["dedup_key"] == "question_created:q1"]
    assert len(matching) == 1


def test_create_notification_different_dedup_keys_both_created():
    a = _make(dedup_key="question_created:q1", notification_id="notif_a")
    b = _make(dedup_key="question_created:q2", notification_id="notif_b")
    assert a["created"] is True
    assert b["created"] is True
    assert len(db.get_recent_notifications(50)) == 2


# ── Delivered / read lifecycle ─────────────────────────────────────

def test_mark_notification_delivered_sets_timestamp_once():
    _make()
    db.mark_notification_delivered("notif_1")
    first = db.get_notification("notif_1")
    assert first["delivered_at"] is not None

    db.mark_notification_delivered("notif_1")
    second = db.get_notification("notif_1")
    assert second["delivered_at"] == first["delivered_at"]  # not overwritten


def test_mark_notification_read_sets_status_and_timestamp():
    _make()
    db.mark_notification_read("notif_1")
    row = db.get_notification("notif_1")
    assert row["status"] == "read"
    assert row["read_at"] is not None


def test_get_pending_notifications_excludes_read():
    _make(dedup_key="k1", notification_id="notif_1")
    _make(dedup_key="k2", notification_id="notif_2")
    db.mark_notification_read("notif_1")

    pending = db.get_pending_notifications()
    ids = {n["notification_id"] for n in pending}
    assert "notif_1" not in ids
    assert "notif_2" in ids


def test_get_undelivered_notifications():
    _make()
    undelivered = db.get_undelivered_notifications()
    assert len(undelivered) == 1
    db.mark_notification_delivered("notif_1")
    assert db.get_undelivered_notifications() == []


# ── Push subscriptions ──────────────────────────────────────────────

def test_push_subscription_crud():
    db.save_push_subscription("https://push.example/ep1", "p256dh-key", "auth-key", "conv_abc123def456")
    subs = db.get_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://push.example/ep1"

    db.delete_push_subscription("https://push.example/ep1")
    assert db.get_push_subscriptions() == []


def test_push_subscription_upsert_on_endpoint():
    db.save_push_subscription("https://push.example/ep1", "old-key", "old-auth", None)
    db.save_push_subscription("https://push.example/ep1", "new-key", "new-auth", "conv_abc123def456")
    subs = db.get_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["p256dh"] == "new-key"
    assert subs[0]["conversation_id"] == "conv_abc123def456"


# ── Settings ─────────────────────────────────────────────────────────

def test_get_setting_returns_default_when_unset():
    assert db.get_setting("notify_on_completion") is None
    assert db.get_setting("notify_on_completion", "true") == "true"


def test_set_and_get_setting():
    db.set_setting("notify_on_completion", "false")
    assert db.get_setting("notify_on_completion") == "false"


def test_set_setting_overwrites():
    db.set_setting("notify_on_completion", "false")
    db.set_setting("notify_on_completion", "true")
    assert db.get_setting("notify_on_completion") == "true"
