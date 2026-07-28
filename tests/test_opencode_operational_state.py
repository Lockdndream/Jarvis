"""ADR-022 Phase 1/3/4: OpenCodeSupervisor's explicit operational state
model, and the specific lifecycle-bug fix this phase exists to verify --
OpenCodeSupervisor.start() previously never reset self._stopped, so the
SSE/poll loops recreated after a restart would see self._stopped already
True and exit on their very first while-check, leaving a "healthy"-looking
server that silently processes no events.

FakeServerManager/FakeAdapter stand in for OpenCodeServerManager/
OpenCodeAdapter so these tests exercise the supervisor's own state machine
and background-loop wiring without a real opencode subprocess or network.
"""
import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app.integrations.opencode_supervisor import OpenCodeSupervisor, OperationalState


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


class FakeServerManager:
    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0
        self.health_calls = 0
        self.fail_start = False
        self.fail_stop = False
        self.healthy = True
        self.base_url = "http://127.0.0.1:0"

    async def start(self):
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("boom-start")

    async def stop(self):
        self.stop_calls += 1
        if self.fail_stop:
            raise RuntimeError("boom-stop")

    async def check_health(self):
        self.health_calls += 1
        return self.healthy


class FakeAdapter:
    """consume_events() yields nothing and returns quickly -- enough for
    _sse_loop to exercise its real while-condition without real network
    I/O or a busy spin."""

    def __init__(self):
        self.consume_calls = 0

    async def consume_events(self, pattern):
        self.consume_calls += 1
        await asyncio.sleep(0.02)
        if False:
            yield


def make_live_supervisor():
    sv = OpenCodeSupervisor(ConnectionManager(), None)
    sv.server = FakeServerManager()
    sv.adapter = FakeAdapter()
    return sv


async def cleanup(sv):
    if sv.state not in (OperationalState.STOPPED,):
        if sv.claim_stop():
            await sv.run_claimed_stop()


# ── Phase 1: the confirmed defect ───────────────────────────────────────

@pytest.mark.asyncio
async def test_start_resets_stopped_flag():
    sv = make_live_supervisor()
    assert sv._stopped is False  # never-started default

    assert sv.claim_stop() is False  # nothing running yet -- not a valid stop
    assert sv.claim_start()
    assert await sv.run_claimed_start()
    assert sv._stopped is False

    assert sv.claim_stop()
    assert await sv.run_claimed_stop()
    assert sv._stopped is True  # this is the flag the bug left permanently True

    # The exact regression: start() after stop() must flip it back.
    assert sv.claim_start()
    assert await sv.run_claimed_start()
    assert sv._stopped is False

    await cleanup(sv)


@pytest.mark.asyncio
async def test_sse_and_poll_loops_do_not_exit_immediately_after_restart():
    sv = make_live_supervisor()
    assert sv.claim_start()
    await sv.run_claimed_start()
    assert sv.claim_stop()
    await sv.run_claimed_stop()

    assert sv.claim_start()
    await sv.run_claimed_start()

    await asyncio.sleep(0.05)
    assert not sv._sse_task.done(), "SSE loop exited immediately after restart -- _stopped was not reset"
    assert not sv._poll_task.done(), "poll loop exited immediately after restart -- _stopped was not reset"
    assert sv.adapter.consume_calls > 0, "SSE loop never actually ran its body after restart"

    await cleanup(sv)


@pytest.mark.asyncio
async def test_repeated_start_stop_start_restart_cycle_ends_healthy():
    sv = make_live_supervisor()
    assert sv.claim_start() and await sv.run_claimed_start()
    assert sv.state == OperationalState.RUNNING
    assert sv.claim_stop() and await sv.run_claimed_stop()
    assert sv.state == OperationalState.STOPPED
    assert sv.claim_start() and await sv.run_claimed_start()
    assert sv.state == OperationalState.RUNNING
    assert sv.claim_restart()
    assert await sv.run_claimed_restart()
    assert sv.state == OperationalState.RUNNING
    assert sv._stopped is False
    await asyncio.sleep(0.05)
    assert not sv._sse_task.done()
    assert not sv._poll_task.done()

    await cleanup(sv)


@pytest.mark.asyncio
async def test_stop_leaves_no_leaked_tasks():
    sv = make_live_supervisor()
    assert sv.claim_start() and await sv.run_claimed_start()
    sse_task, poll_task = sv._sse_task, sv._poll_task
    assert sv.claim_stop() and await sv.run_claimed_stop()
    assert sv._sse_task is None
    assert sv._poll_task is None
    assert sse_task.cancelled() or sse_task.done()
    assert poll_task.cancelled() or poll_task.done()


@pytest.mark.asyncio
async def test_double_start_does_not_create_duplicate_tasks():
    sv = make_live_supervisor()
    assert sv.claim_start() and await sv.run_claimed_start()
    first_sse, first_poll = sv._sse_task, sv._poll_task

    # A second start() while already RUNNING must be a pure no-op.
    await sv.start()
    assert sv._sse_task is first_sse
    assert sv._poll_task is first_poll

    await cleanup(sv)


# ── Concurrency-safety of the claim mechanism ───────────────────────────

def test_concurrent_claim_start_only_one_winner():
    sv = make_live_supervisor()
    assert sv.claim_start() is True
    assert sv.claim_start() is False  # second caller sees STARTING, loses the claim


def test_concurrent_claim_stop_only_one_winner():
    sv = make_live_supervisor()
    sv.state = OperationalState.RUNNING
    assert sv.claim_stop() is True
    assert sv.claim_stop() is False


def test_claim_restart_rejected_when_already_stopped():
    sv = make_live_supervisor()
    assert sv.state == OperationalState.STOPPED
    assert sv.claim_restart() is False  # nothing running to restart


def test_claim_start_rejected_when_already_running():
    sv = make_live_supervisor()
    sv.state = OperationalState.RUNNING
    assert sv.claim_start() is False


# ── Failure handling ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_failure_moves_to_failed_and_allows_retry():
    sv = make_live_supervisor()
    sv.server.fail_start = True
    assert sv.claim_start()
    ok = await sv.run_claimed_start()
    assert ok is False
    assert sv.state == OperationalState.FAILED
    assert sv.last_operation_result == "failure"
    assert "boom-start" in sv.last_operation_error

    # Recovery: FAILED is a valid state to retry start from.
    sv.server.fail_start = False
    assert sv.claim_start()
    assert await sv.run_claimed_start()
    assert sv.state == OperationalState.RUNNING

    await cleanup(sv)


@pytest.mark.asyncio
async def test_restart_does_not_attempt_start_if_stop_phase_fails():
    sv = make_live_supervisor()
    assert sv.claim_start() and await sv.run_claimed_start()
    sv.server.fail_stop = True

    assert sv.claim_restart()
    ok = await sv.run_claimed_restart()

    assert ok is False
    assert sv.state == OperationalState.FAILED
    assert sv.last_operation == "restart"
    assert sv.server.start_calls == 1, "start must not be attempted after a failed stop phase"


@pytest.mark.asyncio
async def test_restart_reports_true_restart_when_both_phases_succeed():
    sv = make_live_supervisor()
    assert sv.claim_start() and await sv.run_claimed_start()

    assert sv.claim_restart()
    ok = await sv.run_claimed_restart()

    assert ok is True
    assert sv.state == OperationalState.RUNNING
    assert sv.last_operation == "restart"
    assert sv.last_operation_result == "success"

    await cleanup(sv)


# ── Phase 4: state vs. health are different questions ───────────────────

@pytest.mark.asyncio
async def test_snapshot_status_reports_unknown_health_when_stopped():
    sv = make_live_supervisor()
    status = await sv.snapshot_status()
    assert status["state"] == OperationalState.STOPPED
    assert status["health"] == "UNKNOWN"
    assert sv.server.health_calls == 0, "no live health check should run against a stopped server"


@pytest.mark.asyncio
async def test_snapshot_status_reports_healthy_when_running_and_reachable():
    sv = make_live_supervisor()
    assert sv.claim_start() and await sv.run_claimed_start()
    status = await sv.snapshot_status()
    assert status["state"] == OperationalState.RUNNING
    assert status["health"] == "HEALTHY"

    await cleanup(sv)


@pytest.mark.asyncio
async def test_snapshot_status_reports_running_but_unhealthy():
    """The exact case the old is_alive-only signal could not express:
    the process looks up (state RUNNING) but is not actually functioning."""
    sv = make_live_supervisor()
    assert sv.claim_start() and await sv.run_claimed_start()
    sv.server.healthy = False

    status = await sv.snapshot_status()
    assert status["state"] == OperationalState.RUNNING
    assert status["health"] == "UNHEALTHY"

    await cleanup(sv)
