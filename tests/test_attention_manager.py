"""Milestone 8 Phase 1-6/17/20-23: AttentionRequest data model + state
machine + orchestration.

Follows the same fake-integration harness pattern as
tests/test_notification_integration.py (real ConnectionManager +
RecordingWebSocket, temp DB per test) — no OpenCode/mock_worker process is
needed since attention_manager only depends on app.database and the
deterministic interruption_policy/contact_channels layers underneath it.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
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


def make_conn_manager():
    return ConnectionManager()


async def _connect(cm):
    ws = RecordingWebSocket()
    await cm.connect(ws)
    return ws


def _broadcast_types(ws):
    return [m["type"] for m in ws.sent]


# ── Idempotent creation / deterministic source correlation (Phase 1-3) ─

@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_by_source():
    cm = make_conn_manager()
    row1 = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH",
        summary="s",
    )
    row2 = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH",
        summary="s (different text, must not matter)",
    )
    assert row1["attention_request_id"] == row2["attention_request_id"]
    assert db.get_attention_request_by_source("local_question", "q1")["summary"] == "s"


@pytest.mark.asyncio
async def test_second_get_or_create_does_not_recontact():
    cm = make_conn_manager()
    await _connect(cm)
    await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    contact_count_after_first = len(db.get_contact_attempts_for_attention(
        db.get_attention_request_by_source("local_question", "q1")["attention_request_id"]
    ))
    await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    contact_count_after_second = len(db.get_contact_attempts_for_attention(
        db.get_attention_request_by_source("local_question", "q1")["attention_request_id"]
    ))
    assert contact_count_after_second == contact_count_after_first


@pytest.mark.asyncio
async def test_different_sources_create_distinct_requests():
    cm = make_conn_manager()
    row1 = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    row2 = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q2", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    assert row1["attention_request_id"] != row2["attention_request_id"]


# ── initiate_contact / InterruptionPolicy wiring (Phase 6/9) ───────────

@pytest.mark.asyncio
async def test_urgent_connected_first_contact_creates_voice_contact_attempt():
    cm = make_conn_manager()
    await _connect(cm)
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="opencode_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="URGENT", summary="s",
    )
    attempts = db.get_contact_attempts_for_attention(row["attention_request_id"])
    assert len(attempts) == 1
    assert attempts[0]["channel"] == am.CHANNEL_VOICE_SESSION
    fresh = db.get_attention_request(row["attention_request_id"])
    assert fresh["status"] == am.STATUS_PENDING  # back to pending after contact, not left CONTACTING


@pytest.mark.asyncio
async def test_disconnected_repeat_contact_is_silent_and_schedules_retry():
    cm = make_conn_manager()  # nobody connected
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    aid = row["attention_request_id"]
    # First contact (disconnected, prior_contact_count=0) is PUSH.
    assert len(db.get_contact_attempts_for_attention(aid)) == 1
    # Re-run initiate_contact directly to simulate the scheduler re-evaluating
    # after the retry delay — prior_contact_count is now 1 -> SILENT.
    fresh = db.get_attention_request(aid)
    await am.initiate_contact(cm, fresh)
    assert len(db.get_contact_attempts_for_attention(aid)) == 1  # no new attempt created
    after = db.get_attention_request(aid)
    assert after["next_contact_at"] is not None


# ── Defer / due / re-contact cycle (Phase 7/8/9) ────────────────────────

@pytest.mark.asyncio
async def test_defer_then_mark_due_then_recontact():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    aid = row["attention_request_id"]

    ok = await am.defer(aid, "2026-07-10T10:15:00Z")
    assert ok
    deferred = db.get_attention_request(aid)
    assert deferred["status"] == "deferred"
    assert deferred["deferred_until"] == "2026-07-10T10:15:00Z"

    ok = await am.mark_due(aid)
    assert ok
    due = db.get_attention_request(aid)
    assert due["status"] == "pending"
    assert due["deferred_until"] is None

    await am.initiate_contact(cm, due)
    attempts = db.get_contact_attempts_for_attention(aid)
    assert len(attempts) >= 1  # re-contacted


@pytest.mark.asyncio
async def test_defer_never_touches_the_underlying_worker_question():
    cm = make_conn_manager()
    db.create_task_record("t1", "Task", "do it")
    db.create_question_record("q1", "t1", "Which approach?", "", '["A", "B"]')
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    await am.defer(row["attention_request_id"], "2026-07-10T10:15:00Z")
    q = db.get_question_record("q1")
    assert q["status"] == "pending"  # the WorkerQuestion is completely untouched
    assert q["answer"] is None


@pytest.mark.asyncio
async def test_get_due_attention_requests_respects_injected_clock():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    aid = row["attention_request_id"]
    await am.defer(aid, "2026-07-10T10:15:00Z")

    before = db.get_due_attention_requests(now="2026-07-10T10:10:00Z")
    assert aid not in [r["attention_request_id"] for r in before]

    after = db.get_due_attention_requests(now="2026-07-10T10:16:00Z")
    assert aid in [r["attention_request_id"] for r in after]


# ── Resolution (Phase 2: never resolved before native success) ─────────

@pytest.mark.asyncio
async def test_resolve_for_source_requires_begin_resolving_first_and_records_value():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="opencode_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    ok = await am.resolve_for_source("opencode_question", "q1", "answered", "approach A")
    assert ok
    fresh = db.get_attention_request(row["attention_request_id"])
    assert fresh["status"] == "resolved"
    assert fresh["resolution_type"] == "answered"
    assert fresh["resolution_value"] == "approach A"
    assert fresh["resolved_at"] is not None


@pytest.mark.asyncio
async def test_resolve_for_source_on_already_resolved_is_a_noop():
    cm = make_conn_manager()
    await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="opencode_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    assert await am.resolve_for_source("opencode_question", "q1", "answered", "A")
    assert not await am.resolve_for_source("opencode_question", "q1", "answered", "B")
    fresh = db.get_attention_request_by_source("opencode_question", "q1")
    assert fresh["resolution_value"] == "A"  # second call never overwrote it


@pytest.mark.asyncio
async def test_resolve_for_unknown_source_returns_false():
    assert not await am.resolve_for_source("opencode_question", "does-not-exist", "answered", "A")


@pytest.mark.asyncio
async def test_fail_resolving_returns_to_pending_not_resolved():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="opencode_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    aid = row["attention_request_id"]
    await am.begin_resolving(aid)
    ok = await am.fail_resolving(aid)
    assert ok
    fresh = db.get_attention_request(aid)
    assert fresh["status"] == "pending"
    assert fresh["resolved_at"] is None


# ── Cancellation / source invalidation (Phase 20) ──────────────────────

@pytest.mark.asyncio
async def test_cancel_for_task_cancels_only_unresolved_requests():
    cm = make_conn_manager()
    row_pending = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    row_resolved = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q2", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    await am.resolve_for_source("local_question", "q2", "answered", "x")

    count = await am.cancel_for_task("t1")
    assert count == 1
    assert db.get_attention_request(row_pending["attention_request_id"])["status"] == "cancelled"
    assert db.get_attention_request(row_resolved["attention_request_id"])["status"] == "resolved"


@pytest.mark.asyncio
async def test_restart_interruption_cascades_to_deferred_local_attention_request():
    """Milestone 8.1 real-phone finding (2026-07-10): a local task's
    question/task rows are correctly force-cancelled/failed on Jarvis
    restart (mark_running_tasks_interrupted() — genuine source
    invalidation for a subprocess-backed task, unlike an OpenCode task).
    A deferred AttentionRequest still pointing at that now-dead source
    must not survive as an orphaned reference waiting to re-contact the
    user about a question that no longer exists (Phase 20) — this
    reproduces the exact sequence app/main.py's lifespan runs on restart."""
    cm = make_conn_manager()
    db.create_task_record("local_t1", "Mock Agent", "/mock-agent")
    db.create_question_record("local_q1", "local_t1", "Which approach?", "", '["A", "B"]')
    db.update_task_status("local_t1", "waiting_for_user")

    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="local_t1", source_type="local_question",
        source_id="local_q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    await am.defer(row["attention_request_id"], "2026-07-10T10:15:00Z")
    assert db.get_attention_request(row["attention_request_id"])["status"] == "deferred"

    # Exactly what app/main.py's lifespan does on restart.
    affected_task_ids = db.mark_running_tasks_interrupted()
    assert "local_t1" in affected_task_ids
    for task_id in affected_task_ids:
        await am.cancel_for_task(task_id)

    assert db.get_question_record("local_q1")["status"] == "cancelled"
    assert db.get_task("local_t1")["status"] == "failed"
    final = db.get_attention_request(row["attention_request_id"])
    assert final["status"] == "cancelled"  # no longer orphaned, pointing at a dead source


@pytest.mark.asyncio
async def test_restart_interruption_does_not_touch_opencode_backed_attention_request():
    """Companion to the above: an OpenCode-backed task must NOT be
    touched by mark_running_tasks_interrupted() at all (Phase 1's
    original fix) — its deferred AttentionRequest must survive
    untouched, since the OpenCode session is still real and answerable."""
    cm = make_conn_manager()
    db.create_task_record("oc_t1", "OC Task", "do it")
    db.create_opencode_task_record("oc_t1", "ses_1", "/tmp/proj", "do it")
    db.create_question_record("oc_q1", "oc_t1", "Which approach?", "", '["A", "B"]')
    db.update_task_status("oc_t1", "waiting_for_user")
    db.update_opencode_task_status("oc_t1", "waiting_for_user")

    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="oc_t1", source_type="opencode_question",
        source_id="oc_q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    await am.defer(row["attention_request_id"], "2026-07-10T10:15:00Z")

    affected_task_ids = db.mark_running_tasks_interrupted()
    assert "oc_t1" not in affected_task_ids
    for task_id in affected_task_ids:
        await am.cancel_for_task(task_id)

    assert db.get_question_record("oc_q1")["status"] == "pending"
    assert db.get_task("oc_t1")["status"] == "waiting_for_user"
    final = db.get_attention_request(row["attention_request_id"])
    assert final["status"] == "deferred"  # untouched, still real and answerable


@pytest.mark.asyncio
async def test_cancel_for_source_only_cancels_active_statuses():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    await am.resolve_for_source("local_question", "q1", "answered", "x")
    assert not await am.cancel_for_source("local_question", "q1")
    assert db.get_attention_request(row["attention_request_id"])["status"] == "resolved"


@pytest.mark.asyncio
async def test_expire_transitions_from_pending():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    ok = await am.expire(row["attention_request_id"])
    assert ok
    assert db.get_attention_request(row["attention_request_id"])["status"] == "expired"


# ── Illegal / stale transitions rejected (Phase 21) ────────────────────

@pytest.mark.asyncio
async def test_cannot_resolve_a_request_that_was_never_begun_resolving():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    # resolve() only accepts RESOLVING as its predecessor.
    ok = await am.resolve(row["attention_request_id"], "answered", "x")
    assert not ok
    assert db.get_attention_request(row["attention_request_id"])["status"] == "pending"


@pytest.mark.asyncio
async def test_cannot_defer_an_already_resolved_request():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    await am.resolve_for_source("local_question", "q1", "answered", "x")
    ok = await am.defer(row["attention_request_id"], "2026-07-10T10:15:00Z")
    assert not ok
    assert db.get_attention_request(row["attention_request_id"])["status"] == "resolved"


@pytest.mark.asyncio
async def test_cannot_mark_due_a_request_that_is_not_deferred_or_pending_or_contacting():
    cm = make_conn_manager()
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    await am.cancel(row["attention_request_id"])
    ok = await am.mark_due(row["attention_request_id"])
    assert not ok


# ── Real-time broadcast hook (frontend prerequisite) ───────────────────

@pytest.mark.asyncio
async def test_broadcast_hook_fires_on_transitions_when_set():
    cm = make_conn_manager()
    ws = await _connect(cm)
    am.set_broadcast_hook(cm)
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    types = _broadcast_types(ws)
    assert "attention_contacting" in types
    assert "attention_pending" in types

    ws.sent.clear()
    await am.defer(row["attention_request_id"], "2026-07-10T10:15:00Z")
    assert "attention_deferred" in _broadcast_types(ws)


@pytest.mark.asyncio
async def test_no_broadcast_hook_set_does_not_raise():
    cm = make_conn_manager()
    am.set_broadcast_hook(None)
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id="q1", attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    # No exception raised despite no hook being set — that's the assertion.
    assert row["attention_request_id"]
    assert row["status"] == "pending"
