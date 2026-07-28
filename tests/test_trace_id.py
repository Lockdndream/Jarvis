"""ADR-020 (Owner Experience M-OX.1): trace_id correlation model.

Covers the four claims the ADR makes: (1) trace_id is minted once per
turn and isolated across concurrently running turns via a ContextVar,
(2) it is persisted on conversations/tasks/opencode_tasks/events, (3) the
hard case -- an OpenCode task started mid-turn, whose real completion
arrives asynchronously -- correctly carries the turn's trace_id on its
row at creation, and (4) it round-trips through VoiceSessionManager into
the caller-visible response.
"""
import asyncio
import json
import os
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app import trace
from app.connection_manager import ConnectionManager
from app.integrations.opencode_supervisor import OpenCodeSupervisor
from app.supervisor.supervisor import Supervisor
from app.supervisor.llm import FakeLLMProvider
from app.supervisor.projects import set_default_projects
import app.voice_session_manager as voice_session_manager_module
from app.voice_session_manager import VoiceSessionManager


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
def test_projects(monkeypatch):
    tmp = tempfile.mktemp(suffix=".json")
    monkeypatch.setattr("app.supervisor.projects.PROJECTS_FILE", tmp)
    set_default_projects({"jarvis": {"path": "d:\\projects\\jarvis", "display_name": "Jarvis"}})
    yield
    set_default_projects({})
    if os.path.exists(tmp):
        os.unlink(tmp)


# ── app/trace.py: minting, binding, isolation ────────────────────────

def test_new_trace_id_format_and_uniqueness():
    a = trace.new_trace_id()
    b = trace.new_trace_id()
    assert a.startswith("trace_")
    assert len(a) == len("trace_") + 12
    assert a != b


def test_current_trace_id_defaults_to_none():
    assert trace.current_trace_id() is None


def test_bind_and_reset_round_trip():
    token = trace.bind_trace_id("trace_abc123def456")
    assert trace.current_trace_id() == "trace_abc123def456"
    trace.reset_trace_id(token)
    assert trace.current_trace_id() is None


@pytest.mark.asyncio
async def test_trace_id_isolated_across_concurrent_tasks():
    """The concurrency claim ADR-020 makes explicitly: two turns in
    flight at once (e.g. phone + PWA) must never see each other's
    trace_id, which a bare module-global could not guarantee."""
    async def bind_wait_and_read(tid, delay):
        token = trace.bind_trace_id(tid)
        await asyncio.sleep(delay)
        seen = trace.current_trace_id()
        trace.reset_trace_id(token)
        return seen

    r1, r2 = await asyncio.gather(
        bind_wait_and_read("trace_first00000", 0.05),
        bind_wait_and_read("trace_second0000", 0.0),
    )
    assert r1 == "trace_first00000"
    assert r2 == "trace_second0000"
    assert trace.current_trace_id() is None


# ── app/database.py: persistence, retrieval, retention ───────────────

def test_save_event_persists_trace_id():
    db.save_event("some_event", "content", trace_id="trace_evt1")
    rows = db.get_events_by_trace_id("trace_evt1")
    assert len(rows) == 1
    assert rows[0]["type"] == "some_event"


def test_save_event_without_trace_id_is_still_backward_compatible():
    db.save_event("legacy_call")  # no trace_id, matches every pre-existing call site
    events = db.get_recent_events(10)
    assert events[-1]["type"] == "legacy_call"


def test_get_events_by_trace_id_filters_correctly():
    db.save_event("a", "1", trace_id="trace_x")
    db.save_event("b", "2", trace_id="trace_y")
    db.save_event("c", "3", trace_id="trace_x")
    rows = db.get_events_by_trace_id("trace_x")
    assert [r["type"] for r in rows] == ["a", "c"]


def test_create_task_record_persists_trace_id():
    db.create_task_record("t1", "Task", "cmd", trace_id="trace_task1")
    task = db.get_task("t1")
    assert task["trace_id"] == "trace_task1"


def test_create_opencode_task_record_persists_trace_id():
    db.create_task_record("oc1", "OC Task", "instr", trace_id="trace_oc1")
    db.create_opencode_task_record("oc1", "ses_1", "/tmp/proj", "instr", trace_id="trace_oc1")
    oc_task = db.get_opencode_task("oc1")
    assert oc_task["trace_id"] == "trace_oc1"


def test_save_conversation_message_persists_trace_id():
    db.save_conversation_message("conv1", "user", "hello", trace_id="trace_conv1")
    messages = db.get_conversation_messages("conv1")
    assert messages[0]["trace_id"] == "trace_conv1"


def test_purge_events_older_than_deletes_only_old_rows():
    db.save_event("old_event", "x")
    conn = db.get_conn()
    conn.execute("UPDATE events SET timestamp=? WHERE type='old_event'", ("2020-01-01T00:00:00Z",))
    conn.commit()
    conn.close()
    db.save_event("recent_event", "y")

    deleted = db.purge_events_older_than(30, now="2026-07-22T00:00:00Z")

    assert deleted == 1
    remaining = {e["type"] for e in db.get_recent_events(50)}
    assert "old_event" not in remaining
    assert "recent_event" in remaining


def test_purge_events_older_than_returns_zero_when_nothing_qualifies():
    db.save_event("recent_event", "y")
    deleted = db.purge_events_older_than(30, now="2026-07-22T00:00:00Z")
    assert deleted == 0


def test_ensure_column_migration_adds_trace_id_to_a_pre_existing_db():
    """Every other test in this file calls init_db() on a fresh DB, which
    exercises the CREATE TABLE path -- trace_id is present from the start.
    A real production jarvis.db predates this migration, so it must take
    the _ensure_column path instead (the same one that added
    termination_reason/last_evidence_type). Build the OLD table shape by
    hand, with real pre-migration data in it, then confirm init_db() adds
    the column without raising or losing that data."""
    # The autouse test_db fixture already ran init_db() (post-migration
    # shape) against its own temp file -- this test needs a genuinely
    # separate, pre-migration-shape DB, not that one.
    old_path = db.DB_PATH
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = path
    try:
        conn = db.get_conn()
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, "
            "timestamp TEXT NOT NULL, content TEXT)"
        )
        conn.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT UNIQUE NOT NULL, "
            "name TEXT NOT NULL, command TEXT, status TEXT NOT NULL DEFAULT 'running', "
            "started_at TEXT NOT NULL, completed_at TEXT, exit_code INTEGER)"
        )
        conn.execute(
            "CREATE TABLE opencode_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT UNIQUE NOT NULL, "
            "session_id TEXT NOT NULL, project_dir TEXT NOT NULL, instruction TEXT, "
            "status TEXT NOT NULL DEFAULT 'running', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, "
            "role TEXT NOT NULL, content TEXT NOT NULL, metadata_json TEXT, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO events (type, timestamp, content) VALUES ('pre_migration_event', '2020-01-01T00:00:00Z', 'x')"
        )
        conn.execute(
            "INSERT INTO tasks (task_id, name, command, started_at) VALUES ('pre1', 'Pre Task', 'cmd', '2020-01-01T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        db.init_db()  # must not raise, must add trace_id without dropping existing rows

        events = db.get_recent_events(10)
        assert any(e["type"] == "pre_migration_event" for e in events)
        task = db.get_task("pre1")
        assert task is not None
        assert task["trace_id"] is None

        # And the migrated column is genuinely usable afterward, not just present.
        db.create_task_record("post1", "Post Task", "cmd", trace_id="trace_post1")
        assert db.get_task("post1")["trace_id"] == "trace_post1"
    finally:
        db.DB_PATH = old_path
        if os.path.exists(path):
            os.unlink(path)


# ── Supervisor.process_message(): minting, propagation ───────────────

@pytest.mark.asyncio
async def test_process_message_returns_a_trace_id():
    sv = Supervisor()
    result = await sv.process_message("what needs my attention")
    assert result.get("trace_id", "").startswith("trace_")


@pytest.mark.asyncio
async def test_two_sequential_turns_get_different_trace_ids():
    sv = Supervisor()
    r1 = await sv.process_message("what needs my attention")
    r2 = await sv.process_message("what needs my attention")
    assert r1["trace_id"] != r2["trace_id"]


@pytest.mark.asyncio
async def test_fast_path_turn_persists_trace_id_on_conversation_messages():
    sv = Supervisor()
    result = await sv.process_message("what needs my attention", "conv_fast1")
    messages = db.get_conversation_messages("conv_fast1")
    assert len(messages) == 2  # user + assistant
    assert all(m["trace_id"] == result["trace_id"] for m in messages)


@pytest.mark.asyncio
async def test_full_llm_loop_turn_persists_trace_id_on_conversation_and_tool_messages():
    sv = Supervisor()
    sv._llm = FakeLLMProvider(responses=[{"role": "assistant", "content": "Done."}])
    result = await sv.process_message("tell me something", "conv_llm1")
    messages = db.get_conversation_messages("conv_llm1")
    assert len(messages) == 2
    assert all(m["trace_id"] == result["trace_id"] for m in messages)


@pytest.mark.asyncio
async def test_concurrent_turns_do_not_cross_contaminate_trace_ids():
    sv = Supervisor()
    r1, r2 = await asyncio.gather(
        sv.process_message("what needs my attention", "conv_a"),
        sv.process_message("what tasks are running", "conv_b"),
    )
    assert r1["trace_id"] != r2["trace_id"]
    msgs_a = db.get_conversation_messages("conv_a")
    msgs_b = db.get_conversation_messages("conv_b")
    assert all(m["trace_id"] == r1["trace_id"] for m in msgs_a)
    assert all(m["trace_id"] == r2["trace_id"] for m in msgs_b)


# ── The hard case: OpenCode's async leg ──────────────────────────────
#
# Real OpenCodeSupervisor.start_session(), not a test double -- this is
# the exact mechanism ADR-020 exists to get right: trace_id must be on
# the row *before* the turn returns, because OpenCode's own completion
# (a separate asyncio Task entirely, the SSE loop) arrives later and has
# no ContextVar binding of its own to read it from.

class _FakeOCAdapter:
    def __init__(self):
        self.sent = []

    async def create_session(self, project_dir):
        return f"ses_{uuid.uuid4().hex[:8]}"

    async def send_prompt(self, session_id, project_dir, instruction, provider_id=None, model_id=None):
        self.sent.append((session_id, project_dir, instruction))


def _make_real_opencode_supervisor():
    sv = OpenCodeSupervisor.__new__(OpenCodeSupervisor)
    sv.cm = ConnectionManager()
    sv.adapter = _FakeOCAdapter()
    sv._completion_events = {}
    sv._error_since_prompt = {}
    return sv


@pytest.mark.asyncio
async def test_start_opencode_task_persists_the_turns_trace_id_on_both_rows():
    real_oc = _make_real_opencode_supervisor()
    sv = Supervisor(opencode_supervisor=real_oc)
    sv._llm = FakeLLMProvider(responses=[
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "start_opencode_task",
                    "arguments": json.dumps({"project_alias": "jarvis", "instruction": "investigate the bug"}),
                },
            }],
        },
        {"role": "assistant", "content": "Started investigating."},
    ])

    result = await sv.process_message("please investigate the bug", "conv_oc1")

    running = db.get_opencode_running_tasks()
    assert len(running) == 1
    task_id = running[0]["task_id"]

    task_row = db.get_task(task_id)
    oc_row = db.get_opencode_task(task_id)
    assert task_row["trace_id"] == result["trace_id"]
    assert oc_row["trace_id"] == result["trace_id"]


@pytest.mark.asyncio
async def test_start_opencode_task_trace_id_survives_for_the_later_sse_handler_to_read():
    """The point of persisting it at creation: a later SSE event, handled
    on a completely different asyncio Task with no ContextVar binding at
    all, can still recover the trace_id by reading the row -- exactly
    what _handle_activity/_handle_session_idle/_handle_session_failed
    will do in a future OX milestone."""
    real_oc = _make_real_opencode_supervisor()
    sv = Supervisor(opencode_supervisor=real_oc)
    sv._llm = FakeLLMProvider(responses=[
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "start_opencode_task",
                    "arguments": json.dumps({"project_alias": "jarvis", "instruction": "do the thing"}),
                },
            }],
        },
        {"role": "assistant", "content": "Started."},
    ])
    result = await sv.process_message("do the thing", "conv_oc2")
    task_id = db.get_opencode_running_tasks()[0]["task_id"]

    # Simulate the turn having long since returned and the ContextVar
    # binding having been reset -- a fresh, unrelated asyncio Task reading
    # the row later must still see the right trace_id.
    async def later_sse_handler():
        assert trace.current_trace_id() is None  # no ambient binding here
        return db.get_opencode_task(task_id)["trace_id"]

    recovered = await asyncio.create_task(later_sse_handler())
    assert recovered == result["trace_id"]


# ── VoiceSessionManager: propagation into the caller-visible response ─

class _RealisticFakeSupervisor:
    """Mimics the real Supervisor's contract of always returning a
    trace_id -- unlike test_voice_session_manager.py's FakeSupervisor,
    which predates ADR-020 and is intentionally left unchanged there."""

    async def process_message(self, user_message, conversation_id=None, bound_attention_request_id=None, confirm_before_tools=None):
        return {"response": "OK", "conversation_id": conversation_id, "trace_id": trace.new_trace_id()}


class _FakeObserverConnManager:
    def __init__(self):
        self.broadcast_calls = []

    def has_observers(self):
        return True

    async def broadcast_observers(self, data):
        self.broadcast_calls.append(data)


@pytest.mark.asyncio
async def test_handle_transcript_propagates_trace_id_into_returned_dict():
    sv = _RealisticFakeSupervisor()
    vsm = VoiceSessionManager(sv)
    session = vsm.open_session("conv_vs1")

    result = await vsm.handle_transcript(session["voice_session_id"], "hello")

    assert result["trace_id"] is not None
    assert result["trace_id"].startswith("trace_")


@pytest.mark.asyncio
async def test_handle_transcript_tags_turn_completed_event_with_the_same_trace_id():
    fake_cm = _FakeObserverConnManager()
    voice_session_manager_module.set_broadcast_hook(fake_cm)
    try:
        sv = _RealisticFakeSupervisor()
        vsm = VoiceSessionManager(sv)
        session = vsm.open_session("conv_vs2")

        result = await vsm.handle_transcript(session["voice_session_id"], "hello")

        turn_completed = [
            json.loads(c["content"]) for c in fake_cm.broadcast_calls
            if json.loads(c["content"]).get("phase") == "turn_completed"
        ]
        assert len(turn_completed) == 1
        assert turn_completed[0]["trace_id"] == result["trace_id"]
    finally:
        voice_session_manager_module.set_broadcast_hook(None)
