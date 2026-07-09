"""Tests for OpenCode server process ownership, shutdown, and stale/incompatible
server classification (Milestone 6, Phases 5 & 6).

All process interaction is mocked — no real opencode.exe is spawned here.
Real start/stop cycles against the actual binary are exercised manually as
part of the Milestone 6 validation matrix (SESSION.md), not in this suite.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.integrations import opencode_server as srv
from app.integrations.opencode_server import (
    OpenCodeServerManager,
    OpenCodeServerError,
    ServerClassification,
    classify_existing_server,
)


@pytest.fixture(autouse=True)
def isolated_marker(monkeypatch):
    tmp = tempfile.mktemp(suffix=".json")
    monkeypatch.setattr(srv, "OWNER_MARKER_PATH", tmp)
    yield tmp
    if os.path.exists(tmp):
        os.unlink(tmp)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeAsyncClient:
    """Minimal httpx.AsyncClient stand-in with a scripted response."""

    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **kw):
        if self._raise_exc:
            raise self._raise_exc
        return self._response

    async def post(self, *a, **kw):
        if self._raise_exc:
            raise self._raise_exc
        return self._response


# ── classify_existing_server ────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_none_when_nothing_listening(monkeypatch):
    async def fake_find(port):
        return None
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find)

    cls, diag = await classify_existing_server(4097, "http://127.0.0.1:4097", "Basic x")
    assert cls == ServerClassification.NONE


@pytest.mark.asyncio
async def test_classify_healthy_external(monkeypatch):
    async def fake_find(port):
        return 4242
    async def fake_identity(pid):
        return {"pid": pid, "name": "opencode.exe", "executable_path": "x", "command_line": "opencode serve"}
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find)
    monkeypatch.setattr(srv.process_utils, "get_process_identity", fake_identity)

    import httpx
    fake_client = FakeAsyncClient(FakeResponse(200, {"healthy": True, "version": "1.15.10"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    cls, diag = await classify_existing_server(4097, "http://127.0.0.1:4097", "Basic x")
    assert cls == ServerClassification.HEALTHY_EXTERNAL
    assert diag["listening_pid"] == 4242


@pytest.mark.asyncio
async def test_classify_healthy_owned_stale_when_marker_matches(monkeypatch, isolated_marker):
    async def fake_find(port):
        return 4242
    async def fake_identity(pid):
        return {"pid": pid, "name": "opencode.exe"}
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find)
    monkeypatch.setattr(srv.process_utils, "get_process_identity", fake_identity)

    with open(isolated_marker, "w") as f:
        json.dump({"pid": 4242, "port": 4097}, f)

    import httpx
    fake_client = FakeAsyncClient(FakeResponse(200, {"healthy": True, "version": "1.15.10"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    cls, diag = await classify_existing_server(4097, "http://127.0.0.1:4097", "Basic x")
    assert cls == ServerClassification.HEALTHY_OWNED_STALE


@pytest.mark.asyncio
async def test_classify_auth_mismatch(monkeypatch):
    async def fake_find(port):
        return 4242
    async def fake_identity(pid):
        return None
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find)
    monkeypatch.setattr(srv.process_utils, "get_process_identity", fake_identity)

    import httpx
    fake_client = FakeAsyncClient(FakeResponse(401, {}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    cls, diag = await classify_existing_server(4097, "http://127.0.0.1:4097", "Basic x")
    assert cls == ServerClassification.AUTH_MISMATCH


@pytest.mark.asyncio
async def test_classify_incompatible_on_bad_shape(monkeypatch):
    async def fake_find(port):
        return 4242
    async def fake_identity(pid):
        return None
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find)
    monkeypatch.setattr(srv.process_utils, "get_process_identity", fake_identity)

    import httpx
    fake_client = FakeAsyncClient(FakeResponse(200, {"not_healthy_key": True}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    cls, diag = await classify_existing_server(4097, "http://127.0.0.1:4097", "Basic x")
    assert cls == ServerClassification.INCOMPATIBLE


@pytest.mark.asyncio
async def test_classify_unresponsive_on_connection_error(monkeypatch):
    async def fake_find(port):
        return 4242
    async def fake_identity(pid):
        return None
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find)
    monkeypatch.setattr(srv.process_utils, "get_process_identity", fake_identity)

    import httpx
    fake_client = FakeAsyncClient(raise_exc=ConnectionError("refused"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    cls, diag = await classify_existing_server(4097, "http://127.0.0.1:4097", "Basic x")
    assert cls == ServerClassification.UNRESPONSIVE
    assert "probe_error" in diag


# ── OpenCodeServerManager.start()/stop() ownership behavior ─────────

@pytest.mark.asyncio
async def test_start_raises_on_auth_mismatch_and_does_not_spawn(monkeypatch):
    async def fake_classify(port, base_url, auth):
        return ServerClassification.AUTH_MISMATCH, {"listening_pid": 999, "process_name": "opencode.exe"}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    spawned = {"called": False}
    async def fake_create_subprocess_exec(*a, **kw):
        spawned["called"] = True
        raise AssertionError("should not spawn when auth-mismatched server is present")
    monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    mgr = OpenCodeServerManager(port=4097)
    with pytest.raises(OpenCodeServerError, match="401|credentials"):
        await mgr.start()
    assert not spawned["called"]
    assert mgr.owned is False


@pytest.mark.asyncio
async def test_start_raises_on_incompatible_server(monkeypatch):
    async def fake_classify(port, base_url, auth):
        return ServerClassification.INCOMPATIBLE, {"listening_pid": 999, "health_status_code": 404}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    mgr = OpenCodeServerManager(port=4097)
    with pytest.raises(OpenCodeServerError):
        await mgr.start()
    assert mgr.owned is False


@pytest.mark.asyncio
async def test_start_raises_on_unknown_unresponsive_server(monkeypatch):
    async def fake_classify(port, base_url, auth):
        return ServerClassification.UNRESPONSIVE, {"listening_pid": 999}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    mgr = OpenCodeServerManager(port=4097)
    with pytest.raises(OpenCodeServerError):
        await mgr.start()
    assert mgr.owned is False


@pytest.mark.asyncio
async def test_start_attaches_without_spawning_on_healthy_external(monkeypatch):
    async def fake_classify(port, base_url, auth):
        return ServerClassification.HEALTHY_EXTERNAL, {"listening_pid": 555}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    async def fake_create_subprocess_exec(*a, **kw):
        raise AssertionError("should not spawn when a compatible external server is present")
    monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    mgr = OpenCodeServerManager(port=4097)
    await mgr.start()
    assert mgr.owned is False
    assert mgr._owned_pid is None


@pytest.mark.asyncio
async def test_stop_never_kills_when_not_owned(monkeypatch):
    async def fake_classify(port, base_url, auth):
        return ServerClassification.HEALTHY_EXTERNAL, {"listening_pid": 555}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    mgr = OpenCodeServerManager(port=4097)
    await mgr.start()
    assert mgr.owned is False

    killed = {"called": False}
    async def fake_kill(pid, force=False):
        killed["called"] = True
        return True
    monkeypatch.setattr(srv.process_utils, "kill_process_tree", fake_kill)

    await mgr.stop()
    assert not killed["called"]


@pytest.mark.asyncio
async def test_stop_kills_owned_server_and_verifies_port_released(monkeypatch):
    async def fake_classify(port, base_url, auth):
        return ServerClassification.NONE, {}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    class FakeProc:
        pid = 111
        returncode = None
        def terminate(self): pass
        def kill(self): pass
        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*a, **kw):
        return FakeProc()
    monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    import httpx
    fake_client = FakeAsyncClient(FakeResponse(200, {"healthy": True, "version": "1.15.10"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    async def fake_find_listening(port):
        return 222  # different from launcher pid 111, matching real observed behavior
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find_listening)

    mgr = OpenCodeServerManager(port=4097)
    await mgr.start()
    assert mgr.owned is True
    assert mgr._owned_pid == 222

    kill_calls = []
    async def fake_kill(pid, force=False):
        kill_calls.append((pid, force))
        return True
    monkeypatch.setattr(srv.process_utils, "kill_process_tree", fake_kill)

    port_released = {"n": 0}
    async def fake_wait_released(port, timeout=10.0, interval=0.5):
        port_released["n"] += 1
        return port_released["n"] >= 2  # not released first check, released after kill
    monkeypatch.setattr(srv.process_utils, "wait_port_released", fake_wait_released)

    await mgr.stop()
    assert mgr.owned is False
    assert kill_calls, "expected the owned pid to be targeted for termination"
    assert kill_calls[0][0] == 222


@pytest.mark.asyncio
async def test_stop_escalates_to_force_kill_if_graceful_kill_insufficient(monkeypatch):
    async def fake_classify(port, base_url, auth):
        return ServerClassification.NONE, {}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    class FakeProc:
        pid = 111
        returncode = None
        def terminate(self): pass
        def kill(self): pass
        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*a, **kw):
        return FakeProc()
    monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    import httpx
    fake_client = FakeAsyncClient(FakeResponse(200, {"healthy": True, "version": "1.15.10"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    async def fake_find_listening(port):
        return 333
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find_listening)

    mgr = OpenCodeServerManager(port=4097)
    await mgr.start()

    kill_calls = []
    async def fake_kill(pid, force=False):
        kill_calls.append((pid, force))
        return True
    monkeypatch.setattr(srv.process_utils, "kill_process_tree", fake_kill)

    async def fake_wait_released(port, timeout=10.0, interval=0.5):
        return False  # never released -> forces every escalation step
    monkeypatch.setattr(srv.process_utils, "wait_port_released", fake_wait_released)

    await mgr.stop()
    assert (333, False) in kill_calls  # graceful kill attempted
    assert (333, True) in kill_calls   # forced kill attempted after graceful failed


@pytest.mark.asyncio
async def test_repeated_start_stop_cycles_leave_no_orphan_marker(monkeypatch):
    async def fake_classify(port, base_url, auth):
        return ServerClassification.NONE, {}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    class FakeProc:
        pid = 111
        returncode = None
        def terminate(self): pass
        def kill(self): pass
        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*a, **kw):
        return FakeProc()
    monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    import httpx
    fake_client = FakeAsyncClient(FakeResponse(200, {"healthy": True, "version": "1.15.10"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    async def fake_find_listening(port):
        return 444
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find_listening)

    async def fake_kill(pid, force=False):
        return True
    monkeypatch.setattr(srv.process_utils, "kill_process_tree", fake_kill)

    async def fake_wait_released(port, timeout=10.0, interval=0.5):
        return True
    monkeypatch.setattr(srv.process_utils, "wait_port_released", fake_wait_released)

    for _ in range(3):
        mgr = OpenCodeServerManager(port=4097)
        await mgr.start()
        assert mgr.owned is True
        await mgr.stop()
        assert mgr.owned is False
        assert not os.path.exists(srv.OWNER_MARKER_PATH)


@pytest.mark.asyncio
async def test_shutdown_after_startup_failure_is_safe(monkeypatch):
    async def fake_classify(port, base_url, auth):
        raise OpenCodeServerError("simulated startup failure")
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    mgr = OpenCodeServerManager(port=4097)
    with pytest.raises(OpenCodeServerError):
        await mgr.start()

    # stop() after a failed start must not raise
    await mgr.stop()
    assert mgr.owned is False
