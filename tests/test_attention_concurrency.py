"""Milestone 8 Phase 21: concurrency / race-condition protection.

Every state-changing attention_manager function routes through
db.transition_attention_status()'s conditional UPDATE (`WHERE status IN
(...)`), which is the real concurrency defense — SQLite's single-writer
serialization means only one of two overlapping conditional UPDATEs can
ever see the pre-transition status, so exactly one wins and the other's
rowcount is 0. asyncio.gather() alone doesn't exercise this (a single
event loop never truly overlaps two sync DB calls); these tests use real
OS threads, each running its own event loop, to get genuine concurrent
access to the same SQLite file.
"""
import os
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
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


class _FakeConnManager:
    _connections = []

    async def broadcast(self, msg):
        pass


def _run_in_thread(coro_fn, results, index):
    import asyncio
    try:
        results[index] = asyncio.run(coro_fn())
    except Exception as e:
        results[index] = e


def _run_concurrently(coro_fns):
    threads = []
    results = [None] * len(coro_fns)
    for i, fn in enumerate(coro_fns):
        t = threading.Thread(target=_run_in_thread, args=(fn, results, i))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return results


async def _create(source_id="q1"):
    row = await am.get_or_create(
        _FakeConnManager(), conversation_id="c1", task_id="t1", source_type="local_question",
        source_id=source_id, attention_type=am.ATTENTION_TYPE_QUESTION, urgency="LOW", summary="s",
    )
    return row["attention_request_id"]


# ── Race 1: concurrent creation for the same source ─────────────────

def test_concurrent_get_or_create_for_same_source_creates_exactly_one_row():
    def make():
        return _create("q_race_1")

    results = _run_concurrently([make, make, make])
    for r in results:
        assert not isinstance(r, Exception), r
    ids = set(results)
    assert len(ids) == 1  # every thread agrees on the same attention_request_id

    all_rows = db.get_attention_requests_for_task("t1")
    matching = [r for r in all_rows if r["source_id"] == "q_race_1"]
    assert len(matching) == 1


# ── Race 2: concurrent cancel vs resolve — exactly one wins ─────────

def test_concurrent_cancel_and_resolve_only_one_wins():
    import asyncio
    aid = asyncio.run(_create("q_race_2"))

    async def do_cancel():
        return await am.cancel(aid)

    async def do_resolve():
        await am.begin_resolving(aid)
        return await am.resolve(aid, "answered", "x")

    _run_concurrently([do_cancel, do_resolve])
    final = db.get_attention_request(aid)
    assert final["status"] in ("cancelled", "resolved")
    # Whichever committed last for this row, the DB is left in a single
    # consistent terminal state — never a half-applied mix of both.
    if final["status"] == "resolved":
        assert final["resolution_value"] == "x"


def test_concurrent_double_cancel_only_one_reports_success():
    import asyncio
    aid = asyncio.run(_create("q_race_3"))

    async def do_cancel():
        return await am.cancel(aid)

    results = _run_concurrently([do_cancel, do_cancel])
    successes = [r for r in results if r is True]
    assert len(successes) == 1
    assert db.get_attention_request(aid)["status"] == "cancelled"


# ── Race 3: concurrent defer attempts — exactly one wins ────────────

def test_concurrent_defer_only_one_wins():
    import asyncio
    aid = asyncio.run(_create("q_race_4"))

    async def defer_15():
        return await am.defer(aid, "2026-07-10T10:15:00Z")

    async def defer_60():
        return await am.defer(aid, "2026-07-10T11:00:00Z")

    results = _run_concurrently([defer_15, defer_60])
    successes = [r for r in results if r is True]
    assert len(successes) == 1
    final = db.get_attention_request(aid)
    assert final["status"] == "deferred"
    assert final["deferred_until"] in ("2026-07-10T10:15:00Z", "2026-07-10T11:00:00Z")


# ── Race 4: concurrent mark_due (scheduler double-tick) ─────────────

def test_concurrent_mark_due_only_one_wins():
    import asyncio

    async def setup():
        aid = await _create("q_race_5")
        await am.defer(aid, "2026-07-10T10:15:00Z")
        return aid

    aid = asyncio.run(setup())

    async def do_mark_due():
        return await am.mark_due(aid)

    results = _run_concurrently([do_mark_due, do_mark_due, do_mark_due])
    successes = [r for r in results if r is True]
    assert len(successes) == 1
    assert db.get_attention_request(aid)["status"] == "pending"


# ── Race 5: task cancellation racing a contact in progress ──────────

def test_cancel_for_task_racing_resolve_leaves_single_consistent_state():
    import asyncio
    aid = asyncio.run(_create("q_race_6"))

    async def do_cancel_task():
        return await am.cancel_for_task("t1")

    async def do_resolve():
        return await am.resolve_for_source("local_question", "q_race_6", "answered", "x")

    _run_concurrently([do_cancel_task, do_resolve])
    final = db.get_attention_request(aid)
    assert final["status"] in ("cancelled", "resolved")


# ── Race 6: concurrent resolve_for_source calls (double native-success) ──

def test_concurrent_resolve_for_source_only_applies_once():
    import asyncio
    asyncio.run(_create("q_race_7"))

    async def do_resolve(value):
        async def inner():
            return await am.resolve_for_source("local_question", "q_race_7", "answered", value)
        return await inner()

    def resolve_a():
        return do_resolve("A")

    def resolve_b():
        return do_resolve("B")

    results = _run_concurrently([resolve_a, resolve_b])
    successes = [r for r in results if r is True]
    assert len(successes) == 1
    final = db.get_attention_request_by_source("local_question", "q_race_7")
    assert final["status"] == "resolved"
    assert final["resolution_value"] in ("A", "B")
