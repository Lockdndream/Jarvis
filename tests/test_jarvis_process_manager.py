"""ADR-023 Phase 1: JarvisProcessManager -- start/stop/restart of Jarvis's
own process from an external manager. All process_utils calls and the
actual subprocess spawn are faked; this exercises classification,
claim/run race-safety, escalation, and failure handling without spawning
anything real (real end-to-end validation happens separately against a
live isolated instance).
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.jarvis_process_manager import JarvisProcessManager
from app.operational_state import OperationalState
from app.integrations import process_utils


class FakeProc:
    def __init__(self):
        self.pid = 999


def make_manager(tmp_path, **overrides):
    kwargs = dict(
        host="127.0.0.1", port=18443, python_exe="fake-python", cwd=str(tmp_path),
        owner_marker_path=str(tmp_path / "owner.json"),
    )
    kwargs.update(overrides)
    return JarvisProcessManager(**kwargs)


@pytest.fixture
def no_real_subprocess(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return FakeProc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


# ── claim gating (concurrency safety) ───────────────────────────────

def test_claim_start_rejected_when_already_running(tmp_path):
    m = make_manager(tmp_path)
    m.state = OperationalState.RUNNING
    assert m.claim_start() is False


def test_claim_stop_rejected_when_already_stopped(tmp_path):
    m = make_manager(tmp_path)
    assert m.state == OperationalState.STOPPED
    assert m.claim_stop() is False


def test_claim_restart_rejected_when_stopped(tmp_path):
    m = make_manager(tmp_path)
    assert m.claim_restart() is False


def test_concurrent_claim_start_only_one_winner(tmp_path):
    m = make_manager(tmp_path)
    assert m.claim_start() is True
    assert m.claim_start() is False


# ── classification-driven start ─────────────────────────────────────

@pytest.mark.asyncio
async def test_start_from_clean_slate_spawns_and_becomes_running(tmp_path, monkeypatch, no_real_subprocess):
    m = make_manager(tmp_path)
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([None, 4242]))
    monkeypatch.setattr(m, "_probe_alive", _seq_async([True]))  # becomes healthy on first poll

    assert m.claim_start()
    ok = await m.run_claimed_start()

    assert ok is True
    assert m.state == OperationalState.RUNNING
    assert m.owned is True
    assert m._owned_pid == 4242
    assert m.last_operation_result == "success"
    assert os.path.exists(m.owner_marker_path)


@pytest.mark.asyncio
async def test_start_times_out_if_never_healthy(tmp_path, monkeypatch, no_real_subprocess):
    m = make_manager(tmp_path)
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([None]))
    monkeypatch.setattr(m, "_probe_alive", _seq_async([False] * 100))
    monkeypatch.setattr(m, "_wait_for_healthy", _fake_wait_for_healthy_false)

    assert m.claim_start()
    ok = await m.run_claimed_start()

    assert ok is False
    assert m.state == OperationalState.FAILED
    assert "healthy" in m.last_operation_error


@pytest.mark.asyncio
async def test_start_attaches_to_healthy_external_without_spawning(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    spawn_calls = []

    async def fake_exec(*a, **k):
        spawn_calls.append(a)
        return FakeProc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([5555]))
    monkeypatch.setattr(process_utils, "get_process_identity", _async_return({"command_line": f"python -m uvicorn app.main:app --port {m.port}"}))
    monkeypatch.setattr(m, "_probe_alive", _seq_async([True]))
    # no owner marker written -- so this is HEALTHY_EXTERNAL, not our own stale process

    assert m.claim_start()
    ok = await m.run_claimed_start()

    assert ok is True
    assert m.state == OperationalState.RUNNING
    assert m.owned is False
    assert m._owned_pid == 5555
    assert spawn_calls == [], "must not spawn a second process over a healthy external one"


@pytest.mark.asyncio
async def test_start_rejects_port_conflict_without_killing_anything(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    kill_calls = []

    async def fake_kill(pid, force=False):
        kill_calls.append((pid, force))
        return True
    monkeypatch.setattr(process_utils, "kill_process_tree", fake_kill)
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([7777]))
    monkeypatch.setattr(m, "_probe_alive", _seq_async([False]))
    monkeypatch.setattr(process_utils, "get_process_identity", _async_return({"command_line": "some-other-app.exe --port 7777"}))

    assert m.claim_start()
    ok = await m.run_claimed_start()

    assert ok is False
    assert m.state == OperationalState.FAILED
    assert "does not look like Jarvis" in m.last_operation_error
    assert kill_calls == [], "a port conflict must never be killed"


@pytest.mark.asyncio
async def test_start_reconciles_stale_owned_process_then_spawns_fresh(tmp_path, monkeypatch, no_real_subprocess):
    m = make_manager(tmp_path)
    from app.integrations import owner_marker
    owner_marker.write_owner_marker(m.owner_marker_path, 8888, m.port)

    # find_listening_pid is called twice: once by _classify(), once after
    # spawn to resolve the fresh process's real listening pid.
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([8888, 9999]))
    monkeypatch.setattr(process_utils, "get_process_identity", _async_return({"command_line": f"python -m uvicorn app.main:app --port {m.port}"}))
    monkeypatch.setattr(m, "_probe_alive", _seq_async([True, True]))  # classify probe, then post-spawn healthy check
    monkeypatch.setattr(process_utils, "wait_port_released", _async_return(True))
    kill_calls = []

    async def fake_kill(pid, force=False):
        kill_calls.append((pid, force))
        return True
    monkeypatch.setattr(process_utils, "kill_process_tree", fake_kill)

    assert m.claim_start()
    ok = await m.run_claimed_start()

    assert ok is True
    assert m.state == OperationalState.RUNNING
    assert kill_calls and kill_calls[0][0] == 8888
    assert m._owned_pid == 9999


# ── stop ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_when_nothing_listening_is_a_clean_success(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.state = OperationalState.RUNNING
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([None]))

    assert m.claim_stop()
    ok = await m.run_claimed_stop()

    assert ok is True
    assert m.state == OperationalState.STOPPED


@pytest.mark.asyncio
async def test_stop_escalates_to_forced_kill_on_timeout(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.state = OperationalState.RUNNING
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([4242]))
    monkeypatch.setattr(process_utils, "wait_port_released", _seq_async([False, True]))
    kill_calls = []

    async def fake_kill(pid, force=False):
        kill_calls.append((pid, force))
        return True
    monkeypatch.setattr(process_utils, "kill_process_tree", fake_kill)

    assert m.claim_stop()
    ok = await m.run_claimed_stop()

    assert ok is True
    assert m.state == OperationalState.STOPPED
    assert kill_calls == [(4242, False), (4242, True)]


@pytest.mark.asyncio
async def test_stop_reports_failure_if_port_never_releases(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.state = OperationalState.RUNNING
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([4242]))
    monkeypatch.setattr(process_utils, "wait_port_released", _async_return(False))
    monkeypatch.setattr(process_utils, "kill_process_tree", _async_return(True))

    assert m.claim_stop()
    ok = await m.run_claimed_stop()

    assert ok is False
    assert m.state == OperationalState.FAILED
    assert "Could not release port" in m.last_operation_error


# ── restart ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restart_stops_then_starts(tmp_path, monkeypatch, no_real_subprocess):
    m = make_manager(tmp_path)
    m.state = OperationalState.RUNNING
    # find_listening_pid: (1) stop phase finds the running pid, (2) start
    # phase's classify sees nothing listening, (3) post-spawn pid resolve.
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([4242, None, 6161]))
    monkeypatch.setattr(process_utils, "wait_port_released", _async_return(True))
    monkeypatch.setattr(process_utils, "kill_process_tree", _async_return(True))
    monkeypatch.setattr(m, "_probe_alive", _seq_async([True]))

    assert m.claim_restart()
    ok = await m.run_claimed_restart()

    assert ok is True
    assert m.state == OperationalState.RUNNING
    assert m.last_operation == "restart"
    assert m.last_operation_result == "success"


@pytest.mark.asyncio
async def test_restart_does_not_leak_a_premature_success_result_mid_flight(tmp_path, monkeypatch, no_real_subprocess):
    """Regression for a real bug found via live validation: the stop
    sub-phase used to set last_operation_result="success" unconditionally,
    so a status poll landing between the stop and start phases of a
    restart would misreport the still-in-progress restart as already
    succeeded."""
    m = make_manager(tmp_path)
    m.state = OperationalState.RUNNING
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([4242, None, 6161]))
    monkeypatch.setattr(process_utils, "wait_port_released", _async_return(True))
    monkeypatch.setattr(process_utils, "kill_process_tree", _async_return(True))

    async def slow_probe(*a, **k):
        await asyncio.sleep(0.15)
        return True
    monkeypatch.setattr(m, "_probe_alive", slow_probe)

    assert m.claim_restart()
    restart_task = asyncio.create_task(m.run_claimed_restart())

    await asyncio.sleep(0.05)  # land inside the stop->start window
    assert m.last_operation_result is None, (
        f"last_operation_result leaked mid-restart: {m.last_operation_result!r}"
    )
    assert m.state in (OperationalState.STARTING, OperationalState.STOPPING)

    ok = await restart_task
    assert ok is True
    assert m.last_operation_result == "success"


@pytest.mark.asyncio
async def test_restart_does_not_attempt_start_if_stop_fails(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.state = OperationalState.RUNNING
    monkeypatch.setattr(process_utils, "find_listening_pid", _seq([4242]))
    monkeypatch.setattr(process_utils, "wait_port_released", _async_return(False))
    monkeypatch.setattr(process_utils, "kill_process_tree", _async_return(True))
    spawn_calls = []

    async def fake_exec(*a, **k):
        spawn_calls.append(a)
        return FakeProc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert m.claim_restart()
    ok = await m.run_claimed_restart()

    assert ok is False
    assert m.state == OperationalState.FAILED
    assert m.last_operation == "restart"
    assert spawn_calls == [], "start phase must never run after a failed stop phase"


# ── status / health separate from state ─────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_status_unknown_health_when_stopped(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    monkeypatch.setattr(process_utils, "find_listening_pid", _async_return(None))
    status = await m.snapshot_status()
    assert status["state"] == OperationalState.STOPPED
    assert status["health"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_snapshot_status_reconciles_a_live_instance_the_console_forgot(tmp_path, monkeypatch):
    """Regression for a real gap found via live validation: a freshly
    started console always begins believing Jarvis is STOPPED. If Jarvis
    is actually running (e.g. the console that started it crashed and was
    replaced), status must self-correct on the next read, not keep lying
    until someone clicks Start."""
    m = make_manager(tmp_path)
    assert m.state == OperationalState.STOPPED

    monkeypatch.setattr(process_utils, "find_listening_pid", _async_return(4242))
    monkeypatch.setattr(process_utils, "get_process_identity",
                         _async_return({"command_line": f"python -m uvicorn app.main:app --port {m.port}"}))
    monkeypatch.setattr(m, "_probe_alive", _async_return(True))

    status = await m.snapshot_status()

    assert status["state"] == OperationalState.RUNNING
    assert status["health"] == "HEALTHY"
    assert m.owned is False
    assert m._owned_pid == 4242


@pytest.mark.asyncio
async def test_snapshot_status_does_not_reconcile_during_an_in_flight_operation(tmp_path, monkeypatch):
    """STARTING/STOPPING must never be silently overwritten by a status
    read -- only quiescent (STOPPED/FAILED) states are reconciled."""
    m = make_manager(tmp_path)
    m.state = OperationalState.STARTING
    calls = []

    async def spy_find(*a, **k):
        calls.append(a)
        return 4242
    monkeypatch.setattr(process_utils, "find_listening_pid", spy_find)

    status = await m.snapshot_status()

    assert status["state"] == OperationalState.STARTING
    assert calls == [], "reconciliation must not run while an operation is in flight"


@pytest.mark.asyncio
async def test_snapshot_status_running_but_unhealthy(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.state = OperationalState.RUNNING
    monkeypatch.setattr(m, "_probe_alive", _async_return(False))
    status = await m.snapshot_status()
    assert status["state"] == OperationalState.RUNNING
    assert status["health"] == "UNHEALTHY"


# ── test helpers ─────────────────────────────────────────────────────

def _seq(values):
    it = iter(values)
    async def _fn(*a, **k):
        return next(it)
    return _fn


def _seq_async(values):
    it = iter(values)
    async def _fn(*a, **k):
        return next(it)
    return _fn


def _async_return(value):
    async def _fn(*a, **k):
        return value
    return _fn


async def _fake_wait_for_healthy_false(*a, **k):
    return False
