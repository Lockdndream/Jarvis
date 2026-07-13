"""Milestone 8 Phase 7/23: deterministic deferral/snooze phrase parsing.

Never invents a time for a vague phrase unless JARVIS_DEFAULT_SNOOZE_MINUTES
is explicitly configured; server-local time is used for daypart targets,
never silently UTC.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import deferral


NOW = datetime(2026, 7, 10, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("JARVIS_DEFAULT_SNOOZE_MINUTES", raising=False)


def test_digit_minutes():
    r = deferral.parse_defer_phrase("Come back in 15 minutes.", now=NOW)
    assert r.kind == "resolved"
    assert r.deferred_until == "2026-07-10T10:15:00Z"


def test_word_form_minutes():
    r = deferral.parse_defer_phrase("Come back in fifteen minutes.", now=NOW)
    assert r.kind == "resolved"
    assert r.deferred_until == "2026-07-10T10:15:00Z"


def test_forty_five_minutes_word_form_hyphenated():
    r = deferral.parse_defer_phrase("come back in forty-five minutes", now=NOW)
    assert r.kind == "resolved"
    assert r.deferred_until == "2026-07-10T10:45:00Z"


def test_digit_hours():
    r = deferral.parse_defer_phrase("remind me in 2 hours", now=NOW)
    assert r.kind == "resolved"
    assert r.deferred_until == "2026-07-10T12:00:00Z"


def test_an_hour():
    r = deferral.parse_defer_phrase("come back in an hour", now=NOW)
    assert r.kind == "resolved"
    assert r.deferred_until == "2026-07-10T11:00:00Z"


def test_a_hour_variant():
    r = deferral.parse_defer_phrase("in a hour", now=NOW)
    assert r.kind == "resolved"
    assert r.deferred_until == "2026-07-10T11:00:00Z"


def test_tomorrow_morning_uses_local_daypart_hour():
    r = deferral.parse_defer_phrase("tomorrow morning", now=NOW)
    assert r.kind == "resolved"
    local_target = datetime.fromisoformat(r.deferred_until.replace("Z", "+00:00")).astimezone()
    assert local_target.hour == 8
    assert local_target.date() == (NOW.astimezone() + timedelta(days=1)).date()


def test_bare_tomorrow_defaults_to_morning():
    r = deferral.parse_defer_phrase("tomorrow", now=NOW)
    assert r.kind == "resolved"
    local_target = datetime.fromisoformat(r.deferred_until.replace("Z", "+00:00")).astimezone()
    assert local_target.hour == 8


def _local_now_at(hour, minute=0):
    """A UTC-tagged instant whose *local* wall-clock time is exactly
    hour:minute today — avoids any assumption about the test machine's
    timezone offset from UTC."""
    local_today = datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local_today.astimezone(timezone.utc)


def test_bare_daypart_today_if_still_ahead():
    now = _local_now_at(6, 0)  # well before any daypart hour (8/15/19) today
    r = deferral.parse_defer_phrase("evening", now=now)
    assert r.kind == "resolved"
    local_target = datetime.fromisoformat(r.deferred_until.replace("Z", "+00:00")).astimezone()
    assert local_target.hour == 19
    assert local_target.date() == now.astimezone().date()


def test_bare_daypart_rolls_to_tomorrow_if_already_passed():
    now = _local_now_at(23, 0)  # after every daypart hour today
    r = deferral.parse_defer_phrase("morning", now=now)
    assert r.kind == "resolved"
    local_target = datetime.fromisoformat(r.deferred_until.replace("Z", "+00:00")).astimezone()
    assert local_target.hour == 8
    assert local_target.date() == (now.astimezone() + timedelta(days=1)).date()


@pytest.mark.parametrize("phrase", ["later", "not now", "not right now", "some other time", "maybe later"])
def test_vague_phrases_ask_for_clarification_by_default(phrase):
    r = deferral.parse_defer_phrase(phrase, now=NOW)
    assert r.kind == "vague"
    assert r.message == "When should I come back?"


def test_vague_phrase_uses_configured_default_when_set(monkeypatch):
    monkeypatch.setenv("JARVIS_DEFAULT_SNOOZE_MINUTES", "20")
    r = deferral.parse_defer_phrase("not now", now=NOW)
    assert r.kind == "resolved"
    assert r.deferred_until == "2026-07-10T10:20:00Z"


def test_non_deferral_text_returns_none():
    r = deferral.parse_defer_phrase("Use approach A.", now=NOW)
    assert r.kind == "none"


def test_empty_text_returns_none():
    r = deferral.parse_defer_phrase("", now=NOW)
    assert r.kind == "none"


def test_never_guesses_a_default_for_unconfigured_vague_phrase():
    # Explicit regression for the "prefers asking over inventing a time"
    # requirement — deferred_until must be None when kind == "vague".
    r = deferral.parse_defer_phrase("later", now=NOW)
    assert r.deferred_until is None
