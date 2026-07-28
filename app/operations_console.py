"""ADR-022/ADR-023: standalone Jarvis Operations console process.

This is deliberately a *separate process* from app.main. For OpenCode,
it proxies to the real Operations API already implemented on app.main
(app/operations.py), which is where OpenCode's actual lifecycle is
owned -- this console does not talk to OpenCodeSupervisor directly,
avoiding a second, uncoordinated owner of that process.

For Jarvis itself (ADR-023), there is nothing to proxy to when Jarvis is
down, so this console manages Jarvis's process directly via
JarvisProcessManager -- reusing process_utils.py, never duplicating
lifecycle logic, and never asking Jarvis to act on itself.

Binds 127.0.0.1 only, by design -- a local-operator console, never a LAN
service, regardless of JARVIS_API_TOKEN or any other setting. Run
directly: `python -m app.operations_console`.
"""
import asyncio
import os
import time
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.jarvis_process_manager import JarvisProcessManager
from app import operation_history

operation_history.init_db()

TARGET_URL = os.environ.get("JARVIS_OPERATIONS_TARGET_URL", "https://127.0.0.1:8443")
CONSOLE_PORT = int(os.environ.get("JARVIS_OPERATIONS_PORT", "8500"))
# Loopback-only call to Jarvis's own self-signed LAN cert (see
# open_control_center.bat / README.md SS5) -- not a new external trust
# decision, since this traffic never leaves 127.0.0.1.
_VERIFY_TLS = os.environ.get("JARVIS_OPERATIONS_VERIFY_TLS", "false").lower() == "true"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_target = urlparse(TARGET_URL)
_default_cwd = os.environ.get("JARVIS_OPERATIONS_CWD", _REPO_ROOT)
_default_ssl_keyfile = os.environ.get("JARVIS_SSL_KEYFILE")
_default_ssl_certfile = os.environ.get("JARVIS_SSL_CERTFILE")
if _target.scheme == "https" and not (_default_ssl_keyfile and _default_ssl_certfile):
    # Matches open_control_center.bat's production launch invocation --
    # only applied when the target URL itself says https, so an isolated
    # http:// validation instance never picks these up.
    _default_ssl_keyfile = _default_ssl_keyfile or os.path.join(_REPO_ROOT, "certs", "jarvis-lan-key.pem")
    _default_ssl_certfile = _default_ssl_certfile or os.path.join(_REPO_ROOT, "certs", "jarvis-lan-cert.pem")

jarvis_manager = JarvisProcessManager(
    # The bind host controls what interface a *spawned* Jarvis listens on
    # (0.0.0.0 in production, for LAN/phone access) -- unrelated to
    # TARGET_URL's hostname, which is only how *this console* reaches
    # Jarvis (always via loopback, regardless of Jarvis's own bind host).
    host=os.environ.get("JARVIS_BIND_HOST", "0.0.0.0"),
    port=_target.port or (443 if _target.scheme == "https" else 80),
    base_url=TARGET_URL,
    python_exe=os.environ.get("JARVIS_VENV_PYTHON", "python"),
    cwd=_default_cwd,
    ssl_keyfile=_default_ssl_keyfile if _target.scheme == "https" else None,
    ssl_certfile=_default_ssl_certfile if _target.scheme == "https" else None,
)

app = FastAPI(title="Jarvis Operations")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "operations_static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="operations-static")


class ConfirmBody(BaseModel):
    confirm: bool = False


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def _proxy(method: str, path: str, json: dict | None = None):
    url = f"{TARGET_URL}{path}"
    try:
        async with httpx.AsyncClient(verify=_VERIFY_TLS, timeout=10) as client:
            resp = await client.request(method, url, json=json)
            try:
                body = resp.json()
            except ValueError:
                body = {"detail": resp.text}
            return resp.status_code, body
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach Jarvis at {TARGET_URL}: {e}")


def _relay(status_code: int, body: dict):
    """Propagate the upstream Jarvis Operations API's own status code
    (202 for accepted-in-progress, 200 for a plain read) rather than
    flattening every success to 200 -- the console is a proxy, not a
    second source of truth for what happened."""
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=body.get("detail", "error"))
    return JSONResponse(status_code=status_code, content=body)


async def _track_jarvis_operation(action: str, run_coro) -> None:
    """ADR-023 Phase 6: wraps a claimed Jarvis action with an Operation
    record. run_coro is the already-scheduled run_claimed_*() coroutine;
    this awaits it directly (it's already race-safe via the claim that
    happened before this was called) and reads the manager's own
    last_operation_error for the FAILED case."""
    operation_id = operation_history.create_operation("jarvis", action)
    operation_history.mark_running(operation_id)
    try:
        ok = await run_coro
        operation_history.mark_finished(
            operation_id, "SUCCEEDED" if ok else "FAILED",
            error=jarvis_manager.last_operation_error if not ok else None,
        )
    except Exception as e:
        operation_history.mark_finished(operation_id, "FAILED", error=str(e))


async def _track_opencode_operation(action: str) -> None:
    """OpenCode's actual lifecycle is owned and tracked on Jarvis's own
    side (app/operations.py's structured-logging audit trail) -- this
    console has no direct handle on that work, only the proxied HTTP
    request. To still produce a real (not fabricated) Operation record,
    poll Jarvis's own status endpoint until it reports this action
    concluded, bounded so a permanently-unreachable Jarvis doesn't leave
    an Operation stuck RUNNING forever."""
    operation_id = operation_history.create_operation("opencode", action)
    operation_history.mark_running(operation_id)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            status_code, body = await _proxy("GET", "/api/operations/opencode/status")
        except HTTPException as e:
            operation_history.mark_finished(operation_id, "FAILED", error=str(e.detail))
            return
        if status_code == 200 and body.get("last_operation") == action and body.get("last_operation_result") is not None:
            ok = body["last_operation_result"] == "success"
            operation_history.mark_finished(operation_id, "SUCCEEDED" if ok else "FAILED",
                                             error=body.get("last_operation_error") if not ok else None)
            return
        await asyncio.sleep(0.5)
    operation_history.mark_finished(operation_id, "FAILED", error="timed out waiting for OpenCode operation to conclude")


@app.get("/api/console/opencode/status")
async def opencode_status():
    return _relay(*await _proxy("GET", "/api/operations/opencode/status"))


@app.post("/api/console/opencode/start")
async def opencode_start():
    status_code, body = await _proxy("POST", "/api/operations/opencode/start")
    if status_code < 400:
        asyncio.create_task(_track_opencode_operation("start"))
    return _relay(status_code, body)


@app.post("/api/console/opencode/stop")
async def opencode_stop(body: ConfirmBody = ConfirmBody()):
    # Re-enforce confirmation at this hop too -- if the console forwarded
    # confirm=true unconditionally, it would become exactly the
    # unprotected script ADR-022's confirm=true requirement exists to
    # rule out, just moved one layer further from the operator.
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Stop requires confirm=true")
    status_code, resp_body = await _proxy("POST", "/api/operations/opencode/stop", json={"confirm": True})
    if status_code < 400:
        asyncio.create_task(_track_opencode_operation("stop"))
    return _relay(status_code, resp_body)


@app.post("/api/console/opencode/restart")
async def opencode_restart(body: ConfirmBody = ConfirmBody()):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Restart requires confirm=true")
    status_code, resp_body = await _proxy("POST", "/api/operations/opencode/restart", json={"confirm": True})
    if status_code < 400:
        asyncio.create_task(_track_opencode_operation("restart"))
    return _relay(status_code, resp_body)


# ── Jarvis lifecycle (ADR-023) -- handled directly, never proxied: when
# Jarvis is down there is nothing on the other end of a proxy call. ────

@app.get("/api/console/jarvis/status")
async def jarvis_status():
    return await jarvis_manager.snapshot_status()


@app.post("/api/console/jarvis/start", status_code=202)
async def jarvis_start():
    if not jarvis_manager.claim_start():
        raise HTTPException(status_code=409, detail=f"Jarvis is already {jarvis_manager.state.lower()}")

    asyncio.create_task(_track_jarvis_operation("start", jarvis_manager.run_claimed_start()))
    return {"action": "start", "target": "jarvis", **(await jarvis_manager.snapshot_status())}


@app.post("/api/console/jarvis/stop", status_code=202)
async def jarvis_stop(body: ConfirmBody = ConfirmBody()):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Stop requires confirm=true")
    if not jarvis_manager.claim_stop():
        raise HTTPException(status_code=409, detail=f"Jarvis is already {jarvis_manager.state.lower()}")

    asyncio.create_task(_track_jarvis_operation("stop", jarvis_manager.run_claimed_stop()))
    return {"action": "stop", "target": "jarvis", **(await jarvis_manager.snapshot_status())}


@app.post("/api/console/jarvis/restart", status_code=202)
async def jarvis_restart(body: ConfirmBody = ConfirmBody()):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Restart requires confirm=true")
    if not jarvis_manager.claim_restart():
        raise HTTPException(status_code=409, detail=f"Cannot restart while Jarvis is {jarvis_manager.state.lower()}")

    asyncio.create_task(_track_jarvis_operation("restart", jarvis_manager.run_claimed_restart()))
    return {"action": "restart", "target": "jarvis", **(await jarvis_manager.snapshot_status())}


@app.get("/api/console/operations")
async def operations_history(
    limit: int = Query(default=50, le=200),
    target: str | None = None,
    action: str | None = None,
    status: str | None = None,
):
    return operation_history.get_recent_operations(limit=limit, target=target, action=action, status=status)


# ── Connectivity policy (ADR-023 Phase 2/3) -- proxied to Jarvis, same
# reasoning as OpenCode: the policy is persisted in Jarvis's own DB. ────

class PolicyBody(BaseModel):
    mode: str


@app.get("/api/console/connectivity/policy")
async def connectivity_policy_get():
    return _relay(*await _proxy("GET", "/api/operations/connectivity/policy"))


@app.post("/api/console/connectivity/policy")
async def connectivity_policy_set(body: PolicyBody):
    status_code, resp_body = await _proxy("POST", "/api/operations/connectivity/policy", json={"mode": body.mode})
    if status_code < 400:
        # Synchronous, no in-progress phase to poll for -- record settled
        # immediately, unlike the async start/stop/restart operations.
        operation_id = operation_history.create_operation("connectivity_policy", "set_policy", detail={"mode": body.mode})
        operation_history.mark_running(operation_id)
        operation_history.mark_finished(operation_id, "SUCCEEDED")
    return _relay(status_code, resp_body)


@app.get("/api/console/connectivity/phone-status")
async def connectivity_phone_status():
    return _relay(*await _proxy("GET", "/api/operations/connectivity/phone-status"))


async def _proxy_or_none(path: str):
    """Like _proxy(), but degrades to None instead of raising when Jarvis
    is unreachable -- used only by the dashboard aggregator, where one
    subsystem being down (most commonly: Jarvis itself) must not prevent
    reporting on the ones that are up (Jarvis's own console-tracked
    state is always available regardless)."""
    try:
        status_code, body = await _proxy("GET", path)
        return body if status_code < 400 else None
    except HTTPException:
        return None


@app.get("/api/console/dashboard")
async def dashboard():
    """ADR-023 Phase 4/7: one call for the whole Operations dashboard --
    Jarvis's own lifecycle state is always available (console-tracked,
    independent of whether Jarvis is reachable); everything Jarvis-side
    degrades to null, not a failed request, when Jarvis is down."""
    jarvis_status = await jarvis_manager.snapshot_status()
    jarvis_side = await _proxy_or_none("/api/operations/status")
    policy = await _proxy_or_none("/api/operations/connectivity/policy")
    phone = await _proxy_or_none("/api/operations/connectivity/phone-status")
    return {
        "jarvis": jarvis_status,
        "opencode": (jarvis_side or {}).get("opencode"),
        "supervisor": (jarvis_side or {}).get("supervisor"),
        "websocket": (jarvis_side or {}).get("websocket"),
        "connectivity_mode": (policy or {}).get("mode"),
        "phone": phone,
    }


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=CONSOLE_PORT)


if __name__ == "__main__":
    main()
