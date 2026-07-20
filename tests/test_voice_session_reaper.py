"""Milestone 9B.10: VoiceSessionReaper — periodic idle-session cleanup.

Never waits real time — run_reap_pass() is called directly with an
injected clock, the same pattern test_attention_scheduler.py uses for
AttentionScheduler.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.voice_session_manager import VoiceSessionManager
from app.voice_session_reaper import VoiceSessionReaper


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


class FakeSupervisor:
    async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None):
        return {"response": "OK", "conversation_id": conversation_id}


class FakeConnManager:
    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, data: dict):
        self.broadcasts.append(data)


def _backdate(voice_session_id: str, value: str) -> None:
    conn = db.get_conn()
    conn.execute("UPDATE voice_sessions SET updated_at=? WHERE voice_session_id=?", (value, voice_session_id))
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_run_reap_pass_closes_idle_sessions_and_broadcasts_the_closure():
    vsm = VoiceSessionManager(FakeSupervisor())
    cm = FakeConnManager()
    session = vsm.open_session("c1")
    _backdate(session["voice_session_id"], "2020-01-01T00:00:00Z")
    reaper = VoiceSessionReaper(vsm, cm, max_idle_seconds=900, clock=lambda: "2020-01-01T00:20:00Z")

    reaped = await reaper.run_reap_pass()

    assert len(reaped) == 1
    assert db.get_voice_session(session["voice_session_id"])["state"] == "closed"
    assert db.get_voice_session(session["voice_session_id"])["termination_reason"] == "idle_timeout"
    assert cm.broadcasts == [{
        "type": "voice_session_closed",
        "voice_session_id": session["voice_session_id"],
        "reason": "idle_timeout",
    }]


@pytest.mark.asyncio
async def test_run_reap_pass_leaves_fresh_sessions_untouched_and_broadcasts_nothing():
    vsm = VoiceSessionManager(FakeSupervisor())
    cm = FakeConnManager()
    session = vsm.open_session("c1")
    reaper = VoiceSessionReaper(vsm, cm, max_idle_seconds=900, clock=lambda: "2026-07-10T10:00:00Z")

    reaped = await reaper.run_reap_pass()

    assert reaped == []
    assert cm.broadcasts == []
    assert db.get_voice_session(session["voice_session_id"])["state"] == "listening"


@pytest.mark.asyncio
async def test_start_and_stop_do_not_raise():
    """The real asyncio-task loop, exercised end-to-end (not the sleep
    duration itself — start()/stop() must cleanly create and cancel the
    background task without error, same guarantee AttentionScheduler's
    equivalent already provides)."""
    vsm = VoiceSessionManager(FakeSupervisor())
    cm = FakeConnManager()
    reaper = VoiceSessionReaper(vsm, cm, tick_seconds=3600)
    await reaper.start()
    await reaper.stop()
