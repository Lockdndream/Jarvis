"""ADR-023: connectivity policy definition (JOPS side) and phone-status
observation. JOPS defines and exposes the policy; the Android Companion
enforces it inside PresenceService -- not yet implemented (explicit
deferral, see ADR-023's Context: no existing Android connectivity-mode
concept, no existing settings-fetch call, and this is exactly the class
of network-state-driven background-service code that only reliably shows
its bugs on a real device, unvalidatable by Playwright).

This module only defines/persists/exposes the policy and observes the
phone's already-known connection state -- it never talks to the phone
directly, per explicit direction: no new command channel.

Does not import from app.main (circular); the ConnectionManager instance
is injected via set_connection_manager(), the same wiring pattern
app/operations.py already uses for the OpenCode supervisor.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.database import get_setting, set_setting

logger = logging.getLogger("app.operations")

router = APIRouter(prefix="/api/operations/connectivity", tags=["operations"])

VALID_MODES = {"always", "wifi_only", "manual"}
DEFAULT_MODE = "always"

_connection_manager = None


def set_connection_manager(connection_manager) -> None:
    global _connection_manager
    _connection_manager = connection_manager


async def _require_localhost(request: Request) -> None:
    """Same fail-closed gate as app/operations.py's OpenCode endpoints --
    see that module for why _require_api_token alone is not sufficient."""
    host = request.client.host if request.client else None
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Jarvis Operations is local-machine-only")


class PolicyBody(BaseModel):
    mode: str


@router.get("/policy")
async def get_policy(_=Depends(_require_localhost)):
    return {"mode": get_setting("connectivity_mode", DEFAULT_MODE)}


@router.post("/policy")
async def set_policy(body: PolicyBody, _=Depends(_require_localhost)):
    if body.mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(VALID_MODES)}")
    set_setting("connectivity_mode", body.mode)
    logger.info(
        "operations: connectivity policy set to %s", body.mode,
        extra={"action": "set_policy", "target": "connectivity_policy", "outcome": "succeeded", "mode": body.mode},
    )
    return {"mode": body.mode}


@router.get("/phone-status")
async def phone_status(_=Depends(_require_localhost)):
    """Observational only, reusing ConnectionManager's existing state
    (ADR-019's Future Evolution: Operations may reuse the Control
    Center's observability primitives as its own status-checking
    foundation). Never a command channel to the phone."""
    if _connection_manager is None:
        raise HTTPException(status_code=503, detail="Operations subsystem not wired up")
    return _connection_manager.get_stats()
