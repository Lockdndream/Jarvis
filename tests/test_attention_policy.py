"""Milestone 7 Phase 7: AttentionPolicy — bounded, deterministic
classification of verified events into timeline/attention/notify/speak
actions. No LLM involved anywhere in this module.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app import attention_policy as ap


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


# ── ALWAYS NOTIFY ────────────────────────────────────────────────────

def test_question_created_always_notifies_and_speaks():
    d = ap.decide(ap.KIND_QUESTION_CREATED)
    assert ap.ACTION_NOTIFY in d["actions"]
    assert ap.ACTION_SPEAK in d["actions"]
    assert ap.ACTION_ATTENTION in d["actions"]
    assert d["notification_type"] == "QUESTION_REQUIRED"
    assert d["priority"] == "HIGH"


def test_permission_created_always_notifies_and_speaks():
    d = ap.decide(ap.KIND_PERMISSION_CREATED)
    assert ap.ACTION_NOTIFY in d["actions"]
    assert ap.ACTION_SPEAK in d["actions"]
    assert d["notification_type"] == "PERMISSION_REQUIRED"
    assert d["priority"] == "HIGH"


def test_task_failed_always_notifies_and_speaks():
    d = ap.decide(ap.KIND_TASK_FAILED)
    assert ap.ACTION_NOTIFY in d["actions"]
    assert ap.ACTION_SPEAK in d["actions"]
    assert d["notification_type"] == "TASK_FAILED"
    assert d["priority"] == "HIGH"


def test_supervisor_alert_always_notifies():
    d = ap.decide(ap.KIND_SUPERVISOR_ALERT)
    assert ap.ACTION_NOTIFY in d["actions"]
    assert d["notification_type"] == "SUPERVISOR_ALERT"


# ── CONFIGURABLE: completion ─────────────────────────────────────────

def test_task_completed_notifies_by_default():
    assert ap.notify_on_completion() is True
    d = ap.decide(ap.KIND_TASK_COMPLETED)
    assert ap.ACTION_NOTIFY in d["actions"]
    assert d["notification_type"] == "TASK_COMPLETED"
    assert d["priority"] == "NORMAL"


def test_task_completed_respects_env_disable(monkeypatch):
    monkeypatch.setenv("JARVIS_NOTIFY_ON_COMPLETION", "false")
    assert ap.notify_on_completion() is False
    d = ap.decide(ap.KIND_TASK_COMPLETED)
    assert d["actions"] == [ap.ACTION_TIMELINE]
    assert d["notification_type"] is None


def test_task_completed_setting_overrides_env(monkeypatch):
    monkeypatch.setenv("JARVIS_NOTIFY_ON_COMPLETION", "true")
    db.set_setting("notify_on_completion", "false")
    assert ap.notify_on_completion() is False


def test_task_completed_setting_can_re_enable(monkeypatch):
    monkeypatch.setenv("JARVIS_NOTIFY_ON_COMPLETION", "false")
    db.set_setting("notify_on_completion", "true")
    assert ap.notify_on_completion() is True


# ── DO NOT NOTIFY BY DEFAULT (noise) ──────────────────────────────────

def test_task_started_is_timeline_only():
    d = ap.decide(ap.KIND_TASK_STARTED)
    assert d["actions"] == [ap.ACTION_TIMELINE]
    assert d["notification_type"] is None


def test_routine_output_is_timeline_only():
    d = ap.decide(ap.KIND_ROUTINE_OUTPUT)
    assert d["actions"] == [ap.ACTION_TIMELINE]


def test_unrecognized_kind_is_timeline_only():
    d = ap.decide("some_unmodeled_event_kind")
    assert d["actions"] == [ap.ACTION_TIMELINE]
    assert d["notification_type"] is None
    assert d["priority"] is None
