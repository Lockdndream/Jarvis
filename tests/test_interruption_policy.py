"""Milestone 8 Phase 6/23: deterministic InterruptionPolicy decision table.

No speculative AI interruption scoring — every branch here is a pure
function of (attention_type, urgency, status, connected, prior_contact_count,
quiet-hours config). No I/O, no DB writes.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import interruption_policy as ip


def _row(attention_type="QUESTION", urgency="HIGH", status="pending"):
    return {"attention_type": attention_type, "urgency": urgency, "status": status}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("JARVIS_QUIET_HOURS", raising=False)
    monkeypatch.delenv("JARVIS_ATTENTION_RETRY_MINUTES", raising=False)


def test_deferred_status_always_means_defer_action():
    row = _row(urgency="URGENT", status="deferred")
    assert ip.decide(row, connected=True, prior_contact_count=0) == ip.ACTION_DEFER


def test_urgent_question_connected_first_contact_offers_voice():
    row = _row(attention_type="QUESTION", urgency="URGENT")
    assert ip.decide(row, connected=True, prior_contact_count=0) == ip.ACTION_VOICE_WHEN_AVAILABLE


def test_urgent_question_connected_repeat_contact_is_in_app():
    row = _row(attention_type="QUESTION", urgency="URGENT")
    assert ip.decide(row, connected=True, prior_contact_count=1) == ip.ACTION_IN_APP


def test_urgent_question_disconnected_is_push():
    row = _row(attention_type="QUESTION", urgency="URGENT")
    assert ip.decide(row, connected=False, prior_contact_count=0) == ip.ACTION_PUSH


def test_urgent_permission_same_as_urgent_question():
    row = _row(attention_type="PERMISSION", urgency="URGENT")
    assert ip.decide(row, connected=True, prior_contact_count=0) == ip.ACTION_VOICE_WHEN_AVAILABLE


def test_normal_question_connected_is_in_app():
    row = _row(attention_type="QUESTION", urgency="HIGH")
    assert ip.decide(row, connected=True, prior_contact_count=0) == ip.ACTION_IN_APP


def test_normal_question_disconnected_first_contact_is_push():
    row = _row(attention_type="QUESTION", urgency="HIGH")
    assert ip.decide(row, connected=False, prior_contact_count=0) == ip.ACTION_PUSH


def test_normal_question_disconnected_repeat_contact_is_silent_not_a_push_storm():
    row = _row(attention_type="QUESTION", urgency="HIGH")
    assert ip.decide(row, connected=False, prior_contact_count=1) == ip.ACTION_SILENT


def test_task_failure_first_contact_notifies():
    row = _row(attention_type="TASK_FAILURE", urgency="HIGH")
    assert ip.decide(row, connected=True, prior_contact_count=0) == ip.ACTION_IN_APP
    assert ip.decide(row, connected=False, prior_contact_count=0) == ip.ACTION_PUSH


def test_task_failure_repeat_contact_is_silent():
    row = _row(attention_type="TASK_FAILURE", urgency="HIGH")
    assert ip.decide(row, connected=True, prior_contact_count=1) == ip.ACTION_SILENT


def test_supervisor_escalation_always_escalates():
    row = _row(attention_type="SUPERVISOR_ESCALATION", urgency="HIGH")
    assert ip.decide(row, connected=True, prior_contact_count=0) == ip.ACTION_ESCALATE
    assert ip.decide(row, connected=False, prior_contact_count=5) == ip.ACTION_ESCALATE


def test_task_completion_is_silent_defensive_default():
    # Phase 4: completion never becomes an unresolved AttentionRequest at
    # all in normal operation, so this is a defensive default, not a real
    # path — still must never surprise-interrupt if ever reached.
    row = _row(attention_type="TASK_COMPLETION", urgency="HIGH")
    assert ip.decide(row, connected=True, prior_contact_count=0) == ip.ACTION_SILENT


def test_unrecognized_attention_type_is_silent():
    row = _row(attention_type="SOMETHING_NEW", urgency="HIGH")
    assert ip.decide(row, connected=True, prior_contact_count=0) == ip.ACTION_SILENT


# ── Quiet hours (server-local time, explicit, off by default) ─────────

def test_quiet_hours_off_by_default():
    row = _row(attention_type="QUESTION", urgency="HIGH")
    now = datetime.now(timezone.utc)  # whatever time it happens to be
    assert ip.decide(row, connected=True, prior_contact_count=0, now=now) == ip.ACTION_IN_APP


def test_quiet_hours_defers_non_urgent(monkeypatch):
    monkeypatch.setenv("JARVIS_QUIET_HOURS", "00:00-23:59")
    row = _row(attention_type="QUESTION", urgency="HIGH")
    now = datetime.now(timezone.utc)
    assert ip.decide(row, connected=True, prior_contact_count=0, now=now) == ip.ACTION_DEFER


def test_quiet_hours_never_silences_urgent(monkeypatch):
    monkeypatch.setenv("JARVIS_QUIET_HOURS", "00:00-23:59")
    row = _row(attention_type="QUESTION", urgency="URGENT")
    now = datetime.now(timezone.utc)
    assert ip.decide(row, connected=True, prior_contact_count=0, now=now) != ip.ACTION_DEFER


def test_malformed_quiet_hours_config_is_ignored(monkeypatch):
    monkeypatch.setenv("JARVIS_QUIET_HOURS", "garbage")
    row = _row(attention_type="QUESTION", urgency="HIGH")
    now = datetime.now(timezone.utc)
    assert ip.decide(row, connected=True, prior_contact_count=0, now=now) == ip.ACTION_IN_APP


# ── Retry backoff ────────────────────────────────────────────────────

def test_default_retry_minutes():
    assert ip.next_retry_delay_minutes(now=datetime.now(timezone.utc)) == 30


def test_configured_retry_minutes(monkeypatch):
    # DEFAULT_RETRY_MINUTES is read once at module import time, not
    # per-call — this is a real, intentional characteristic (it's a
    # deployment-time knob, not a per-request one), so exercising it
    # requires reloading the module rather than just setting the env var.
    import importlib
    monkeypatch.setenv("JARVIS_ATTENTION_RETRY_MINUTES", "5")
    reloaded = importlib.reload(ip)
    try:
        assert reloaded.next_retry_delay_minutes(now=datetime.now(timezone.utc)) == 5
    finally:
        monkeypatch.delenv("JARVIS_ATTENTION_RETRY_MINUTES", raising=False)
        importlib.reload(ip)


def test_quiet_hours_retry_is_shorter(monkeypatch):
    monkeypatch.setenv("JARVIS_QUIET_HOURS", "00:00-23:59")
    assert ip.next_retry_delay_minutes(now=datetime.now(timezone.utc)) == 15
