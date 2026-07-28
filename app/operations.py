"""ADR-022: Jarvis Operations subsystem -- OpenCode lifecycle controls.

This module owns the localhost-only, confirmation-gated, audited write
path for OpenCode's Start/Stop/Restart. It is deliberately kept separate
from the Control Center's read-only contract (ADR-018/ADR-019) -- nothing
here is reachable from or wired into Control Center code, and nothing in
the Control Center is modified to support it.

It does not import from app.main (that would be circular, since main.py
mounts this router). The running OpenCodeSupervisor instance is injected
via set_opencode_supervisor(), the same wiring pattern app.main already
uses for supervisor.set_broadcast_hook()/attention_manager.set_broadcast_hook().
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("app.operations")

router = APIRouter(prefix="/api/operations", tags=["operations"])

_opencode_supervisor = None
_connection_manager = None


def set_opencode_supervisor(supervisor) -> None:
    global _opencode_supervisor
    _opencode_supervisor = supervisor


def set_connection_manager(connection_manager) -> None:
    global _connection_manager
    _connection_manager = connection_manager


def _require_supervisor():
    if _opencode_supervisor is None:
        raise HTTPException(status_code=503, detail="Operations subsystem not wired up")
    return _opencode_supervisor


async def _require_localhost(request: Request) -> None:
    """ADR-022 security model: Operations endpoints live on the main app,
    which itself binds 0.0.0.0 for phone/dashboard access -- so this gate,
    not _require_api_token (a no-op unless JARVIS_API_TOKEN is set), is
    what keeps a destructive action from being reachable by anything on
    the LAN by default. Fails closed regardless of configuration."""
    host = request.client.host if request.client else None
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Jarvis Operations is local-machine-only")


class ConfirmBody(BaseModel):
    confirm: bool = False


def _audit(action: str, outcome: str, *, destructive: bool, error: str | None = None) -> None:
    """ADR-022 audit logging: every attempted/succeeded/failed action goes
    through structured logging (component 'operations', ADR-021), never a
    trace_id -- per ADR-020's canonical definition an operator click is
    not Supervisor-coordinated autonomous work, so no trace is invented
    for it."""
    if outcome == "failed":
        level = logger.error
    elif destructive:
        level = logger.warning
    else:
        level = logger.info
    level(
        "operations: opencode %s %s", action, outcome,
        extra={"action": action, "target": "opencode", "outcome": outcome, "error": error},
    )


@router.get("/opencode/status")
async def opencode_status(_=Depends(_require_localhost)):
    sv = _require_supervisor()
    return await sv.snapshot_status()


@router.post("/opencode/start", status_code=202)
async def start_opencode(_=Depends(_require_localhost)):
    sv = _require_supervisor()
    if not sv.claim_start():
        raise HTTPException(status_code=409, detail=f"OpenCode is already {sv.state.lower()}")
    _audit("start", "attempted", destructive=False)

    async def _run():
        ok = await sv.run_claimed_start()
        _audit("start", "succeeded" if ok else "failed", destructive=False,
               error=sv.last_operation_error if not ok else None)

    asyncio.create_task(_run())
    return {"action": "start", "target": "opencode", **(await sv.snapshot_status())}


@router.post("/opencode/stop", status_code=202)
async def stop_opencode(body: ConfirmBody = ConfirmBody(), _=Depends(_require_localhost)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Stop requires confirm=true")
    sv = _require_supervisor()
    if not sv.claim_stop():
        raise HTTPException(status_code=409, detail=f"OpenCode is already {sv.state.lower()}")
    _audit("stop", "attempted", destructive=True)

    async def _run():
        ok = await sv.run_claimed_stop()
        _audit("stop", "succeeded" if ok else "failed", destructive=True,
               error=sv.last_operation_error if not ok else None)

    asyncio.create_task(_run())
    return {"action": "stop", "target": "opencode", **(await sv.snapshot_status())}


@router.post("/opencode/restart", status_code=202)
async def restart_opencode(body: ConfirmBody = ConfirmBody(), _=Depends(_require_localhost)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Restart requires confirm=true")
    sv = _require_supervisor()
    if not sv.claim_restart():
        raise HTTPException(status_code=409, detail=f"Cannot restart while OpenCode is {sv.state.lower()}")
    _audit("restart", "attempted", destructive=True)

    async def _run():
        ok = await sv.run_claimed_restart()
        _audit("restart", "succeeded" if ok else "failed", destructive=True,
               error=sv.last_operation_error if not ok else None)

    asyncio.create_task(_run())
    return {"action": "restart", "target": "opencode", **(await sv.snapshot_status())}


@router.get("/status")
async def aggregate_status(_=Depends(_require_localhost)):
    """ADR-023 Phase 7: one call for everything the Operations dashboard
    needs about Jarvis-side subsystems (OpenCode, Supervisor, WebSocket),
    localhost-gated like every other endpoint here. Supervisor/WebSocket
    have no independent health check of their own -- they run in-process
    with Jarvis, so their health is Jarvis's own reachability (this
    endpoint responding at all *is* that signal); what's reported here is
    their current *state*, not a fabricated separate health check."""
    from app.supervisor import supervisor as supervisor_module

    sv = _require_supervisor()
    opencode_status = await sv.snapshot_status()
    supervisor_state = supervisor_module.get_supervisor_state()
    connection_stats = _connection_manager.get_stats() if _connection_manager else None
    return {
        "opencode": opencode_status,
        "supervisor": {"state": supervisor_state},
        "websocket": connection_stats,
    }
