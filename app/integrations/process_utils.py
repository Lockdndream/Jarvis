"""Windows-safe process identity and termination helpers.

`asyncio.create_subprocess_exec` on Windows tracks the PID of the process it
launched — but some binaries (confirmed for `opencode.exe serve`, launched via
Bun) spawn a child and exit almost immediately, leaving the real long-running
process as a parentless orphan one or two levels deeper in the process tree.
Terminating the originally-tracked PID is then a silent no-op: it's already
dead by the time `terminate()`/`kill()` runs.

These helpers resolve process identity by what's actually listening on a
port (ground truth) rather than trusting a launcher's PID, and terminate by
PID + process-tree (`taskkill /T`), never by process-name matching — so an
unrelated process that happens to be named `opencode.exe` (e.g. OpenCode
Desktop, or an unrelated CLI session) is never touched.
"""
import asyncio
import logging
import re

logger = logging.getLogger(__name__)


async def find_listening_pid(port: int) -> int | None:
    """Return the PID currently LISTENING on 127.0.0.1:<port>, or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "netstat", "-ano",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except (asyncio.TimeoutError, OSError):
        return None

    for line in out.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto, local_addr, _foreign, state = parts[0], parts[1], parts[2], parts[3]
        if proto != "TCP" or state != "LISTENING":
            continue
        m = re.match(r"^(127\.0\.0\.1|0\.0\.0\.0|\[::1?\]|::):(\d+)$", local_addr)
        if not m or int(m.group(2)) != port:
            continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return None


async def get_process_identity(pid: int) -> dict | None:
    """Return {pid, parent_pid, name, executable_path, command_line} for a
    live PID, or None if the process no longer exists. Read-only; never
    modifies process state.
    """
    script = (
        f"Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" | "
        "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except (asyncio.TimeoutError, OSError):
        return None

    raw = out.decode("utf-8", errors="replace").strip()
    if not raw:
        return None
    import json
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not data:
        return None
    return {
        "pid": data.get("ProcessId"),
        "parent_pid": data.get("ParentProcessId"),
        "name": data.get("Name"),
        "executable_path": data.get("ExecutablePath"),
        "command_line": data.get("CommandLine"),
    }


def looks_like_opencode_serve(identity: dict | None, port: int) -> bool:
    """Best-effort identity check before we terminate anything by PID.

    Never a substitute for PID-based targeting — only used to avoid acting on
    a process that merely happens to occupy the expected PID/port.
    """
    if not identity:
        return False
    cmd = (identity.get("command_line") or "").lower()
    name = (identity.get("name") or "").lower()
    return "opencode" in name and "serve" in cmd and str(port) in cmd


async def kill_process_tree(pid: int, force: bool = False) -> bool:
    """Terminate a process and its full descendant tree by PID via taskkill.

    Never matches by process name. Returns True if taskkill reported success
    or the process was already gone (exit code 128); False on real failure.
    """
    args = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        args.append("/F")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=15)
    except (asyncio.TimeoutError, OSError) as e:
        logger.warning("taskkill failed to run for pid=%s: %s", pid, e)
        return False

    if proc.returncode in (0, 128):  # 128 = "not found" (already gone)
        return True
    logger.warning("taskkill exited %s for pid=%s: %s", proc.returncode, pid, err.decode("utf-8", errors="replace")[:300])
    return False


async def wait_port_released(port: int, timeout: float = 10.0, interval: float = 0.5) -> bool:
    """Poll until nothing is LISTENING on the port, or timeout. Returns True
    if released within the timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await find_listening_pid(port) is None:
            return True
        await asyncio.sleep(interval)
    return await find_listening_pid(port) is None
