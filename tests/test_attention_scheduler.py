"""Milestone 8 Phase 8/19: persistent, DB-driven scheduler.

Never waits real time — reconcile_on_startup()/run_due_pass() are called
directly, exactly as the module's own docstring specifies tests should
(deterministic clock injection, no browser timers involved at all).
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app import attention_manager as am
from app.attention_scheduler import AttentionScheduler


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


async def _make_deferred(cm, source_id="q1", deferred_until="2026-07-10T10:15:00Z"):
    row = await am.get_or_create(
        cm, conversation_id="c1", task_id="t1", source_type="local_question",
        source_id=source_id, attention_type=am.ATTENTION_TYPE_QUESTION, urgency="HIGH", summary="s",
    )
    await am.defer(row["attention_request_id"], deferred_until)
    return row["attention_request_id"]


@pytest.mark.asyncio
async def test_run_due_pass_recontacts_only_items_whose_deferred_until_has_passed():
    cm = ConnectionManager()
    aid = await _make_deferred(cm)
    scheduler = AttentionScheduler(cm, clock=lambda: "2026-07-10T10:10:00Z")

    processed = await scheduler.run_due_pass()
    assert processed == 0
    assert db.get_attention_request(aid)["status"] == "deferred"

    scheduler = AttentionScheduler(cm, clock=lambda: "2026-07-10T10:20:00Z")
    processed = await scheduler.run_due_pass()
    assert processed == 1
    assert db.get_attention_request(aid)["status"] == "pending"


@pytest.mark.asyncio
async def test_reconcile_on_startup_recovers_overdue_items_after_restart():
    # Simulates: item was deferred, Jarvis was offline past the due time,
    # then restarted — reconcile_on_startup() must catch it, not just items
    # that become due while already running (Phase 19).
    cm = ConnectionManager()
    aid = await _make_deferred(cm, deferred_until="2026-07-10T10:15:00Z")
    scheduler = AttentionScheduler(cm, clock=lambda: "2026-07-10T12:00:00Z")  # well past due

    await scheduler.reconcile_on_startup()
    assert db.get_attention_request(aid)["status"] == "pending"


@pytest.mark.asyncio
async def test_run_due_pass_does_not_process_the_same_item_twice_concurrently():
    import asyncio

    cm = ConnectionManager()
    aid = await _make_deferred(cm, deferred_until="2026-07-10T10:15:00Z")
    scheduler = AttentionScheduler(cm, clock=lambda: "2026-07-10T10:20:00Z")

    # Two overlapping run_due_pass() calls racing over the same due row —
    # the in-process _claiming guard plus the DB-level guarded UPDATE in
    # mark_due() must ensure only one of them actually re-contacts it.
    results = await asyncio.gather(scheduler.run_due_pass(), scheduler.run_due_pass())
    assert sum(results) == 1
    attempts = db.get_contact_attempts_for_attention(aid)
    # Exactly the contact attempts from creation + the single re-contact —
    # never doubled by the race.
    assert len(attempts) <= 2


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle_does_not_raise():
    cm = ConnectionManager()
    scheduler = AttentionScheduler(cm, tick_seconds=60)
    await scheduler.start()
    await scheduler.stop()


@pytest.mark.asyncio
async def test_due_pass_skips_items_resolved_concurrently():
    # If something else resolves/cancels the item between get_due_attention_
    # requests() and the scheduler acting on it, mark_due()'s guarded
    # transition must reject it rather than resurrecting a resolved item.
    cm = ConnectionManager()
    aid = await _make_deferred(cm, deferred_until="2026-07-10T10:15:00Z")
    due_rows = db.get_due_attention_requests(now="2026-07-10T10:20:00Z")
    assert len(due_rows) == 1

    # Resolve it out from under the scheduler before run_due_pass() acts.
    await am.mark_due(aid)
    await am.resolve_for_source("local_question", "q1", "answered", "x")

    scheduler = AttentionScheduler(cm, clock=lambda: "2026-07-10T10:20:00Z")
    processed = await scheduler.run_due_pass(due_rows)
    assert processed == 0
    assert db.get_attention_request(aid)["status"] == "resolved"
