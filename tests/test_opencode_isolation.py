"""Milestone 6.1 (compatibility recovery): OpenCode storage isolation tests.

All process interaction is mocked — no real opencode.exe is spawned here,
and nothing under this suite ever touches OpenCode Desktop's real
~/.local/share/opencode or ~/.config/opencode paths. The real, live
isolation probe (against the actual opencode.exe binary) is documented in
SESSION.md and was run manually as part of this task's acceptance testing,
not as part of this deterministic suite.
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
    ServerClassification,
    default_runtime_dir,
    resolve_runtime_dir,
    isolated_env_overrides,
    ensure_isolated_runtime_provisioned,
)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeAsyncClient:
    def __init__(self, response=None):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **kw):
        return self._response

    async def post(self, *a, **kw):
        return self._response


# ── Runtime directory resolution ─────────────────────────────────────

def test_default_runtime_dir_is_outside_repo(monkeypatch):
    monkeypatch.delenv("JARVIS_OPENCODE_RUNTIME_DIR", raising=False)
    d = default_runtime_dir()
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    assert not os.path.abspath(d).startswith(repo_root)


def test_resolve_runtime_dir_prefers_explicit_env(monkeypatch, tmp_path):
    custom = str(tmp_path / "custom-runtime")
    monkeypatch.setenv("JARVIS_OPENCODE_RUNTIME_DIR", custom)
    assert resolve_runtime_dir() == custom


def test_resolve_runtime_dir_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("JARVIS_OPENCODE_RUNTIME_DIR", raising=False)
    assert resolve_runtime_dir() == default_runtime_dir()


def test_runtime_dir_with_spaces_resolves_correctly(monkeypatch, tmp_path):
    custom = str(tmp_path / "runtime with spaces" / "sub dir")
    monkeypatch.setenv("JARVIS_OPENCODE_RUNTIME_DIR", custom)
    assert resolve_runtime_dir() == custom
    overrides = isolated_env_overrides(custom)
    assert overrides["XDG_DATA_HOME"] == os.path.join(custom, "data")
    assert " " in overrides["XDG_DATA_HOME"]


# ── isolated_env_overrides ───────────────────────────────────────────

def test_isolated_env_overrides_covers_all_four_xdg_vars(tmp_path):
    overrides = isolated_env_overrides(str(tmp_path))
    assert set(overrides.keys()) == {"XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"}
    for v in overrides.values():
        assert str(tmp_path) in v


def test_isolated_env_overrides_never_reference_desktop_paths(tmp_path):
    overrides = isolated_env_overrides(str(tmp_path))
    for v in overrides.values():
        assert ".local\\share\\opencode" not in v
        assert ".config\\opencode" not in v


# ── ensure_isolated_runtime_provisioned ──────────────────────────────

def test_provisioning_creates_minimal_config_without_mcp(tmp_path):
    ensure_isolated_runtime_provisioned(str(tmp_path))
    config_path = tmp_path / "config" / "opencode" / "opencode.jsonc"
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert "mcp" not in data  # never carries over Desktop's blender-mcp entry


def test_provisioning_writes_auth_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-or-v1-test-fake-key-not-real")
    monkeypatch.delenv("JARVIS_OPENCODE_OPENROUTER_KEY", raising=False)
    ensure_isolated_runtime_provisioned(str(tmp_path))
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    assert auth_path.exists()
    data = json.loads(auth_path.read_text())
    assert data["openrouter"]["key"] == "sk-or-v1-test-fake-key-not-real"
    assert data["openrouter"]["type"] == "api"


def test_provisioning_prefers_dedicated_opencode_key_over_llm_key(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-or-v1-supervisor-key")
    monkeypatch.setenv("JARVIS_OPENCODE_OPENROUTER_KEY", "sk-or-v1-dedicated-opencode-key")
    ensure_isolated_runtime_provisioned(str(tmp_path))
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    data = json.loads(auth_path.read_text())
    assert data["openrouter"]["key"] == "sk-or-v1-dedicated-opencode-key"


def test_provisioning_without_any_key_does_not_write_auth(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_OPENCODE_OPENROUTER_KEY", raising=False)
    ensure_isolated_runtime_provisioned(str(tmp_path))
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    assert not auth_path.exists()


def test_provisioning_never_overwrites_existing_isolated_auth(tmp_path, monkeypatch):
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps({"openrouter": {"type": "api", "key": "pre-existing"}}))
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-or-v1-should-not-be-used")
    ensure_isolated_runtime_provisioned(str(tmp_path))
    data = json.loads(auth_path.read_text())
    assert data["openrouter"]["key"] == "pre-existing"


def test_provisioning_never_overwrites_existing_isolated_config(tmp_path):
    config_path = tmp_path / "config" / "opencode" / "opencode.jsonc"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"custom": "operator-provided"}))
    ensure_isolated_runtime_provisioned(str(tmp_path))
    data = json.loads(config_path.read_text())
    assert data == {"custom": "operator-provided"}


def test_provisioning_does_not_touch_desktop_paths(tmp_path, monkeypatch):
    """Sanity: provisioning logic only ever writes under the given
    runtime_dir argument — never a hardcoded Desktop-shared path."""
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-or-v1-test")
    before = {}
    desktop_paths = [
        os.path.expanduser(r"~\.local\share\opencode\opencode.db"),
        os.path.expanduser(r"~\.local\share\opencode\auth.json"),
        os.path.expanduser(r"~\.config\opencode\opencode.jsonc"),
    ]
    for p in desktop_paths:
        before[p] = os.path.exists(p) and os.path.getmtime(p)
    ensure_isolated_runtime_provisioned(str(tmp_path))
    for p in desktop_paths:
        after = os.path.exists(p) and os.path.getmtime(p)
        assert before[p] == after, f"{p} mtime changed — provisioning must never touch Desktop paths"


# ── OpenCodeServerManager: owned vs external isolation behavior ─────

@pytest.mark.asyncio
async def test_owned_server_receives_isolation_env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_OPENCODE_RUNTIME_DIR", str(tmp_path))

    async def fake_classify(port, base_url, auth):
        return ServerClassification.NONE, {}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    captured_env = {}

    class FakeProc:
        pid = 111
        returncode = None
        def terminate(self): pass
        def kill(self): pass
        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return FakeProc()
    monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    import httpx
    fake_client = FakeAsyncClient(FakeResponse(200, {"healthy": True, "version": "1.15.10"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake_client)

    async def fake_find_listening(port):
        return 222
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find_listening)

    mgr = OpenCodeServerManager(port=4097)
    await mgr.start()

    assert captured_env.get("XDG_DATA_HOME") == os.path.join(str(tmp_path), "data")
    assert captured_env.get("XDG_CONFIG_HOME") == os.path.join(str(tmp_path), "config")
    assert captured_env.get("XDG_CACHE_HOME") == os.path.join(str(tmp_path), "cache")
    assert captured_env.get("XDG_STATE_HOME") == os.path.join(str(tmp_path), "state")
    assert mgr.runtime_dir == str(tmp_path)


@pytest.mark.asyncio
async def test_external_server_does_not_trigger_isolation_provisioning(monkeypatch, tmp_path):
    """Attaching to an external server must not require or apply Jarvis's
    isolation environment at all — no spawn happens, so no isolation env is
    ever built for it."""
    monkeypatch.setenv("JARVIS_OPENCODE_RUNTIME_DIR", str(tmp_path))

    async def fake_classify(port, base_url, auth):
        return ServerClassification.HEALTHY_EXTERNAL, {"listening_pid": 555}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    spawn_called = {"value": False}
    async def fake_create_subprocess_exec(*a, **kw):
        spawn_called["value"] = True
        raise AssertionError("must not spawn for an external server")
    monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    provision_called = {"value": False}
    def fake_provision(runtime_dir):
        provision_called["value"] = True
    monkeypatch.setattr(srv, "ensure_isolated_runtime_provisioned", fake_provision)

    mgr = OpenCodeServerManager(port=4097)
    await mgr.start()

    assert mgr.owned is False
    assert not spawn_called["value"]
    assert not provision_called["value"]
    assert mgr.runtime_dir is None


@pytest.mark.asyncio
async def test_runtime_directory_is_created_on_start(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "fresh-runtime"
    monkeypatch.setenv("JARVIS_OPENCODE_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.delenv("JARVIS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_OPENCODE_OPENROUTER_KEY", raising=False)

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
        return 222
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find_listening)

    assert not runtime_dir.exists()
    mgr = OpenCodeServerManager(port=4097)
    await mgr.start()
    # config dir is always created by provisioning; data dir only created if
    # a key is available (no key configured in this test) — check config.
    assert (runtime_dir / "config" / "opencode" / "opencode.jsonc").exists()


@pytest.mark.asyncio
async def test_repeated_restart_reuses_same_isolated_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_OPENCODE_RUNTIME_DIR", str(tmp_path))

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
        return 222
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find_listening)

    async def fake_kill(pid, force=False):
        return True
    monkeypatch.setattr(srv.process_utils, "kill_process_tree", fake_kill)

    async def fake_wait_released(port, timeout=10.0, interval=0.5):
        return True
    monkeypatch.setattr(srv.process_utils, "wait_port_released", fake_wait_released)

    runtime_dirs_seen = []
    for _ in range(3):
        mgr = OpenCodeServerManager(port=4097)
        await mgr.start()
        runtime_dirs_seen.append(mgr.runtime_dir)
        await mgr.stop()

    assert len(set(runtime_dirs_seen)) == 1  # same isolated dir every restart


@pytest.mark.asyncio
async def test_shutdown_does_not_delete_isolated_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_OPENCODE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-or-v1-test")

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
        return 222
    monkeypatch.setattr(srv.process_utils, "find_listening_pid", fake_find_listening)

    async def fake_kill(pid, force=False):
        return True
    monkeypatch.setattr(srv.process_utils, "kill_process_tree", fake_kill)

    async def fake_wait_released(port, timeout=10.0, interval=0.5):
        return True
    monkeypatch.setattr(srv.process_utils, "wait_port_released", fake_wait_released)

    mgr = OpenCodeServerManager(port=4097)
    await mgr.start()
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    assert auth_path.exists()
    await mgr.stop()
    assert auth_path.exists()  # still there — shutdown must not delete storage


@pytest.mark.asyncio
async def test_auth_mismatch_handling_still_works_with_isolation_present(monkeypatch, tmp_path):
    """Isolation must not interfere with the M6 stale/incompatible-server
    fail-closed behavior."""
    monkeypatch.setenv("JARVIS_OPENCODE_RUNTIME_DIR", str(tmp_path))

    async def fake_classify(port, base_url, auth):
        return ServerClassification.AUTH_MISMATCH, {"listening_pid": 999, "process_name": "opencode.exe"}
    monkeypatch.setattr(srv, "classify_existing_server", fake_classify)

    mgr = OpenCodeServerManager(port=4097)
    with pytest.raises(srv.OpenCodeServerError, match="401|credentials"):
        await mgr.start()
    assert mgr.owned is False
    assert mgr.runtime_dir is None  # never even resolved — no spawn attempted


# ── Secrets are never logged ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_key_is_never_logged(tmp_path, monkeypatch, caplog):
    secret = "sk-or-v1-super-secret-value-must-not-leak-into-logs"
    monkeypatch.setenv("JARVIS_LLM_API_KEY", secret)
    import logging
    with caplog.at_level(logging.DEBUG):
        ensure_isolated_runtime_provisioned(str(tmp_path))
    assert secret not in caplog.text
