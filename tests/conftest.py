"""Project-wide pytest fixtures.

Control Center hardening: app.supervisor.supervisor and
app.voice_session_manager each hold a module-global `_broadcast_hook`,
set once at import time by app/main.py's top-level `set_broadcast_hook()`
calls. Because pytest runs every test file in one process, any test file
that imports app.main (directly or via TestClient) leaves that global
pointed at the real ConnectionManager for the rest of the run -- so a
later test file's bare `Supervisor()`/`VoiceSessionManager()` instances
would otherwise silently pick up a real hook and perform extra
db.save_event() writes as a side effect of unrelated test files having
run earlier, an order-dependent contamination no test currently asserts
on (so it causes no visible failure today) but is exactly the kind of
thing that should not be left to chance.
"""
import asyncio
import time
from collections.abc import Callable
from typing import Any

import pytest

import app.supervisor.supervisor as supervisor_module
import app.voice_session_manager as voice_session_manager_module


async def _wait_until(predicate: Callable[[], Any], timeout: float = 20.0,
                       interval: float = 0.05) -> Any:
    """Poll until predicate() is truthy; assert-fail on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


@pytest.fixture(autouse=True)
def _reset_broadcast_hooks():
    supervisor_before = supervisor_module._broadcast_hook
    voice_before = voice_session_manager_module._broadcast_hook
    supervisor_module._broadcast_hook = None
    voice_session_manager_module._broadcast_hook = None
    yield
    supervisor_module._broadcast_hook = supervisor_before
    voice_session_manager_module._broadcast_hook = voice_before
