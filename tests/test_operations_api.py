"""ADR-022 Phase 2/6: the localhost-only, confirmation-gated Operations
API for OpenCode lifecycle control.

Exercises the real app.main FastAPI app via TestClient, same pattern as
tests/test_notification_api.py. opencode_supervisor.start()/stop() (the
lifespan-only entry points) are monkeypatched to no-ops so the app's own
startup/shutdown never touches a real opencode process; .server/.adapter
are swapped for fakes so the Operations endpoints exercise the *real*
claim_start()/run_claimed_start()/etc. state machine end-to-end without
spawning anything real.
"""
import asyncio
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db

_ENV_BEFORE_IMPORT = dict(os.environ)
import app.main as _main_module
for _leaked_var in set(os.environ) - set(_ENV_BEFORE_IMPORT):
    if _leaked_var.startswith("JARVIS_LLM") or _leaked_var.startswith("OPENCODE_") or _leaked_var.startswith("JARVIS_VAPID"):
        del os.environ[_leaked_var]

from app.integrations.opencode_supervisor import OperationalState


class FakeServerManager:
    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0
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
        return self.healthy


class FakeAdapter:
    """consume_events() must actually suspend (await asyncio.sleep), or
    _sse_loop's `while not self._stopped: async for event in
    consume_events(...)` becomes a true busy loop that never yields to
    the event loop -- starves the whole process, including the
    TestClient's own request handling."""

    async def consume_events(self, pattern):
        await asyncio.sleep(0.02)
        if False:
            yield


def _client(monkeypatch, host="127.0.0.1"):
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    main_module = _main_module
    sv = main_module.opencode_supervisor

    async def noop(self):
        pass

    monkeypatch.setattr(sv, "start", noop.__get__(sv))
    monkeypatch.setattr(sv, "stop", noop.__get__(sv))
    monkeypatch.setattr(sv, "server", FakeServerManager())
    monkeypatch.setattr(sv, "adapter", FakeAdapter())
    sv.state = OperationalState.STOPPED
    sv.last_operation = None
    sv.last_operation_result = None
    sv.last_operation_error = None
    sv.last_updated = None

    monkeypatch.delenv("JARVIS_VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("JARVIS_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    from fastapi.testclient import TestClient
    c = TestClient(main_module.app, client=(host, 50000))
    return c, sv, path


def _drain_background_tasks(c, sv):
    """opencode_supervisor.stop() is monkeypatched to a no-op (protecting
    real lifespan shutdown), so a test that left the fake server RUNNING
    would otherwise leak its _sse_task/_poll_task past this test's own
    TestClient teardown. Stop for real, via the same API under test,
    before the client (and its event loop) closes."""
    if sv.state in (OperationalState.RUNNING, OperationalState.STARTING):
        sv.server.fail_stop = False
        if sv.state == OperationalState.STARTING:
            _wait_for(sv, {OperationalState.RUNNING, OperationalState.FAILED})
        if sv.state == OperationalState.RUNNING:
            c.post("/api/operations/opencode/stop", json={"confirm": True})
            _wait_for(sv, {OperationalState.STOPPED, OperationalState.FAILED})


@pytest.fixture
def client(monkeypatch):
    c, sv, path = _client(monkeypatch)
    with c:
        yield c, sv
        _drain_background_tasks(c, sv)
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def remote_client(monkeypatch):
    c, sv, path = _client(monkeypatch, host="10.0.0.5")
    with c:
        yield c, sv
    if os.path.exists(path):
        os.unlink(path)


def _wait_for(sv, terminal_states, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sv.state in terminal_states:
            return
        time.sleep(0.02)
    raise AssertionError(f"state never reached {terminal_states}, stuck at {sv.state}")


# ── Localhost-only gate ──────────────────────────────────────────────

def test_status_rejected_from_non_localhost(remote_client):
    c, sv = remote_client
    resp = c.get("/api/operations/opencode/status")
    assert resp.status_code == 403


def test_start_rejected_from_non_localhost(remote_client):
    c, sv = remote_client
    resp = c.post("/api/operations/opencode/start")
    assert resp.status_code == 403
    assert sv.state == OperationalState.STOPPED  # no side effect from a rejected request


def test_status_allowed_from_localhost(client):
    c, sv = client
    resp = c.get("/api/operations/opencode/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "STOPPED"
    assert body["health"] == "UNKNOWN"


# ── Confirmation flow ────────────────────────────────────────────────

def test_stop_without_confirm_is_rejected(client):
    c, sv = client
    resp = c.post("/api/operations/opencode/stop", json={})
    assert resp.status_code == 400
    assert sv.state == OperationalState.STOPPED


def test_restart_without_confirm_is_rejected(client):
    c, sv = client
    resp = c.post("/api/operations/opencode/restart", json={})
    assert resp.status_code == 400


def test_start_requires_no_confirmation(client):
    c, sv = client
    resp = c.post("/api/operations/opencode/start")
    assert resp.status_code == 202


# ── Real start/stop/restart via the API, end to end ─────────────────

def test_start_transitions_to_running(client):
    c, sv = client
    resp = c.post("/api/operations/opencode/start")
    assert resp.status_code == 202
    assert resp.json()["state"] == "STARTING"
    _wait_for(sv, {OperationalState.RUNNING, OperationalState.FAILED})
    assert sv.state == OperationalState.RUNNING

    status = c.get("/api/operations/opencode/status").json()
    assert status["state"] == "RUNNING"
    assert status["health"] == "HEALTHY"


def test_stop_transitions_to_stopped(client):
    c, sv = client
    c.post("/api/operations/opencode/start")
    _wait_for(sv, {OperationalState.RUNNING})

    resp = c.post("/api/operations/opencode/stop", json={"confirm": True})
    assert resp.status_code == 202
    assert resp.json()["state"] == "STOPPING"
    _wait_for(sv, {OperationalState.STOPPED, OperationalState.FAILED})
    assert sv.state == OperationalState.STOPPED


def test_restart_cycles_through_stopping_then_running(client):
    c, sv = client
    c.post("/api/operations/opencode/start")
    _wait_for(sv, {OperationalState.RUNNING})

    resp = c.post("/api/operations/opencode/restart", json={"confirm": True})
    assert resp.status_code == 202
    _wait_for(sv, {OperationalState.RUNNING, OperationalState.FAILED})
    assert sv.state == OperationalState.RUNNING
    assert sv.last_operation == "restart"
    assert sv.last_operation_result == "success"


# ── Impossible actions rejected (proper HTTP status codes) ──────────

def test_start_while_already_running_is_conflict(client):
    c, sv = client
    c.post("/api/operations/opencode/start")
    _wait_for(sv, {OperationalState.RUNNING})

    resp = c.post("/api/operations/opencode/start")
    assert resp.status_code == 409


def test_stop_while_already_stopped_is_conflict(client):
    c, sv = client
    resp = c.post("/api/operations/opencode/stop", json={"confirm": True})
    assert resp.status_code == 409


def test_duplicate_concurrent_start_requests_only_one_wins(client):
    c, sv = client
    r1 = c.post("/api/operations/opencode/start")
    r2 = c.post("/api/operations/opencode/start")
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [202, 409]


# ── Failure handling ─────────────────────────────────────────────────

def test_start_failure_reported_as_failed_not_running(client):
    c, sv = client
    sv.server.fail_start = True
    resp = c.post("/api/operations/opencode/start")
    assert resp.status_code == 202
    _wait_for(sv, {OperationalState.FAILED})
    assert sv.state == OperationalState.FAILED
    assert sv.last_operation_error is not None

    status = c.get("/api/operations/opencode/status").json()
    assert status["state"] == "FAILED"


def test_restart_stop_failure_does_not_report_running(client):
    c, sv = client
    c.post("/api/operations/opencode/start")
    _wait_for(sv, {OperationalState.RUNNING})
    sv.server.fail_stop = True

    resp = c.post("/api/operations/opencode/restart", json={"confirm": True})
    assert resp.status_code == 202
    _wait_for(sv, {OperationalState.FAILED})
    assert sv.state == OperationalState.FAILED
    assert sv.server.start_calls == 1, "start must never be attempted after a failed stop phase"
