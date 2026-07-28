"""ADR-023: Jarvis's own service lifecycle (start/stop/restart), managed
from the Operations console process -- never from Jarvis itself. Jarvis
cannot safely act on its own lifecycle: the process handling a "restart"
request would be the process being restarted. Unlike OpenCode (already
owned in-process by OpenCodeSupervisor, reused via a proxy), there is no
in-process object to proxy to for Jarvis: when Jarvis is down, there is
nothing on the other end of a proxy call.

Mirrors OpenCodeServerManager's classify-before-acting pattern and
OpenCodeSupervisor's claim/run_claimed race-safe state machine (ADR-022),
reusing process_utils.py and owner_marker.py directly rather than
reimplementing process management.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import httpx

from app import config
from app.integrations import process_utils, owner_marker
from app.operational_state import OperationalState

logger = logging.getLogger("app.operations")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _looks_like_jarvis(identity: dict | None, port: int) -> bool:
    """Best-effort identity check before terminating anything by PID --
    never a substitute for PID-based targeting, only a guard against
    acting on an unrelated process that merely occupies the port."""
    if not identity:
        return False
    cmd = (identity.get("command_line") or "").lower()
    return "uvicorn" in cmd and "app.main" in cmd and str(port) in cmd


class JarvisClassification:
    NONE = "none"                               # nothing listening -- safe to spawn
    HEALTHY_EXTERNAL = "healthy_external"        # answers correctly, not ours -- attach, never kill
    HEALTHY_OWNED_STALE = "healthy_owned_stale"  # answers correctly, our own marker matches -- safe to reconcile
    UNRESPONSIVE = "unresponsive"                # listening, looks like Jarvis, not answering -- fail closed
    PORT_CONFLICT = "port_conflict"              # listening, does not look like Jarvis at all -- fail closed


class JarvisProcessManagerError(Exception):
    pass


class JarvisProcessManager:
    def __init__(self, host="127.0.0.1", port=8443, base_url=None, python_exe=None,
                 cwd=None, ssl_keyfile=None, ssl_certfile=None, owner_marker_path=None,
                 extra_env=None):
        self.host = host
        self.port = port
        self.base_url = base_url or f"http://127.0.0.1:{port}"
        self.python_exe = python_exe or config.venv_python()
        self.cwd = cwd or os.getcwd()
        self.ssl_keyfile = ssl_keyfile
        self.ssl_certfile = ssl_certfile
        self.owner_marker_path = owner_marker_path or config.operations_jarvis_owner_marker_default()
        self.extra_env = extra_env or {}

        self._proc: asyncio.subprocess.Process | None = None
        self.owned = False
        self._owned_pid: int | None = None

        self.state = OperationalState.STOPPED
        self.last_operation: str | None = None
        self.last_operation_result: str | None = None
        self.last_operation_error: str | None = None
        self.last_updated: str | None = None

    # ── race-safe claim mechanism (ADR-022 pattern) ──────────────────

    def _claim(self, operation: str, from_states: set, to_state: str) -> bool:
        if self.state not in from_states:
            return False
        self.state = to_state
        self.last_operation = operation
        self.last_operation_result = None
        self.last_operation_error = None
        self.last_updated = _now_iso()
        return True

    def claim_start(self) -> bool:
        return self._claim("start", {OperationalState.STOPPED, OperationalState.FAILED}, OperationalState.STARTING)

    def claim_stop(self) -> bool:
        return self._claim("stop", {OperationalState.RUNNING, OperationalState.FAILED}, OperationalState.STOPPING)

    def claim_restart(self) -> bool:
        return self._claim("restart", {OperationalState.RUNNING, OperationalState.FAILED}, OperationalState.STOPPING)

    # ── classification ────────────────────────────────────────────

    async def _probe_alive(self) -> bool:
        """GET / is unauthenticated (unlike /api/dashboard/snapshot, which
        is gated by _require_api_token when JARVIS_API_TOKEN is set) --
        using it means this probe works regardless of that setting."""
        try:
            async with httpx.AsyncClient(verify=False, timeout=3) as client:
                r = await client.get(f"{self.base_url}/")
                return r.status_code == 200
        except Exception:
            return False

    async def _classify(self) -> tuple[str, dict]:
        listening_pid = await process_utils.find_listening_pid(self.port)
        diagnostics: dict[str, int | dict | None] = {"listening_pid": listening_pid}
        if listening_pid is None:
            return JarvisClassification.NONE, diagnostics

        # Process identity is checked FIRST and gates everything else --
        # not just a fallback consulted when the HTTP probe fails. GET /
        # returning 200 is not, by itself, evidence this is Jarvis: any
        # generic web server (confirmed live: `python -m http.server`)
        # answers 200 to a bare GET /, which would otherwise misclassify
        # an unrelated process as a healthy, already-running Jarvis.
        identity = await process_utils.get_process_identity(listening_pid)
        diagnostics["process_identity"] = identity
        if not _looks_like_jarvis(identity, self.port):
            return JarvisClassification.PORT_CONFLICT, diagnostics

        alive = await self._probe_alive()
        if not alive:
            return JarvisClassification.UNRESPONSIVE, diagnostics

        marker = owner_marker.read_owner_marker(self.owner_marker_path)
        if marker and marker.get("port") == self.port and marker.get("pid") == listening_pid:
            return JarvisClassification.HEALTHY_OWNED_STALE, diagnostics
        return JarvisClassification.HEALTHY_EXTERNAL, diagnostics

    # ── start/stop/restart bodies (caller must already hold the claim) ──

    async def _do_start(self) -> tuple[bool, str | None]:
        """Mechanics only -- deliberately does not touch
        last_operation_result/error (see run_claimed_restart's docstring:
        a restart's stop sub-phase completing must never make
        last_operation_result briefly read "success" for the still-in-
        progress overall restart -- found via live validation)."""
        try:
            classification, diagnostics = await self._classify()
            logger.info("Jarvis startup diagnostics: classification=%s port=%s", classification, self.port)

            if classification == JarvisClassification.HEALTHY_EXTERNAL:
                self.owned = False
                self._owned_pid = diagnostics["listening_pid"]
                self.state = OperationalState.RUNNING
                return True, None

            if classification == JarvisClassification.HEALTHY_OWNED_STALE:
                stale_pid = diagnostics["listening_pid"]
                logger.warning("Stale Jarvis process from a prior console run detected (pid=%s) -- cleaning it up", stale_pid)
                await process_utils.kill_process_tree(stale_pid, force=False)
                if not await process_utils.wait_port_released(self.port, timeout=8):
                    await process_utils.kill_process_tree(stale_pid, force=True)
                    await process_utils.wait_port_released(self.port, timeout=5)
                owner_marker.clear_owner_marker(self.owner_marker_path)
                # fall through to spawn a fresh instance below

            elif classification == JarvisClassification.PORT_CONFLICT:
                raise JarvisProcessManagerError(
                    f"Something is already listening on port {self.port} that does not look like Jarvis "
                    f"(pid={diagnostics.get('listening_pid')}, identity={diagnostics.get('process_identity')}). "
                    f"Not attaching or killing it."
                )

            elif classification == JarvisClassification.UNRESPONSIVE:
                raise JarvisProcessManagerError(
                    f"Something is listening on port {self.port} and looks like Jarvis but did not respond "
                    f"to a health probe. Failing closed -- not attaching or killing it."
                )

            args = [self.python_exe, "-m", "uvicorn", "app.main:app", "--host", self.host, "--port", str(self.port)]
            if self.ssl_keyfile and self.ssl_certfile:
                args += ["--ssl-keyfile", self.ssl_keyfile, "--ssl-certfile", self.ssl_certfile]

            env = config.subprocess_env(self.extra_env)
            logger.info("Starting Jarvis on port %s (cwd=%s)", self.port, self.cwd)
            self._proc = await asyncio.create_subprocess_exec(
                *args, cwd=self.cwd, env=env,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )

            if not await self._wait_for_healthy(timeout=30):
                raise JarvisProcessManagerError("Jarvis did not become healthy within the startup timeout")

            resolved_pid = await process_utils.find_listening_pid(self.port)
            if resolved_pid is None:
                raise JarvisProcessManagerError("Jarvis reported healthy but nothing is listening on its port")
            self._owned_pid = resolved_pid
            self.owned = True
            owner_marker.write_owner_marker(self.owner_marker_path, resolved_pid, self.port)

            logger.info("Jarvis is healthy (owned_pid=%s, launcher_pid=%s)", resolved_pid, self._proc.pid)
            self.state = OperationalState.RUNNING
            return True, None
        except Exception as e:
            self.state = OperationalState.FAILED
            logger.error("Jarvis failed to start: %s", e, exc_info=True)
            return False, str(e)

    async def _wait_for_healthy(self, timeout: int = 30) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self._probe_alive():
                return True
            await asyncio.sleep(1)
        return False

    async def _do_stop(self) -> tuple[bool, str | None]:
        """Mechanics only -- see _do_start()'s docstring for why this does
        not touch last_operation_result/error itself."""
        try:
            pid = await process_utils.find_listening_pid(self.port)
            if pid is None:
                self.owned = False
                self._owned_pid = None
                self._proc = None
                owner_marker.clear_owner_marker(self.owner_marker_path)
                self.state = OperationalState.STOPPED
                return True, None

            await process_utils.kill_process_tree(pid, force=False)
            released = await process_utils.wait_port_released(self.port, timeout=8)
            if not released:
                logger.warning("Graceful stop did not release port %s -- forcing termination of pid=%s", self.port, pid)
                await process_utils.kill_process_tree(pid, force=True)
                released = await process_utils.wait_port_released(self.port, timeout=5)

            if not released:
                raise JarvisProcessManagerError(f"Could not release port {self.port} after forced termination of pid={pid}")

            self._proc = None
            self.owned = False
            self._owned_pid = None
            owner_marker.clear_owner_marker(self.owner_marker_path)

            logger.info("Jarvis stopped (port %s released)", self.port)
            self.state = OperationalState.STOPPED
            return True, None
        except Exception as e:
            self.state = OperationalState.FAILED
            logger.error("Jarvis failed to stop: %s", e, exc_info=True)
            return False, str(e)

    def _finalize(self, ok: bool, error: str | None) -> bool:
        self.last_operation_result = "success" if ok else "failure"
        self.last_operation_error = error
        self.last_updated = _now_iso()
        return ok

    async def run_claimed_start(self) -> bool:
        ok, error = await self._do_start()
        return self._finalize(ok, error)

    async def run_claimed_stop(self) -> bool:
        ok, error = await self._do_stop()
        return self._finalize(ok, error)

    async def run_claimed_restart(self) -> bool:
        """Stop and start are sequenced, not atomic: if stop fails, start
        is never attempted and the true state (FAILED) is left in place
        rather than reporting a false restart."""
        stopped_ok, stop_error = await self._do_stop()
        if not stopped_ok:
            return self._finalize(False, stop_error)
        # _do_stop() already set state to STOPPED; advance straight to
        # STARTING with no intervening await, so no other caller can
        # observe or act on the momentary STOPPED state.
        self.state = OperationalState.STARTING
        self.last_updated = _now_iso()
        started_ok, start_error = await self._do_start()
        return self._finalize(started_ok, start_error)

    # ── status ────────────────────────────────────────────────────

    async def _reconcile_quiescent_state(self) -> None:
        """Read-only -- never kills or spawns anything. A fresh console
        process always starts believing Jarvis is STOPPED, regardless of
        reality: after a console restart (or a second operator's console
        having started it), a real, alive Jarvis can be running with no
        in-memory record of it here. Without this, snapshot_status() would
        keep reporting stale STOPPED/FAILED forever, since nothing ever
        prompts a fresh classification outside of claim_start()/stop()."""
        listening_pid = await process_utils.find_listening_pid(self.port)
        if listening_pid is None:
            return
        identity = await process_utils.get_process_identity(listening_pid)
        if not _looks_like_jarvis(identity, self.port):
            return
        if not await self._probe_alive():
            return
        self.owned = False
        self._owned_pid = listening_pid
        self.state = OperationalState.RUNNING
        self.last_operation = self.last_operation or "start"
        self.last_operation_result = "success"
        self.last_operation_error = None
        self.last_updated = _now_iso()
        logger.info("Reconciled Jarvis status: found a live instance (pid=%s) this console had no record of", listening_pid)

    async def snapshot_status(self) -> dict:
        """State and health are different questions (ADR-022 Phase 4):
        health is only meaningful while RUNNING."""
        if self.state in (OperationalState.STOPPED, OperationalState.FAILED):
            await self._reconcile_quiescent_state()
        if self.state == OperationalState.RUNNING:
            health = "HEALTHY" if await self._probe_alive() else "UNHEALTHY"
        else:
            health = "UNKNOWN"
        return {
            "state": self.state,
            "health": health,
            "last_operation": self.last_operation,
            "last_operation_result": self.last_operation_result,
            "last_operation_error": self.last_operation_error,
            "last_updated": self.last_updated,
        }
