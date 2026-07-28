"""Managed subprocess lifecycle for `opencode serve`.

Ownership model (Milestone 6):
  - Jarvis only terminates a server it can prove it started. Proof is a
    small on-disk marker (`.jarvis_opencode_owner.json`) recording the PID
    that was actually resolved as LISTENING on the port after our own spawn
    (not the launcher PID `asyncio.create_subprocess_exec` returns, which is
    unreliable for `opencode.exe` — see process_utils.py docstring).
  - If something is already listening at startup, it is classified before
    Jarvis does anything: healthy+external (never touched), healthy+stale
    (provably our own orphaned instance from a prior run, safe to clean),
    auth-mismatched/incompatible (reported, never attached or killed), or
    unknown (fail closed, reported, never killed).

Storage isolation (Milestone 6.1 — compatibility recovery):
  - A Jarvis-*owned* (spawned) server runs against an isolated runtime
    directory, never the shared `~/.local/share/opencode` / `~/.config/opencode`
    OpenCode Desktop uses. This was verified necessary: the shared database
    accumulates rows from mixed OpenCode versions and violates a
    `session_message.seq NOT NULL` constraint on every API-submitted prompt,
    before any model call (see SESSION.md, Milestone 6 Phase 1/3, and the
    Milestone 6.1 compatibility-recovery section for the isolated-storage
    fix and verification evidence).
  - Isolation uses `XDG_DATA_HOME`/`XDG_CONFIG_HOME`/`XDG_CACHE_HOME`/
    `XDG_STATE_HOME` — experimentally verified against the installed
    `opencode debug paths` command (not officially documented for OpenCode,
    but deterministic, repeatable, and confirmed via direct CLI behavior;
    `OPENCODE_CONFIG_DIR`/`OPENCODE_CONFIG` *are* officially documented but
    control a different, additive thing — an extra agents/commands/modes/
    plugins search path — not the base config/data directory `debug paths`
    reports, so they are not used here).
  - Only applied when Jarvis actually spawns its own server. Attaching to an
    already-running external server (`classification == HEALTHY_EXTERNAL`)
    never receives or requires these overrides — that server's storage is
    whatever its own operator configured, not Jarvis's concern.
  - Never touches OpenCode Desktop's storage: different environment
    variables entirely, applied only to the spawned subprocess's own
    environment dict, never mutating the parent process environment for
    the Jarvis process itself or anything else on the machine.

Credential/cost isolation (Milestone 9B.0 — recon fix):
  - Storage isolation alone does not stop the spawned server from
    inheriting ambient provider credentials (e.g. a machine-wide
    OPENAI_API_KEY) if the subprocess env is built as a full copy of the
    parent process environment.
    Verified real: an inherited OPENAI_API_KEY caused an isolated-runtime
    session to actually run gpt-5.3-chat-latest/gpt-5-nano via OpenAI,
    silently, despite only an OpenRouter key ever being provisioned into
    the isolated auth.json.
  - Fixed by building the owned-spawn subprocess env from an explicit
    OS-essential allowlist (`isolated_subprocess_env`/
    `config.OS_ESSENTIAL_ENV_VARS`) instead of inheriting the full ambient
    environment. The isolated server's own provider credential comes only
    from its isolated auth.json (ensure_isolated_runtime_provisioned),
    never from the process environment at spawn time.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from app import config
from app.integrations import process_utils
from app.integrations import owner_marker

logger = logging.getLogger(__name__)

OPENCODE_EXE = config.opencode_exe_default()

OWNER_MARKER_PATH = config.opencode_owner_marker_default()


def default_runtime_dir() -> str:
    """Outside the repository by default, never committed to Git."""
    return config.opencode_runtime_dir()


def resolve_runtime_dir() -> str:
    return config.opencode_runtime_dir()


def isolated_env_overrides(runtime_dir: str) -> dict:
    """The verified-effective isolation variables for a Jarvis-owned server.
    Applied to a subprocess env dict only — never to the parent process
    environment directly."""
    return {
        "XDG_DATA_HOME": os.path.join(runtime_dir, "data"),
        "XDG_CONFIG_HOME": os.path.join(runtime_dir, "config"),
        "XDG_CACHE_HOME": os.path.join(runtime_dir, "cache"),
        "XDG_STATE_HOME": os.path.join(runtime_dir, "state"),
    }


def isolated_subprocess_env(isolation_env: dict, password: str) -> dict:
    """Build the full env dict for a Jarvis-owned opencode serve subprocess.

    Allowlist, not a full copy of the parent process environment — see
    config.OS_ESSENTIAL_ENV_VARS for the variable list."""
    env = config.os_essential_subprocess_env()
    env["OPENCODE_SERVER_PASSWORD"] = password
    env.update(isolation_env)
    return env


def ensure_isolated_runtime_provisioned(runtime_dir: str) -> None:
    """Create the isolated runtime's config, if missing, and provision
    provider credentials from environment (never from OpenCode Desktop's
    auth.json, never hardcoded, never logged). Idempotent: never overwrites
    a pre-existing isolated auth.json or config — if an operator has already
    customized the isolated runtime, that customization is respected.
    """
    config_path = os.path.join(runtime_dir, "config", "opencode", "opencode.jsonc")
    if not os.path.exists(config_path):
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"$schema": "https://opencode.ai/config.json"}, f)

    auth_path = os.path.join(runtime_dir, "data", "opencode", "auth.json")
    if not os.path.exists(auth_path):
        key = config.opencode_openrouter_key() or config.llm_api_key()
        if key:
            os.makedirs(os.path.dirname(auth_path), exist_ok=True)
            with open(auth_path, "w", encoding="utf-8") as f:
                json.dump({"openrouter": {"type": "api", "key": key}}, f)
            logger.info("Provisioned isolated OpenCode runtime provider auth from environment (key not logged)")
        else:
            logger.warning(
                "No JARVIS_OPENCODE_OPENROUTER_KEY or JARVIS_LLM_API_KEY set — isolated OpenCode "
                "runtime will have no provider credentials until one is configured"
            )


class OpenCodeServerError(Exception):
    """Raised when the opencode server fails to start, or an existing
    listener at the configured port cannot be safely used."""


class ServerClassification:
    NONE = "none"                    # nothing listening; safe to spawn
    HEALTHY_EXTERNAL = "healthy_external"      # responds correctly, not ours; never kill
    HEALTHY_OWNED_STALE = "healthy_owned_stale"  # responds correctly, provably our prior orphan; safe to clean
    AUTH_MISMATCH = "auth_mismatch"  # listening, wrong credentials; never kill
    INCOMPATIBLE = "incompatible"    # listening, wrong/unexpected API shape; never kill
    UNRESPONSIVE = "unresponsive"    # listening (TCP) but not answering HTTP; unknown, fail closed
    UNKNOWN = "unknown"              # any other ambiguous case; fail closed


def _read_owner_marker() -> dict | None:
    return owner_marker.read_owner_marker(OWNER_MARKER_PATH)


def _write_owner_marker(pid: int, port: int) -> None:
    owner_marker.write_owner_marker(OWNER_MARKER_PATH, pid, port)


def _clear_owner_marker() -> None:
    owner_marker.clear_owner_marker(OWNER_MARKER_PATH)


async def classify_existing_server(port: int, base_url: str, auth_header: str) -> tuple[str, dict]:
    """Determine what (if anything) is already listening at base_url/port.

    Returns (classification, diagnostics). diagnostics never includes the
    auth header value itself.
    """
    listening_pid = await process_utils.find_listening_pid(port)
    if listening_pid is None:
        return ServerClassification.NONE, {}

    diagnostics: dict = {"port": port, "listening_pid": listening_pid}

    identity = await process_utils.get_process_identity(listening_pid)
    if identity:
        diagnostics["process_name"] = identity.get("name")
        diagnostics["process_path"] = identity.get("executable_path")

    import httpx
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base_url}/global/health", headers={"Authorization": auth_header}, timeout=5)
    except Exception as e:
        diagnostics["probe_error"] = str(e)
        return ServerClassification.UNRESPONSIVE, diagnostics

    diagnostics["health_status_code"] = r.status_code

    if r.status_code == 401:
        return ServerClassification.AUTH_MISMATCH, diagnostics

    if r.status_code != 200:
        return ServerClassification.INCOMPATIBLE, diagnostics

    try:
        body = r.json()
    except Exception:
        return ServerClassification.INCOMPATIBLE, diagnostics

    if not isinstance(body, dict) or not body.get("healthy"):
        return ServerClassification.INCOMPATIBLE, diagnostics

    diagnostics["version"] = body.get("version")

    marker = _read_owner_marker()
    if marker and marker.get("port") == port and marker.get("pid") == listening_pid:
        return ServerClassification.HEALTHY_OWNED_STALE, diagnostics

    return ServerClassification.HEALTHY_EXTERNAL, diagnostics


class OpenCodeServerManager:
    """Manages a single `opencode serve` subprocess with explicit ownership."""

    def __init__(self, port: int = 4097, project_dir: str | None = None):
        self.port = port
        self.project_dir = project_dir or os.getcwd()
        self._proc: asyncio.subprocess.Process | None = None
        self._owned_pid: int | None = None
        self.owned: bool = False
        self._health_task: asyncio.Task | None = None
        self._stopped = False
        # Storage isolation for a Jarvis-owned server (see module docstring).
        # Resolved lazily in start() so JARVIS_OPENCODE_RUNTIME_DIR can still
        # be set right up until startup (e.g. by tests).
        self.runtime_dir: str | None = None
        # Control Center observability (Milestone 9B.10) -- last time
        # check_health() actually ran, regardless of the result.
        self.last_health_check_at: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def password(self) -> str:
        return config.opencode_server_password()

    @property
    def username(self) -> str:
        return config.opencode_server_username()

    @property
    def auth_header(self) -> str:
        import base64
        raw = f"{self.username}:{self.password}"
        return "Basic " + base64.b64encode(raw.encode()).decode()

    async def start(self) -> None:
        if self._proc is not None or self.owned or (self._owned_pid is not None):
            return
        self._stopped = False

        classification, diagnostics = await classify_existing_server(self.port, self.base_url, self.auth_header)
        logger.info("OpenCode server startup diagnostics: classification=%s port=%s", classification, self.port)

        if classification == ServerClassification.HEALTHY_EXTERNAL:
            logger.info("Compatible external OpenCode server detected on port %s (pid=%s) — attaching, ownership=false",
                        self.port, diagnostics.get("listening_pid"))
            self.owned = False
            self._owned_pid = None
            return

        if classification == ServerClassification.HEALTHY_OWNED_STALE:
            stale_pid = diagnostics["listening_pid"]
            logger.warning("Stale OpenCode server from a prior Jarvis run detected (pid=%s) — cleaning it up", stale_pid)
            await process_utils.kill_process_tree(stale_pid, force=False)
            if not await process_utils.wait_port_released(self.port, timeout=8):
                await process_utils.kill_process_tree(stale_pid, force=True)
                await process_utils.wait_port_released(self.port, timeout=5)
            _clear_owner_marker()
            # fall through to spawn a fresh instance below

        elif classification == ServerClassification.AUTH_MISMATCH:
            raise OpenCodeServerError(
                f"Something is already listening on port {self.port} but rejected our credentials "
                f"(HTTP 401). Not attaching or killing it. Diagnostics: pid={diagnostics.get('listening_pid')}, "
                f"process={diagnostics.get('process_name')}. Stop it manually or change JARVIS_OPENCODE_PORT."
            )

        elif classification == ServerClassification.INCOMPATIBLE:
            raise OpenCodeServerError(
                f"Something is already listening on port {self.port} but does not behave like a "
                f"compatible OpenCode server (HTTP {diagnostics.get('health_status_code')}). Not attaching or "
                f"killing it. Diagnostics: pid={diagnostics.get('listening_pid')}, process={diagnostics.get('process_name')}. "
                f"Stop it manually or change JARVIS_OPENCODE_PORT."
            )

        elif classification in (ServerClassification.UNRESPONSIVE, ServerClassification.UNKNOWN):
            raise OpenCodeServerError(
                f"Something is listening on port {self.port} but did not respond usably to a health "
                f"check ({classification}). Failing closed — not attaching or killing it. "
                f"Diagnostics: pid={diagnostics.get('listening_pid')}, process={diagnostics.get('process_name')}, "
                f"error={diagnostics.get('probe_error')}. Stop it manually or change JARVIS_OPENCODE_PORT."
            )

        self.runtime_dir = resolve_runtime_dir()
        ensure_isolated_runtime_provisioned(self.runtime_dir)
        isolation_env = isolated_env_overrides(self.runtime_dir)
        logger.info("OpenCode storage isolation enabled: runtime_dir=%s (owned server only; "
                    "external attachment is never isolated)", self.runtime_dir)

        logger.info("Starting opencode serve on port %s (cwd=%s)", self.port, self.project_dir)
        self._proc = await asyncio.create_subprocess_exec(
            OPENCODE_EXE, "serve", "--port", str(self.port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.project_dir,
            env=isolated_subprocess_env(isolation_env, self.password),
        )
        await self._wait_for_healthy(timeout=30)

        # The PID asyncio tracked may not be the real long-running server
        # (confirmed: opencode.exe's Bun launcher spawns a child and exits).
        # Resolve ground truth from what's actually listening on the port.
        resolved_pid = await process_utils.find_listening_pid(self.port)
        if resolved_pid is None:
            raise OpenCodeServerError("opencode serve reported healthy but nothing is listening on its port")
        self._owned_pid = resolved_pid
        self.owned = True
        _write_owner_marker(resolved_pid, self.port)

        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("opencode serve is healthy (owned_pid=%s, launcher_pid=%s)", resolved_pid, self._proc.pid)

    async def _wait_for_healthy(self, timeout: int = 30) -> None:
        import httpx
        async with httpx.AsyncClient() as client:
            for i in range(timeout):
                if self._stopped:
                    raise OpenCodeServerError("Server stopped during startup")
                try:
                    r = await client.get(
                        f"{self.base_url}/global/health",
                        headers={"Authorization": self.auth_header},
                        timeout=3,
                    )
                    if r.status_code == 200 and r.json().get("healthy"):
                        return
                except Exception:
                    pass
                await asyncio.sleep(1)
        raise OpenCodeServerError("Server did not become healthy in time")

    async def stop(self) -> None:
        logger.info("opencode server shutdown initiated: owned=%s owned_pid=%s", self.owned, self._owned_pid)
        self._stopped = True
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

        if not self.owned:
            logger.info("OpenCode server not owned by this process — leaving it running")
            self._proc = None
            self._owned_pid = None
            return

        if self._owned_pid is None and self._proc is None:
            return

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.base_url}/global/dispose",
                    headers={"Authorization": self.auth_header},
                    timeout=5,
                )
        except Exception:
            pass

        await asyncio.sleep(1.5)
        released = await process_utils.wait_port_released(self.port, timeout=3)

        if not released and self._owned_pid is not None:
            logger.info("Graceful dispose did not release port %s — terminating owned pid=%s", self.port, self._owned_pid)
            await process_utils.kill_process_tree(self._owned_pid, force=False)
            released = await process_utils.wait_port_released(self.port, timeout=5)

        if not released and self._owned_pid is not None:
            logger.warning("Owned OpenCode server (pid=%s) still alive — forcing termination", self._owned_pid)
            await process_utils.kill_process_tree(self._owned_pid, force=True)
            released = await process_utils.wait_port_released(self.port, timeout=5)

        # Best-effort cleanup of the launcher handle too, harmless if already dead.
        if self._proc is not None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                    await asyncio.wait_for(self._proc.wait(), timeout=2)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass

        if released:
            logger.info("opencode serve stopped, port %s released", self.port)
            _clear_owner_marker()
        else:
            logger.error("opencode serve (pid=%s) did NOT release port %s after escalation", self._owned_pid, self.port)

        self._proc = None
        self._owned_pid = None
        self.owned = False

    @property
    def is_alive(self) -> bool:
        if self.owned:
            return self._owned_pid is not None
        return self._proc is not None and self._proc.returncode is None

    async def check_health(self) -> bool:
        """Live health check, correct for both ownership cases.

        Control Center finding (Milestone 9B.10): `is_alive` alone is not
        a usable "is OpenCode healthy" signal for an *attached* (owned=False)
        server, which is the real deployment's actual state — the attach
        path in start() never sets self._proc, so `is_alive` always
        returned False for a genuinely healthy, actively-used external
        server. This does the same real HTTP check start()/_wait_for_healthy()
        already do, works for both ownership cases, and records when it
        last ran for observability (Control Center "Last Health Check").
        """
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{self.base_url}/global/health",
                    headers={"Authorization": self.auth_header},
                    timeout=3,
                )
                alive = r.status_code == 200 and bool(r.json().get("healthy"))
        except Exception:
            alive = False
        self.last_health_check_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return alive

    async def _health_loop(self) -> None:
        import httpx
        async with httpx.AsyncClient() as client:
            while not self._stopped:
                try:
                    r = await client.get(
                        f"{self.base_url}/global/health",
                        headers={"Authorization": self.auth_header},
                        timeout=5,
                    )
                    if r.status_code != 200 or not r.json().get("healthy"):
                        logger.warning("opencode serve health check failed")
                except Exception:
                    logger.warning("opencode serve unreachable, scheduling restart")
                    await self._restart()
                await asyncio.sleep(15)

    async def _restart(self) -> None:
        logger.info("Restarting opencode serve...")
        await self.stop()
        await self.start()
