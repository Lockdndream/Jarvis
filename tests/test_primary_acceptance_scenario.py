"""Milestone 8 Phase 25: the primary end-to-end acceptance scenario
(mandatory), reproduced faithfully step by step:

  OpenCode is working on a task. OpenCode reaches a question. Jarvis
  creates one persistent AttentionRequest representing the unresolved need
  for human input. Jarvis decides how and when to contact the user.

  Jarvis: "I need your input on the Jarvis task. OpenCode is asking
  whether to use approach A or B."
  User: "Not now. Come back in fifteen minutes."
  Jarvis: "Okay."

  The original worker question remains unresolved. The AttentionRequest
  becomes deferred. The deferred state survives: browser closure,
  WebSocket disconnect, Jarvis restart.

  Fifteen minutes later, the request becomes due. Jarvis attempts contact
  again through available channels. The user opens or enters a voice
  session.

  Jarvis: "You asked me to come back about the Jarvis task. OpenCode
  still needs a decision between approach A and B."
  User: "Use approach A."

  Jarvis routes the answer to the exact original: attention request,
  worker task, OpenCode session, native question. OpenCode resumes. The
  attention request becomes resolved only after native answer delivery
  succeeds.

Uses the same fake-OpenCode-harness pattern as tests/test_notification_
integration.py and tests/test_opencode_lifecycle.py (OpenCodeSupervisor.
__new__ + a real ConnectionManager + a FakeAdapter) rather than a live
OpenCode server — real OpenCode execution is separately, deliberately
covered by tests/m6_execution_probe.py (Milestone 6) and the Milestone 8
Phase 26 real-phone regression, not re-tested here. "Browser closure /
WebSocket disconnect" needs no special simulation: every assertion below
re-reads state fresh from the database, which is the only thing that
persists across those events by construction.
"""
import collections
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app.integrations.opencode_supervisor import OpenCodeSupervisor
from app.supervisor.supervisor import Supervisor, _resolve_defer_command
from app.supervisor.tools import ToolRegistry
from app.voice_session_manager import VoiceSessionManager
from app.attention_scheduler import AttentionScheduler
from app import attention_manager as am


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


class RecordingWebSocket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class FakeAdapter:
    """Stands in for the real OpenCode HTTP adapter — reply_question()
    succeeding is the "native answer delivery succeeds" the scenario
    requires before resolution."""

    def __init__(self):
        self.replies = []

    async def reply_question(self, question_id, project_dir, answer):
        self.replies.append((question_id, project_dir, answer))


def make_opencode_supervisor():
    sv = OpenCodeSupervisor.__new__(OpenCodeSupervisor)
    sv.cm = ConnectionManager()
    sv._completion_events = {}
    sv._error_since_prompt = {}
    sv._seen_event_ids = collections.deque(maxlen=500)
    sv._seen_event_ids_set = set()
    sv.adapter = FakeAdapter()
    return sv


@pytest.mark.asyncio
async def test_primary_acceptance_scenario():
    # ── Step 1: OpenCode is working on a task; a real client is connected ──
    sv = make_opencode_supervisor()
    ws = RecordingWebSocket()
    await sv.cm.connect(ws)
    am.set_broadcast_hook(sv.cm)

    task_id = "oc_task_jarvis"
    session_id = "ses_1"
    project_dir = "/tmp/jarvis_proj"
    db.create_task_record(task_id, "Jarvis task", "implement the feature")
    db.create_opencode_task_record(task_id, session_id, project_dir, "implement the feature")
    db.update_task_status(task_id, "waiting_for_user")
    db.update_opencode_task_status(task_id, "waiting_for_user")

    # ── Step 2: OpenCode reaches a question ──────────────────────────
    question_id = "q_approach"
    await sv._emit_question({
        "task_id": task_id, "request_id": question_id,
        "question": "Which approach should I use?", "options": ["A", "B"],
        "context": "A is faster. B is safer.",
    })

    # ── Step 3: exactly one WorkerQuestion, one AttentionRequest ─────
    worker_question = db.get_question_record(question_id)
    assert worker_question is not None
    assert worker_question["status"] == "pending"

    attention_row = db.get_attention_request_by_source("opencode_question", question_id)
    assert attention_row is not None
    attention_request_id = attention_row["attention_request_id"]
    assert attention_row["task_id"] == task_id
    assert attention_row["attention_type"] == "QUESTION"

    all_for_source = [
        r for r in db.get_attention_requests_for_task(task_id) if r["source_id"] == question_id
    ]
    assert len(all_for_source) == 1  # exactly one, never duplicated

    # ── Step 4: Jarvis decided how/when to contact — a ContactAttempt exists ──
    attempts = db.get_contact_attempts_for_attention(attention_request_id)
    assert len(attempts) >= 1
    notif_types = [m["notification_type"] for m in ws.sent if m.get("type") == "notification"]
    assert "QUESTION_REQUIRED" in notif_types  # the "I need your input..." contact

    # ── Step 5: "Not now. Come back in fifteen minutes." -> "Okay." ──
    reply = await _resolve_defer_command("Not now. Come back in fifteen minutes.", None)
    assert reply is not None and reply.startswith("Okay")

    # ── Step 6: the original worker question remains unresolved ──────
    worker_question = db.get_question_record(question_id)
    assert worker_question["status"] == "pending"
    assert worker_question["answer"] is None

    # ── Step 7: the AttentionRequest becomes deferred ─────────────────
    deferred_row = db.get_attention_request(attention_request_id)
    assert deferred_row["status"] == "deferred"
    assert deferred_row["deferred_until"] is not None
    deferred_until = deferred_row["deferred_until"]

    # ── Step 8: survives browser closure / WebSocket disconnect ──────
    # Nothing to simulate beyond re-reading from the database — there is
    # no in-memory-only copy of this state anywhere in the system.
    sv.cm.disconnect(ws)
    still_deferred = db.get_attention_request(attention_request_id)
    assert still_deferred["status"] == "deferred"
    assert still_deferred["deferred_until"] == deferred_until

    # ── Step 9: survives a full Jarvis restart ────────────────────────
    # Exercises the real restart-time DB functions app/main.py's lifespan
    # calls on startup — including the Phase 1 reconnaissance fix that
    # excludes OpenCode-backed tasks/questions from the blanket
    # cancel-everything-pending sweep.
    db.mark_running_tasks_interrupted()
    db.mark_running_opencode_tasks_interrupted()

    after_restart = db.get_attention_request(attention_request_id)
    assert after_restart["status"] == "deferred"
    assert after_restart["deferred_until"] == deferred_until
    worker_question_after_restart = db.get_question_record(question_id)
    assert worker_question_after_restart["status"] == "pending"  # NOT cancelled by the restart sweep
    task_after_restart = db.get_task(task_id)
    assert task_after_restart["status"] == "waiting_for_user"  # NOT falsely marked failed

    # opencode_tasks moves to 'degraded' (M6.1: "state unknown, needs
    # reconciliation" — never a false failure claim). Real reconciliation
    # against a live server is OpenCodeSupervisor.reconcile_on_startup()'s
    # job (Milestone 6, tested separately) — stand in for "reconciliation
    # already confirmed the session is still waiting" so this test stays
    # focused on the AttentionRequest lifecycle, which is Milestone 8's
    # actual scope.
    oc_task_after_restart = db.get_opencode_task(task_id)
    assert oc_task_after_restart["status"] == "degraded"
    db.update_opencode_task_status(task_id, "waiting_for_user")

    # New in-process supervisor instance too, matching a real restart —
    # the old `sv` above is deliberately not reused past this point.
    sv2 = make_opencode_supervisor()
    ws2 = RecordingWebSocket()
    await sv2.cm.connect(ws2)
    am.set_broadcast_hook(sv2.cm)

    # ── Step 10: fifteen minutes later, the request becomes due ──────
    # DB-driven scheduler with an injected clock — never a browser timer,
    # never a real fifteen-minute sleep in this test (Phase 8).
    from datetime import datetime, timedelta
    due_time = (
        datetime.fromisoformat(deferred_until.replace("Z", "+00:00")) + timedelta(minutes=1)
    ).isoformat().replace("+00:00", "Z")
    scheduler = AttentionScheduler(sv2.cm, clock=lambda: due_time)
    await scheduler.reconcile_on_startup()

    # ── Step 11: Jarvis attempts contact again ────────────────────────
    recontacted = db.get_attention_request(attention_request_id)
    assert recontacted["status"] == "pending"
    assert recontacted["deferred_until"] is None
    attempts_after_recontact = db.get_contact_attempts_for_attention(attention_request_id)
    assert len(attempts_after_recontact) > len(attempts)  # a new ContactAttempt was made

    # ── Step 12: the user opens/enters a voice session bound to it ───
    supervisor = Supervisor(task_manager=None, opencode_supervisor=sv2)
    vsm = VoiceSessionManager(supervisor)
    session = vsm.open_session("conv_1", attention_request_id)
    assert session["attention_request_id"] == attention_request_id
    assert session["state"] == "listening"

    # Jarvis's re-contact still correlates to the exact original task and
    # question (never re-derived from "most recent" or truncated IDs).
    assert recontacted["source_id"] == question_id
    assert recontacted["task_id"] == task_id

    # ── Step 13/14: "Use approach A." -> routed to the exact original ─
    result = await vsm.handle_transcript(session["voice_session_id"], "Use approach A.")
    assert "A" in result["response"]
    assert result["attention_request_id"] == attention_request_id

    # ── Step 15: Jarvis routed the answer to the exact original WorkerQuestion,
    # via the real adapter.reply_question() (native delivery) ───────
    assert sv2.adapter.replies == [(question_id, project_dir, "A")]
    final_question = db.get_question_record(question_id)
    assert final_question["status"] == "answered"
    assert final_question["answer"] == "A"

    # ── Step 16: OpenCode resumes ─────────────────────────────────────
    final_task = db.get_task(task_id)
    assert final_task["status"] == "running"
    final_oc_task = db.get_opencode_task(task_id)
    assert final_oc_task["status"] == "running"

    # ── Step 17: the AttentionRequest becomes resolved only after native
    # answer delivery succeeded — never before ───────────────────────
    final_attention = db.get_attention_request(attention_request_id)
    assert final_attention["status"] == "resolved"
    assert final_attention["resolution_type"] == "answered"
    assert final_attention["resolution_value"] == "A"
    assert final_attention["resolved_at"] is not None


@pytest.mark.asyncio
async def test_primary_scenario_native_delivery_failure_never_resolves():
    """If adapter.reply_question() raises (native delivery fails), the
    AttentionRequest must never be marked resolved — the explicit Phase 2
    ordering guarantee this whole scenario depends on."""
    sv = make_opencode_supervisor()

    class FailingAdapter:
        async def reply_question(self, question_id, project_dir, answer):
            raise RuntimeError("adapter unreachable")

    sv.adapter = FailingAdapter()

    task_id = "oc_task_2"
    db.create_task_record(task_id, "Jarvis task", "do it")
    db.create_opencode_task_record(task_id, "ses_2", "/tmp/proj2", "do it")
    db.update_task_status(task_id, "waiting_for_user")
    db.update_opencode_task_status(task_id, "waiting_for_user")

    await sv._emit_question({
        "task_id": task_id, "request_id": "q_fail", "question": "Which approach?",
        "options": ["A", "B"], "context": "",
    })
    attention_row = db.get_attention_request_by_source("opencode_question", "q_fail")

    tools = ToolRegistry(task_manager=None, opencode_supervisor=sv)
    result = await tools.call("answer_question", {"question_id": "q_fail", "answer": "A"})
    assert result.startswith("Error")

    final = db.get_attention_request(attention_row["attention_request_id"])
    assert final["status"] != "resolved"
    question = db.get_question_record("q_fail")
    assert question["status"] == "pending"  # never falsely marked answered either
