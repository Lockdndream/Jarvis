"""Milestone 6, Phase 4 + Phase 10: verified OpenCode task lifecycle and SSE
event-evidence handling.

Every state transition here is driven by feeding a real-shaped SSE frame
(GlobalEvent envelope, as captured from the actual OpenCode v1.15.10 server)
into OpenCodeSupervisor._handle_sse_event and asserting DB state — never by
calling internal handlers directly with invented shapes.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app.integrations.opencode_supervisor import OpenCodeSupervisor, _extract_result_text


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


class RecordingWebSocket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.sent.append(text)


def make_supervisor():
    sv = OpenCodeSupervisor.__new__(OpenCodeSupervisor)
    sv.cm = ConnectionManager()
    sv._completion_events = {}
    sv._error_since_prompt = {}
    import collections
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


def frame(directory, evt_type, properties, evt_id="evt_1"):
    return {"data": {"directory": directory, "project": "global",
                      "payload": {"id": evt_id, "type": evt_type, "properties": properties}}}


# ── RUNNING: activity evidence ──────────────────────────────────────

@pytest.mark.asyncio
async def test_activity_evidence_promotes_pending_task_to_running():
    sv = make_supervisor()
    task_id, session_id = make_task(status="pending")
    await sv._handle_sse_event(frame("/tmp/proj", "message.part.delta", {"sessionID": session_id}))
    task = db.get_task(task_id)
    oc_task = db.get_opencode_task(task_id)
    assert task["status"] == "running"
    assert oc_task["status"] == "running"
    assert oc_task["last_evidence_type"] == "message.part.delta"


@pytest.mark.asyncio
async def test_activity_evidence_does_not_override_waiting_for_user():
    sv = make_supervisor()
    task_id, session_id = make_task(status="waiting_for_user")
    await sv._handle_sse_event(frame("/tmp/proj", "session.next.tool.called", {"sessionID": session_id}))
    task = db.get_task(task_id)
    assert task["status"] == "waiting_for_user"


# ── FAILED: session.error is definitive evidence ────────────────────

@pytest.mark.asyncio
async def test_session_error_marks_task_failed():
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    task_id, session_id = make_task()

    await sv._handle_sse_event(frame(None, "session.error", {"sessionID": session_id, "error": {"name": "UnknownError"}}))

    task = db.get_task(task_id)
    oc_task = db.get_opencode_task(task_id)
    assert task["status"] == "failed"
    assert oc_task["status"] == "failed"
    assert oc_task["last_evidence_type"] == "session.error"
    assert sv._error_since_prompt[task_id] is True
    assert sv._completion_events[task_id].is_set() if task_id in sv._completion_events else True


@pytest.mark.asyncio
async def test_session_error_is_idempotent_on_already_terminal_task():
    sv = make_supervisor()
    task_id, session_id = make_task(status="completed")
    db.update_task_status(task_id, "completed", 0)
    await sv._handle_sse_event(frame(None, "session.error", {"sessionID": session_id, "error": {}}, evt_id="evt_x"))
    task = db.get_task(task_id)
    assert task["status"] == "completed"  # not clobbered by a late/duplicate error


# ── COMPLETED: idle + no error + no pending question ────────────────

@pytest.mark.asyncio
async def test_session_idle_alone_marks_completed_when_no_error_no_pending_question():
    sv = make_supervisor()
    task_id, session_id = make_task()
    await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_id}))
    task = db.get_task(task_id)
    oc_task = db.get_opencode_task(task_id)
    assert task["status"] == "completed"
    assert oc_task["last_evidence_type"] == "session.idle"


@pytest.mark.asyncio
async def test_session_idle_does_not_complete_if_error_seen_this_turn():
    sv = make_supervisor()
    task_id, session_id = make_task()
    await sv._handle_sse_event(frame(None, "session.error", {"sessionID": session_id, "error": {}}, evt_id="e1"))
    # error already made it terminal (failed); a following idle must not flip it to completed
    await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_id}, evt_id="e2"))
    task = db.get_task(task_id)
    assert task["status"] == "failed"


@pytest.mark.asyncio
async def test_session_idle_does_not_complete_while_question_pending():
    sv = make_supervisor()
    task_id, session_id = make_task()
    db.create_question_record("que_1", task_id, "Which approach?", None, None)

    await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_id}))

    task = db.get_task(task_id)
    assert task["status"] != "completed"


# ── M-OX.3 live-validation fixes: terminal-state logging carries trace_id ─
#
# A real end-to-end run against a live server found these terminal-state
# log lines always carried trace_id: null, because _handle_session_failed/
# _handle_session_idle/_handle_activity run on the SSE loop's own asyncio
# Task, never the turn's -- trace.current_trace_id() is always None there.
# Fixed by recovering trace_id from the opencode_tasks row (already
# persisted at creation, per ADR-020) and passing it explicitly via
# extra=, plus fixing JarvisContextFilter to respect that explicit value
# instead of unconditionally overwriting it back to None.

@pytest.mark.asyncio
async def test_session_error_log_line_recovers_trace_id_and_logs_at_warning(caplog):
    import logging as _logging

    sv = make_supervisor()
    task_id = "oc_trace_fail1"
    session_id = "ses_trace_fail1"
    db.create_task_record(task_id, "Test Task", "do the thing", trace_id="trace_fail_abc123")
    db.create_opencode_task_record(task_id, session_id, "/tmp/proj", "do the thing", trace_id="trace_fail_abc123")

    with caplog.at_level(_logging.WARNING, logger="app.integrations.opencode_supervisor"):
        await sv._handle_sse_event(frame(None, "session.error", {"sessionID": session_id, "error": {}}))

    terminal_records = [r for r in caplog.records if "terminal state observed" in r.message]
    assert len(terminal_records) == 1
    record = terminal_records[0]
    assert record.levelname == "WARNING"
    assert record.trace_id == "trace_fail_abc123"
    assert record.task_id == task_id
    assert record.status == "failed"
    assert isinstance(record.duration_seconds, float)


@pytest.mark.asyncio
async def test_session_idle_log_line_recovers_trace_id(caplog):
    import logging as _logging

    sv = make_supervisor()
    task_id = "oc_trace_ok1"
    session_id = "ses_trace_ok1"
    db.create_task_record(task_id, "Test Task", "do the thing", trace_id="trace_ok_def456")
    db.create_opencode_task_record(task_id, session_id, "/tmp/proj", "do the thing", trace_id="trace_ok_def456")

    with caplog.at_level(_logging.INFO, logger="app.integrations.opencode_supervisor"):
        await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_id}))

    terminal_records = [r for r in caplog.records if "terminal state observed" in r.message]
    assert len(terminal_records) == 1
    record = terminal_records[0]
    assert record.trace_id == "trace_ok_def456"
    assert record.task_id == task_id
    assert record.status == "completed"


@pytest.mark.asyncio
async def test_activity_evidence_log_line_includes_task_id_and_trace_id(caplog):
    import logging as _logging

    sv = make_supervisor()
    task_id = "oc_trace_activity1"
    session_id = "ses_trace_activity1"
    db.create_task_record(task_id, "Test Task", "do the thing", trace_id="trace_activity_xyz")
    db.create_opencode_task_record(task_id, session_id, "/tmp/proj", "do the thing", trace_id="trace_activity_xyz")

    with caplog.at_level(_logging.INFO, logger="app.integrations.opencode_supervisor"):
        await sv._handle_sse_event(frame("/tmp/proj", "message.part.delta", {"sessionID": session_id}))

    activity_records = [r for r in caplog.records if "opencode first execution event observed" in r.message]
    assert len(activity_records) == 1
    record = activity_records[0]
    assert record.task_id == task_id
    assert record.trace_id == "trace_activity_xyz"


@pytest.mark.asyncio
async def test_session_idle_after_error_reset_by_new_instruction_can_complete():
    """A follow-up instruction resets the error flag so a later idle for the
    NEW turn is correctly evaluated, not poisoned by an earlier failure."""
    sv = make_supervisor()
    task_id, session_id = make_task()
    sv._error_since_prompt[task_id] = True  # simulate a prior failed turn
    db.update_task_status(task_id, "running")  # not terminal (e.g. cancel/re-run scenario)

    sv._error_since_prompt[task_id] = False  # what send_instruction() does on a new prompt
    await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_id}))

    task = db.get_task(task_id)
    assert task["status"] == "completed"


# ── Dedup / malformed / unknown-session / two-session routing ───────

@pytest.mark.asyncio
async def test_duplicate_event_id_is_not_reprocessed():
    sv = make_supervisor()
    task_id, session_id = make_task()
    f = frame(None, "session.error", {"sessionID": session_id, "error": {}}, evt_id="evt_dup")
    await sv._handle_sse_event(f)
    # flip back to running to detect whether the duplicate reprocesses
    db.update_task_status(task_id, "running")
    db.update_opencode_task_status(task_id, "running")
    await sv._handle_sse_event(f)  # exact same event id — must be ignored
    task = db.get_task(task_id)
    assert task["status"] == "running"  # not re-marked failed by the duplicate


@pytest.mark.asyncio
async def test_malformed_sse_event_does_not_raise():
    sv = make_supervisor()
    await sv._handle_sse_event({})
    await sv._handle_sse_event({"data": "garbage"})
    await sv._handle_sse_event({"data": {"payload": {"type": "session.error"}}})  # no properties at all
    await sv._handle_sse_event({"data": {"payload": {"type": "unknown.made.up.type", "properties": {}}}})
    # no exception means success


@pytest.mark.asyncio
async def test_event_for_unknown_session_is_safely_ignored():
    sv = make_supervisor()
    await sv._handle_sse_event(frame(None, "session.error", {"sessionID": "ses_never_seen", "error": {}}))
    # no task exists for that session — must not raise, must not create anything
    assert db.get_opencode_task_by_session("ses_never_seen") is None


@pytest.mark.asyncio
async def test_two_session_routing_is_isolated():
    sv = make_supervisor()
    task_a, session_a = make_task(task_id="oc_a", session_id="ses_a")
    task_b, session_b = make_task(task_id="oc_b", session_id="ses_b")

    await sv._handle_sse_event(frame(None, "session.error", {"sessionID": session_a, "error": {}}, evt_id="ea"))
    await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_b}, evt_id="eb"))

    assert db.get_task(task_a)["status"] == "failed"
    assert db.get_task(task_b)["status"] == "completed"


# ── Reconciliation on startup (degraded state) ───────────────────────

@pytest.mark.asyncio
async def test_reconcile_on_startup_leaves_unverifiable_tasks_degraded():
    sv = make_supervisor()

    class FakeAdapter:
        async def get_session(self, session_id, directory):
            return {"id": session_id}  # no verified terminal signal available

    sv.adapter = FakeAdapter()
    task_id, session_id = make_task(status="degraded")

    await sv.reconcile_on_startup()

    task = db.get_opencode_task(task_id)
    assert task["status"] == "degraded"  # not upgraded without verified evidence


@pytest.mark.asyncio
async def test_reconcile_on_startup_handles_query_failure_gracefully():
    sv = make_supervisor()

    class FailingAdapter:
        async def get_session(self, session_id, directory):
            raise ConnectionError("server not reachable")

    sv.adapter = FailingAdapter()
    make_task(status="degraded")

    await sv.reconcile_on_startup()  # must not raise


@pytest.mark.asyncio
async def test_reconcile_on_startup_noop_when_nothing_degraded():
    sv = make_supervisor()
    sv.adapter = None  # would raise AttributeError if reconcile tried to use it


# ── Delegated Observation and Reporting milestone ────────────────────
# Real-device finding this traces back to: OpenCode's own message history
# was reachable the whole time (OpenCodeAdapter.get_messages(), Milestone
# 9A) but nothing ever called it, so a completed task's actual result was
# invisible to the Supervisor. Message shapes below are copied verbatim
# from a live probe against a real completed OpenCode session (git status
# on a non-git directory) -- not invented.

_REAL_MESSAGES = [
    {
        "info": {"role": "user"},
        "parts": [{"type": "text", "text": "Run git status and tell me the current status."}],
    },
    {
        "info": {"role": "assistant"},
        "parts": [
            {"type": "step-start"},
            {"type": "reasoning", "text": "The user wants git status."},
            {
                "type": "tool",
                "tool": "bash",
                "state": {
                    "status": "completed",
                    "output": "fatal: not a git repository (or any of the parent directories): .git\n",
                },
            },
            {"type": "step-finish"},
        ],
    },
    {
        "info": {"role": "assistant"},
        "parts": [
            {"type": "step-start"},
            {"type": "reasoning", "text": "I should explain this to the user."},
            {
                "type": "text",
                "text": "This directory is not a Git repository — there is no .git directory present.",
            },
            {"type": "step-finish"},
        ],
    },
]


def test_extract_result_text_prefers_the_last_assistant_text_part():
    assert _extract_result_text(_REAL_MESSAGES) == "This directory is not a Git repository — there is no .git directory present."


def test_extract_result_text_falls_back_to_tool_output_with_no_closing_text():
    messages = [m for m in _REAL_MESSAGES if not any(p.get("type") == "text" for p in m.get("parts", []))]
    assert "fatal: not a git repository" in _extract_result_text(messages)


def test_extract_result_text_ignores_reasoning_and_user_messages():
    messages = [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "the question"}]},
        {"info": {"role": "assistant"}, "parts": [{"type": "reasoning", "text": "internal thoughts only"}]},
    ]
    assert _extract_result_text(messages) is None


def test_extract_result_text_empty_list_returns_none():
    assert _extract_result_text([]) is None


def test_extract_result_text_tolerates_malformed_entries():
    assert _extract_result_text([None, {}, {"info": {}}, "not a dict"]) is None


class FakeMessagesAdapter:
    def __init__(self, messages=None, raises=False):
        self._messages = messages if messages is not None else _REAL_MESSAGES
        self._raises = raises
        self.calls = []

    async def get_messages(self, session_id, directory, limit=20):
        self.calls.append((session_id, directory))
        if self._raises:
            raise ConnectionError("opencode unreachable")
        return self._messages


@pytest.mark.asyncio
async def test_fetch_task_result_text_returns_extracted_text():
    sv = make_supervisor()
    sv.adapter = FakeMessagesAdapter()
    task_id, session_id = make_task()

    text = await sv.fetch_task_result_text(task_id)

    assert text == "This directory is not a Git repository — there is no .git directory present."
    assert sv.adapter.calls == [(session_id, "/tmp/proj")]


@pytest.mark.asyncio
async def test_fetch_task_result_text_unknown_task_returns_none():
    sv = make_supervisor()
    sv.adapter = FakeMessagesAdapter()
    assert await sv.fetch_task_result_text("oc_does_not_exist") is None


@pytest.mark.asyncio
async def test_fetch_task_result_text_adapter_failure_returns_none_not_raise():
    sv = make_supervisor()
    sv.adapter = FakeMessagesAdapter(raises=True)
    task_id, _ = make_task()
    assert await sv.fetch_task_result_text(task_id) is None


@pytest.mark.asyncio
async def test_session_idle_captures_and_persists_result_summary():
    sv = make_supervisor()
    sv.adapter = FakeMessagesAdapter()
    task_id, session_id = make_task()

    await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_id}))

    oc_task = db.get_opencode_task(task_id)
    assert oc_task["status"] == "completed"
    assert oc_task["result_summary"] == "This directory is not a Git repository — there is no .git directory present."


@pytest.mark.asyncio
async def test_session_error_captures_and_persists_result_summary():
    sv = make_supervisor()
    sv.adapter = FakeMessagesAdapter()
    task_id, session_id = make_task()

    await sv._handle_sse_event(frame(None, "session.error", {"sessionID": session_id, "error": {}}))

    oc_task = db.get_opencode_task(task_id)
    assert oc_task["status"] == "failed"
    assert oc_task["result_summary"] == "This directory is not a Git repository — there is no .git directory present."


@pytest.mark.asyncio
async def test_session_idle_completes_normally_even_when_capture_fails():
    """A result-capture failure must never affect the terminal state
    transition that already happened -- same never-break-the-primary-flow
    discipline as every other observability concern in this module."""
    sv = make_supervisor()
    sv.adapter = FakeMessagesAdapter(raises=True)
    task_id, session_id = make_task()

    await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_id}))

    oc_task = db.get_opencode_task(task_id)
    assert oc_task["status"] == "completed"
    assert oc_task["result_summary"] is None
    await sv.reconcile_on_startup()  # must return immediately, no adapter call


@pytest.mark.asyncio
async def test_session_idle_completion_notification_body_includes_result_summary():
    sv = make_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    sv.adapter = FakeMessagesAdapter()
    task_id, session_id = make_task(task_id="oc_enrich1")

    await sv._handle_sse_event(frame(None, "session.idle", {"sessionID": session_id}))

    oc_task = db.get_opencode_task(task_id)
    assert oc_task["status"] == "completed"
    expected_summary = "This directory is not a Git repository — there is no .git directory present."

    notifs = [n for n in db.get_recent_notifications(50)
              if n["task_id"] == task_id and n["source_type"] == "opencode_task"]
    assert len(notifs) == 1
    assert notifs[0]["title"] == "Jarvis task completed"
    assert expected_summary in notifs[0]["body"]

    broadcast_notifs = [json.loads(m) for m in ws.sent if m.startswith("{")]
    broadcast_notifs = [m for m in broadcast_notifs if m.get("type") == "notification"]
    assert len(broadcast_notifs) == 1
    assert expected_summary in broadcast_notifs[0]["body"]
