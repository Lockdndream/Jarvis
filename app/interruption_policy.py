"""InterruptionPolicy v2 (Milestone 8 Phase 6).

Evolves app/attention_policy.py's event-kind classification (which
Notification artifacts to create at all — unchanged, still authoritative
for that) into a broader, still fully deterministic decision about *how*
and *whether* to contact the user about a persisted AttentionRequest:
SILENT, IN_APP, PUSH, VOICE_WHEN_AVAILABLE, DEFER, or ESCALATE.

No speculative AI interruption scoring — Phase 6 explicitly defers that.
"""
from datetime import datetime, timezone

from app import config

ACTION_SILENT = "SILENT"
ACTION_IN_APP = "IN_APP"
ACTION_PUSH = "PUSH"
ACTION_VOICE_WHEN_AVAILABLE = "VOICE_WHEN_AVAILABLE"
ACTION_DEFER = "DEFER"
ACTION_ESCALATE = "ESCALATE"

URGENCY_URGENT = "URGENT"
URGENCY_HIGH = "HIGH"
URGENCY_NORMAL = "NORMAL"
URGENCY_LOW = "LOW"

# Deterministic backoff after a SILENT/DEFER decision, so an unresolved
# item doesn't sit forever uncontacted, but also never gets recontacted in
# a tight loop (Phase 8/9). Configurable.
DEFAULT_RETRY_MINUTES = config.attention_retry_minutes()
DEFAULT_QUIET_HOURS_RETRY_MINUTES = 15


def _in_quiet_hours(now: datetime) -> bool:
    """Deterministic, explicit server-local-time quiet hours (Phase 6).
    Off by default — JARVIS_QUIET_HOURS unset. Format: "HH:MM-HH:MM",
    e.g. "22:00-07:00" (wraps past midnight). Never silently assumes UTC —
    astimezone() converts to the server's actual local timezone."""
    configured = config.quiet_hours()
    if not configured or "-" not in configured:
        return False
    try:
        start_s, end_s = configured.split("-", 1)
        start_h, start_m = (int(x) for x in start_s.split(":"))
        end_h, end_m = (int(x) for x in end_s.split(":"))
    except ValueError:
        return False
    local_now = now.astimezone()
    minutes_now = local_now.hour * 60 + local_now.minute
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    if start <= end:
        return start <= minutes_now < end
    return minutes_now >= start or minutes_now < end


def decide(attention_row: dict, *, connected: bool, prior_contact_count: int, now: datetime | None = None) -> str:
    """Pure decision function — no I/O, no DB writes. The caller
    (app.attention_manager) is responsible for acting on the result.

    Deterministic policy table (Phase 6):
      URGENT question/permission: in-app if connected, else push, with a
        voice-session invitation offered on first contact while connected.
      IMPORTANT (default) question/permission: in-app if connected, one
        push if not — never a repeated immediate push storm.
      TASK_FAILURE: notify once, silent afterward unless escalated.
      TASK_COMPLETION / anything unrecognized: silent (routine — stays
        timeline/notification-only via the unchanged attention_policy.py
        path, never becomes an unresolved AttentionRequest at all per
        Phase 4, so this branch is mostly a defensive default).
      DEFERRED (either user-requested or quiet-hours policy-driven): no
        contact before due.
    """
    now = now or datetime.now(timezone.utc)
    attention_type = attention_row["attention_type"]
    urgency = attention_row.get("urgency", URGENCY_NORMAL)

    if attention_row["status"] == "deferred":
        return ACTION_DEFER

    if _in_quiet_hours(now) and urgency != URGENCY_URGENT:
        return ACTION_DEFER

    if attention_type in ("QUESTION", "PERMISSION"):
        if urgency == URGENCY_URGENT:
            if connected:
                return ACTION_VOICE_WHEN_AVAILABLE if prior_contact_count == 0 else ACTION_IN_APP
            return ACTION_PUSH
        if connected:
            return ACTION_IN_APP
        if prior_contact_count == 0:
            return ACTION_PUSH
        return ACTION_SILENT

    if attention_type == "TASK_FAILURE":
        if prior_contact_count == 0:
            return ACTION_IN_APP if connected else ACTION_PUSH
        return ACTION_SILENT

    if attention_type == "SUPERVISOR_ESCALATION":
        return ACTION_ESCALATE

    return ACTION_SILENT


def next_retry_delay_minutes(now: datetime | None = None) -> int:
    """How long to wait before re-evaluating a SILENT/DEFER(quiet-hours)
    decision. Bounded, configurable, never a tight loop (Phase 8.7)."""
    now = now or datetime.now(timezone.utc)
    if _in_quiet_hours(now):
        return DEFAULT_QUIET_HOURS_RETRY_MINUTES
    return DEFAULT_RETRY_MINUTES
