"""Milestone 8 Phase 10-12: VoiceSessionManager state machine.

VoiceSessionManager tracks session lifecycle/correlation only — it never
duplicates Supervisor reasoning (Phase 10 explicit constraint), so these
tests use a FakeSupervisor that just records calls and returns a canned
response, the same pattern tests/test_voice_fast_paths.py uses for
FakeTools.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app import attention_manager as am
import app.voice_session_manager as voice_session_manager_module
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

    async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None, confirm_before_tools=None):
        self.calls.append((user_message, conversation_id, bound_attention_request_id))
        return {"response": self.response, "conversation_id": conversation_id}


async def _attention_row(source_id="q1", conversation_id="c1"):
    class _FakeConnManager:
        _connections = []

        def has_user_surfaces(self) -> bool:
            return False

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


def test_transition_log_lines_carry_conversation_id(caplog):
    """M-OX.3 live-validation finding: every voice_session_manager state-
    transition log line carried conversation_id: null despite it being
    directly available on the already-fetched `session` row -- with two
    concurrent sessions, these lines were indistinguishable except by
    parsing the free-text `id=vs_...` substring."""
    import logging

    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    with caplog.at_level(logging.INFO, logger="app.voice_session_manager"):
        vsm.open_session("conv_transition_test")
    state_records = [r for r in caplog.records if "voice session state:" in r.message]
    assert len(state_records) >= 1
    assert all(r.conversation_id == "conv_transition_test" for r in state_records)


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
        async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None, confirm_before_tools=None):
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
        async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None, confirm_before_tools=None):
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


# ── termination_reason / idle reaper (Milestone 9B.10) ──────────────────

def _backdate(voice_session_id: str, column: str, value: str) -> None:
    """Test-only direct write — open_session()/handle_transcript() always
    stamp "now", so backdating updated_at/created_at to simulate an idle
    or long-running session requires going around VoiceSessionManager."""
    conn = db.get_conn()
    conn.execute(f"UPDATE voice_sessions SET {column}=? WHERE voice_session_id=?", (value, voice_session_id))
    conn.commit()
    conn.close()


def test_close_session_default_reason_is_client_requested():
    vsm = VoiceSessionManager(FakeSupervisor())
    session = vsm.open_session("c1")
    vsm.close_session(session["voice_session_id"])
    assert db.get_voice_session(session["voice_session_id"])["termination_reason"] == "client_requested"


def test_close_session_records_the_given_reason():
    vsm = VoiceSessionManager(FakeSupervisor())
    session = vsm.open_session("c1")
    vsm.close_session(session["voice_session_id"], reason="disconnect")
    assert db.get_voice_session(session["voice_session_id"])["termination_reason"] == "disconnect"


def test_close_session_twice_keeps_the_first_recorded_reason():
    """A duplicate close (e.g. a stray client retry) must not overwrite
    the true original cause with a different one."""
    vsm = VoiceSessionManager(FakeSupervisor())
    session = vsm.open_session("c1")
    vsm.close_session(session["voice_session_id"], reason="client_requested")
    vsm.close_session(session["voice_session_id"], reason="disconnect")  # no-op, already closed
    assert db.get_voice_session(session["voice_session_id"])["termination_reason"] == "client_requested"


def test_reap_idle_sessions_closes_only_sessions_past_the_idle_threshold():
    vsm = VoiceSessionManager(FakeSupervisor())
    stale = vsm.open_session("c1")
    fresh = vsm.open_session("c2")
    _backdate(stale["voice_session_id"], "updated_at", "2020-01-01T00:00:00Z")

    reaped = vsm.reap_idle_sessions(max_idle_seconds=900, now="2020-01-01T00:20:00Z")

    assert [s["voice_session_id"] for s in reaped] == [stale["voice_session_id"]]
    assert db.get_voice_session(stale["voice_session_id"])["state"] == "closed"
    assert db.get_voice_session(stale["voice_session_id"])["termination_reason"] == "idle_timeout"
    assert db.get_voice_session(fresh["voice_session_id"])["state"] == "listening"


@pytest.mark.asyncio
async def test_an_active_conversation_never_accrues_idle_time_regardless_of_total_duration():
    """The core correctness requirement: a session that keeps having real
    turns must never be reaped, no matter how long it's been open overall
    — only genuine silence (no transition of any kind) counts as idle."""
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsid = session["voice_session_id"]

    # Backdate created_at (but NOT updated_at) far in the past, simulating
    # a session that's been open a long time but is still actively used.
    _backdate(vsid, "created_at", "2020-01-01T00:00:00Z")
    await vsm.handle_transcript(vsid, "still here")  # refreshes updated_at to "now"

    reaped = vsm.reap_idle_sessions(max_idle_seconds=900)
    assert reaped == []
    assert db.get_voice_session(vsid)["state"] == "listening"


@pytest.mark.asyncio
async def test_reap_idle_sessions_releases_the_lease_of_a_bound_session():
    vsm = VoiceSessionManager(FakeSupervisor())

    row = await _attention_row()
    aid = row["attention_request_id"]
    session = vsm.open_session("c1", aid)
    _backdate(session["voice_session_id"], "updated_at", "2020-01-01T00:00:00Z")

    vsm.reap_idle_sessions(max_idle_seconds=900, now="2020-01-01T00:20:00Z")

    assert db.get_attention_request(aid)["active_voice_session_id"] is None


@pytest.mark.asyncio
async def test_handle_transcript_raises_if_session_closed_concurrently_during_process_message():
    """Milestone 9B.10 (independent review finding): process_message()
    awaits, and something else (the idle-timeout reaper, another device's
    explicit close, the LLM's own close_voice_session tool) could close
    this exact session while the turn is in flight. Without the fix,
    handle_transcript() would return voice_session_state: "listening" for
    a session that is actually closed — reproduced here deterministically
    with a fake supervisor that closes the session mid-call, standing in
    for the real race's timing."""
    vsm = VoiceSessionManager(FakeSupervisor())
    session = vsm.open_session("c1")
    voice_session_id = session["voice_session_id"]

    class ClosingSupervisor:
        async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None, confirm_before_tools=None):
            vsm.close_session(voice_session_id, reason="idle_timeout")
            return {"response": "too late", "conversation_id": conversation_id}

    vsm._supervisor = ClosingSupervisor()

    with pytest.raises(VoiceSessionError):
        await vsm.handle_transcript(session["voice_session_id"], "hello")

    assert db.get_voice_session(session["voice_session_id"])["state"] == "closed"
    assert db.get_voice_session(session["voice_session_id"])["termination_reason"] == "idle_timeout"


@pytest.mark.asyncio
async def test_handle_transcript_after_close_raises():
    sv = FakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsm.close_session(session["voice_session_id"])
    with pytest.raises(VoiceSessionError):
        await vsm.handle_transcript(session["voice_session_id"], "hello")


# ── TD-002 / ADR-007 ownership lease guard (Milestone 9B.4) ────────────

@pytest.mark.asyncio
async def test_second_client_cannot_bind_a_session_to_an_already_leased_attention_request():
    """The core TD-002 scenario: the PWA and the Android companion each
    call open_session() against the same bound AttentionRequest. The first
    must succeed and claim the lease; the second must be rejected outright
    rather than silently opening a second, uncoordinated session."""
    row = await _attention_row()
    aid = row["attention_request_id"]
    vsm = VoiceSessionManager(FakeSupervisor())

    first = vsm.open_session("c1", aid)
    assert first["attention_request_id"] == aid
    assert db.get_attention_request(aid)["active_voice_session_id"] == first["voice_session_id"]

    with pytest.raises(VoiceSessionError):
        vsm.open_session("c2", aid)

    # The rejected attempt must not have created a second VoiceSession row
    # bound to the same AttentionRequest, and must not have disturbed the
    # first session's lease.
    assert db.get_attention_request(aid)["active_voice_session_id"] == first["voice_session_id"]


@pytest.mark.asyncio
async def test_closing_a_bound_session_releases_the_lease_for_a_new_one():
    row = await _attention_row()
    aid = row["attention_request_id"]
    vsm = VoiceSessionManager(FakeSupervisor())

    first = vsm.open_session("c1", aid)
    vsm.close_session(first["voice_session_id"])
    assert db.get_attention_request(aid)["active_voice_session_id"] is None

    second = vsm.open_session("c2", aid)  # must not raise now that the lease is free
    assert second["attention_request_id"] == aid
    assert db.get_attention_request(aid)["active_voice_session_id"] == second["voice_session_id"]


@pytest.mark.asyncio
async def test_failing_a_bound_session_releases_the_lease():
    row = await _attention_row()
    aid = row["attention_request_id"]
    vsm = VoiceSessionManager(FakeSupervisor())

    first = vsm.open_session("c1", aid)
    assert vsm.fail_session(first["voice_session_id"])
    assert db.get_attention_request(aid)["active_voice_session_id"] is None

    second = vsm.open_session("c2", aid)  # must not raise
    assert second["attention_request_id"] == aid


@pytest.mark.asyncio
async def test_releasing_a_stale_session_never_clears_a_newer_lease():
    """Defense-in-depth: a delayed close() call for a session that no
    longer holds the (already-reassigned) lease must not clear whichever
    session currently does hold it."""
    row = await _attention_row()
    aid = row["attention_request_id"]
    vsm = VoiceSessionManager(FakeSupervisor())

    first = vsm.open_session("c1", aid)
    vsm.close_session(first["voice_session_id"])
    second = vsm.open_session("c2", aid)

    # A late/duplicate release call for the already-closed first session
    # (calling the DB function directly, since close_session() itself is
    # idempotent and already released its own lease correctly above).
    db.release_voice_session_lease(aid, first["voice_session_id"])
    assert db.get_attention_request(aid)["active_voice_session_id"] == second["voice_session_id"]


def test_unbound_sessions_never_touch_any_lease():
    vsm = VoiceSessionManager(FakeSupervisor())
    a = vsm.open_session("c1")
    b = vsm.open_session("c2")
    assert a["attention_request_id"] is None
    assert b["attention_request_id"] is None  # no lease contention for unbound sessions


# ── Control Center hardening: set_broadcast_hook / _broadcast_lifecycle ─
#
# Self-contained: each test explicitly sets its own hook rather than
# relying on app.main's module-level import-time wiring (see
# tests/conftest.py's autouse fixture, which resets this hook to None
# around every test for the same reason).

class _FakeObserverConnManager:
    def __init__(self, observers=True):
        self._observers = observers
        self.broadcast_calls = []

    def has_observers(self):
        return self._observers

    async def broadcast_observers(self, data):
        self.broadcast_calls.append(data)


@pytest.mark.asyncio
async def test_broadcast_lifecycle_is_noop_with_no_hook():
    voice_session_manager_module._broadcast_hook = None
    events_before = len(db.get_recent_events(50))

    await voice_session_manager_module._broadcast_lifecycle("opened", {"voice_session_id": "vs1"})

    assert len(db.get_recent_events(50)) == events_before


@pytest.mark.asyncio
async def test_broadcast_lifecycle_is_noop_when_hook_set_but_no_observers():
    """Phase 4 (performance): zero observers means zero extra
    db.save_event writes on the voice-turn hot path, not just zero
    broadcast() sends."""
    fake = _FakeObserverConnManager(observers=False)
    voice_session_manager_module.set_broadcast_hook(fake)
    events_before = len(db.get_recent_events(50))

    await voice_session_manager_module._broadcast_lifecycle("transcript_received", {"voice_session_id": "vs1"})

    assert len(db.get_recent_events(50)) == events_before
    assert fake.broadcast_calls == []


@pytest.mark.asyncio
async def test_broadcast_lifecycle_persists_and_forwards_when_observers_present():
    fake = _FakeObserverConnManager(observers=True)
    voice_session_manager_module.set_broadcast_hook(fake)
    events_before = len(db.get_recent_events(50))

    await voice_session_manager_module._broadcast_lifecycle("turn_completed", {"voice_session_id": "vs1"}, response="hi")

    assert len(db.get_recent_events(50)) == events_before + 1
    assert len(fake.broadcast_calls) == 1
    assert fake.broadcast_calls[0]["type"] == "voice_session_lifecycle"


def test_schedule_lifecycle_skips_when_no_observers_even_with_running_loop():
    """_schedule_lifecycle is the fire-and-forget variant open_session/
    close_session call synchronously -- it must not even schedule the
    task when nobody is observing, not just no-op inside the task."""
    import asyncio

    fake = _FakeObserverConnManager(observers=False)
    voice_session_manager_module.set_broadcast_hook(fake)

    async def run():
        tasks_before = len(asyncio.all_tasks())
        voice_session_manager_module._schedule_lifecycle("opened", {"voice_session_id": "vs1"})
        # No task should have been created at all.
        assert len(asyncio.all_tasks()) == tasks_before

    asyncio.run(run())


def test_schedule_lifecycle_is_safe_with_no_running_loop():
    """Synchronous callers (e.g. a plain unit test with no event loop)
    must not raise -- this is the existing, unchanged guarantee; confirm
    it still holds now that has_observers() is checked first."""
    fake = _FakeObserverConnManager(observers=True)
    voice_session_manager_module.set_broadcast_hook(fake)
    voice_session_manager_module._schedule_lifecycle("opened", {"voice_session_id": "vs1"})  # must not raise


# ── Interaction Layer v1 (Goal 5): technical command confirmation ──────

_PENDING = {"name": "start_opencode_task", "args": {"project_alias": "jarvis-test", "instruction": "create a file"}}


class ConfirmingSupervisor:
    """Stands in for the real Supervisor's Goal 5 behavior: a turn that
    proposes a confirmation-gated tool returns pending_tool_call instead
    of executing it; resolve_pending_tool_confirmation resolves the
    following yes/no reply. VoiceSessionManager never duplicates this
    reasoning (same Phase 10 constraint as FakeSupervisor above) — it only
    needs to know that a pending_tool_call arrived and route accordingly."""

    def __init__(self):
        self.process_message_calls = []
        self.resolve_calls = []

    async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None, confirm_before_tools=None):
        self.process_message_calls.append((user_message, conversation_id, bound_attention_request_id, confirm_before_tools))
        return {
            "response": "I heard: create a file — for the jarvis-test project. Should I go ahead?",
            "conversation_id": conversation_id,
            "pending_tool_call": dict(_PENDING),
        }

    async def resolve_pending_tool_confirmation(self, conversation_id, transcript, pending_tool_call):
        self.resolve_calls.append((conversation_id, transcript, pending_tool_call))
        decision = transcript.strip().lower()
        if decision == "yes":
            return {"response": "Okay, I've started that.", "conversation_id": conversation_id}
        if decision == "no":
            return {"response": "Okay, I won't do that. What would you like instead?", "conversation_id": conversation_id}
        return {
            "response": "Sorry, was that a yes or a no?",
            "conversation_id": conversation_id,
            "pending_tool_call": pending_tool_call,
        }


@pytest.mark.asyncio
async def test_pending_tool_call_transitions_to_confirming_not_listening():
    sv = ConfirmingSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsid = session["voice_session_id"]

    result = await vsm.handle_transcript(vsid, "start an opencode task to create a file")

    assert result["voice_session_state"] == "confirming"
    assert "Should I go ahead" in result["response"]
    row = db.get_voice_session(vsid)
    assert row["state"] == "confirming"
    assert json.loads(row["pending_tool_call"]) == _PENDING
    # process_message() was passed the voice-only confirmation set — this
    # is what makes Goal 5 voice-specific rather than affecting text/
    # browser chat, which never sets this.
    assert sv.process_message_calls[0][3] is not None


@pytest.mark.asyncio
async def test_yes_reply_resolves_confirmation_and_returns_to_listening():
    sv = ConfirmingSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsid = session["voice_session_id"]
    await vsm.handle_transcript(vsid, "start an opencode task to create a file")

    result = await vsm.handle_transcript(vsid, "yes")

    assert result["voice_session_state"] == "listening"
    assert result["response"] == "Okay, I've started that."
    assert sv.resolve_calls == [("c1", "yes", _PENDING)]
    row = db.get_voice_session(vsid)
    assert row["state"] == "listening"
    assert row["pending_tool_call"] is None  # cleared once resolved


@pytest.mark.asyncio
async def test_no_reply_cancels_confirmation_and_returns_to_listening():
    sv = ConfirmingSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsid = session["voice_session_id"]
    await vsm.handle_transcript(vsid, "start an opencode task to create a file")

    result = await vsm.handle_transcript(vsid, "no")

    assert result["voice_session_state"] == "listening"
    assert "won't do that" in result["response"]
    row = db.get_voice_session(vsid)
    assert row["state"] == "listening"
    assert row["pending_tool_call"] is None


@pytest.mark.asyncio
async def test_unclear_reply_stays_in_confirming_and_reasks():
    """No-guessing-under-ambiguity (same philosophy as
    _resolve_deterministic_command in supervisor.py) — an unrecognized
    reply must re-ask, never silently execute or silently cancel."""
    sv = ConfirmingSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsid = session["voice_session_id"]
    await vsm.handle_transcript(vsid, "start an opencode task to create a file")

    result = await vsm.handle_transcript(vsid, "maybe")

    assert result["voice_session_state"] == "confirming"
    assert "yes or a no" in result["response"]
    row = db.get_voice_session(vsid)
    assert row["state"] == "confirming"
    assert json.loads(row["pending_tool_call"]) == _PENDING  # still pending, not lost


@pytest.mark.asyncio
async def test_second_yes_after_unclear_reply_still_resolves_correctly():
    sv = ConfirmingSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("c1")
    vsid = session["voice_session_id"]
    await vsm.handle_transcript(vsid, "start an opencode task to create a file")
    await vsm.handle_transcript(vsid, "maybe")

    result = await vsm.handle_transcript(vsid, "yes")

    assert result["voice_session_state"] == "listening"
    assert result["response"] == "Okay, I've started that."
