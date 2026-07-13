"""Milestone 8 Phase 10-12: VoiceSessionManager state machine.

VoiceSessionManager tracks session lifecycle/correlation only — it never
duplicates Supervisor reasoning (Phase 10 explicit constraint), so these
tests use a FakeSupervisor that just records calls and returns a canned
response, the same pattern tests/test_voice_fast_paths.py uses for
FakeTools.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app import attention_manager as am
from app.voice_session_manager import VoiceSessionManager, VoiceSessionError


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
def reset_broadcast_hook():
    am.set_broadcast_hook(None)
    yield
    am.set_broadcast_hook(None)


class FakeSupervisor:
    def __init__(self, response="OK"):
        self.calls = []
        self.response = response

    async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None):
        self.calls.append((user_message, conversation_id, bound_attention_request_id))
        return {"response": self.response, "conversation_id": conversation_id}


async def _attention_row(source_id="q1", conversation_id="c1"):
    class _FakeConnManager:
        _connections = []

        async def broadcast(self, msg):
            pass

    return await am.get_or_create(
        _FakeConnManager(), conversation_id=conversation_id, task_id="t1",
        source_type="local_question", source_id=source_id,
        attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )


# ── Open / basic transitions ────────────────────────────────────────

def test_open_session_unbound_goes_straight_to_listening():
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    assert session["state"] == "listening"
    assert session["attention_request_id"] is None
    assert session["greeting"] is None


@pytest.mark.asyncio
async def test_open_session_bound_includes_a_greeting_with_the_real_question_text():
    """Milestone 8.1 real-phone finding: a bound voice session must
    proactively state context (the real question/options), not silently
    go straight to listening — otherwise a re-contacted user has no way
    to know what they're being asked about."""
    db.create_task_record("t1", "Task", "do it")
    db.create_question_record("q1", "t1", "Which approach should I use?", "", '["A", "B"]')
    row = await _attention_row()
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1", row["attention_request_id"])
    assert "Which approach should I use?" in session["greeting"]
    assert "A" in session["greeting"] and "B" in session["greeting"]


@pytest.mark.asyncio
async def test_bound_greeting_falls_back_to_summary_without_a_question_record():
    row = await _attention_row()
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1", row["attention_request_id"])
    assert session["greeting"] == "s"  # the summary passed to _attention_row()


@pytest.mark.asyncio
async def test_recontact_greeting_is_prefixed_as_a_return_visit():
    db.create_task_record("t1", "Task", "do it")
    db.create_question_record("q1", "t1", "Which approach should I use?", "", '["A", "B"]')
    row = await _attention_row()
    db.record_attention_contact(row["attention_request_id"])  # bumps contact_attempt_count to 2
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1", row["attention_request_id"])
    assert session["greeting"].startswith("You asked me to come back.")


@pytest.mark.asyncio
async def test_open_session_bound_to_valid_attention_request():
    row = await _attention_row()
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1", row["attention_request_id"])
    assert session["attention_request_id"] == row["attention_request_id"]


@pytest.mark.asyncio
async def test_open_session_with_stale_attention_request_opens_unbound():
    row = await _attention_row()
    await am.resolve_for_source("local_question", "q1", "answered", "x")
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1", row["attention_request_id"])
    assert session["attention_request_id"] is None  # never binds to a resolved source


def test_open_session_with_unknown_attention_request_opens_unbound():
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1", "attn_does_not_exist")
    assert session["attention_request_id"] is None


# ── Transcript handling / barge-in guard ────────────────────────────

@pytest.mark.asyncio
async def test_handle_transcript_routes_to_supervisor_and_returns_to_listening():
    """Milestone 9A real-phone finding (2026-07-10): a completed turn must
    leave the session ready for another one (STATE_LISTENING), not stuck
    in STATE_WAITING — see test_second_turn_is_accepted_not_rejected below
    for the regression this was actually caught by."""
    sv = FakeSupervisor(response="Got it.")
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    result = await vsm.handle_transcript(session["voice_session_id"], "hello")
    assert result["response"] == "Got it."
    assert sv.calls == [("hello", "c1", None)]
    assert result["voice_session_state"] == "listening"
    assert db.get_voice_session(session["voice_session_id"])["state"] == "listening"


@pytest.mark.asyncio
async def test_second_turn_is_accepted_not_rejected():
    """Milestone 9A real-phone finding (2026-07-10): every second turn in
    a voice session used to be rejected with a misleading "already
    processing a turn" error, because nothing ever transitioned the
    session from WAITING back to LISTENING after the first turn completed
    — even though the legal-transition map always allowed it. Found on a
    real S20 FE when a mis-heard "option" (missing the "a") triggered a
    clarification, and the follow-up "option a" was silently dropped."""
    sv = FakeSupervisor(response="Which option?")
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsid = session["voice_session_id"]

    first = await vsm.handle_transcript(vsid, "option")
    assert first["response"] == "Which option?"

    # This must NOT raise "already processing a turn" — the session must
    # have returned to a state that accepts a new transcript.
    second = await vsm.handle_transcript(vsid, "option a")
    assert second["response"] == "Which option?"  # FakeSupervisor always returns the same canned text
    assert sv.calls == [("option", "c1", None), ("option a", "c1", None)]


@pytest.mark.asyncio
async def test_handle_transcript_on_unknown_session_raises():
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    with pytest.raises(VoiceSessionError):
        await vsm.handle_transcript("vs_does_not_exist", "hello")


@pytest.mark.asyncio
async def test_bound_transcript_passes_bound_attention_request_id_through():
    row = await _attention_row()
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1", row["attention_request_id"])
    await vsm.handle_transcript(session["voice_session_id"], "answer A")
    assert sv.calls == [("answer A", "c1", row["attention_request_id"])]


@pytest.mark.asyncio
async def test_bound_session_reports_stale_source_instead_of_acting():
    row = await _attention_row()
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1", row["attention_request_id"])
    # The bound request resolves elsewhere mid-session (e.g. answered via
    # the in-app UI) before the user speaks their next turn.
    await am.resolve_for_source("local_question", "q1", "answered", "x")
    result = await vsm.handle_transcript(session["voice_session_id"], "answer A")
    assert "resolved" in result["response"].lower()
    assert sv.calls == []  # never routed to the supervisor at all
    assert result["attention_request_id"] is None


def _sync_barge_in_setup():
    pass


@pytest.mark.asyncio
async def test_concurrent_transcripts_on_same_session_barge_in_guard():
    import asyncio

    class SlowSupervisor:
        async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None):
            await asyncio.sleep(0.05)
            return {"response": "done", "conversation_id": conversation_id}

    vsm = VoiceSessionManager(SlowSupervisor())
    session = vsm.open_session("c1")
    vsid = session["voice_session_id"]

    results = await asyncio.gather(
        vsm.handle_transcript(vsid, "first"),
        vsm.handle_transcript(vsid, "second"),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, VoiceSessionError)]
    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(errors) == 1  # exactly one transcript rejected as "already processing"
    assert len(successes) == 1


# ── Bound defer -> DEFERRED state (Phase 10) ────────────────────────

@pytest.mark.asyncio
async def test_bound_defer_transitions_voice_session_to_deferred_not_waiting():
    row = await _attention_row()
    aid = row["attention_request_id"]

    class DeferringSupervisor:
        async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None):
            await am.defer(bound_attention_request_id, "2026-07-10T10:15:00Z")
            return {"response": "Okay.", "conversation_id": conversation_id}

    vsm = VoiceSessionManager(DeferringSupervisor())
    session = vsm.open_session("c1", aid)
    result = await vsm.handle_transcript(session["voice_session_id"], "come back in 15 minutes")
    assert result["voice_session_state"] == "deferred"
    assert db.get_voice_session(session["voice_session_id"])["state"] == "deferred"


# ── Close / fail ─────────────────────────────────────────────────────

def test_close_session_is_idempotent():
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    assert vsm.close_session(session["voice_session_id"])
    assert db.get_voice_session(session["voice_session_id"])["state"] == "closed"
    assert vsm.close_session(session["voice_session_id"])  # closing twice is a safe no-op


def test_close_unknown_session_returns_false():
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    assert not vsm.close_session("vs_does_not_exist")


@pytest.mark.asyncio
async def test_handle_transcript_after_close_raises():
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsm.close_session(session["voice_session_id"])
    with pytest.raises(VoiceSessionError):
        await vsm.handle_transcript(session["voice_session_id"], "hello")
