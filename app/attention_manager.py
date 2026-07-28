"""AttentionRequest orchestration (Milestone 8).

Core distinction — never conflate these (see SESSION.md Milestone 8):

  WorkerQuestion   the underlying worker-native request for information
                    (a row in the existing `questions` table, unchanged)
  AttentionRequest Jarvis's persistent representation that human attention
                    is required (this module; `attention_requests` table)
  ContactAttempt    an attempt to reach the user about an AttentionRequest
  Notification      one possible delivery artifact of a ContactAttempt
                    (app/notifications.py, unchanged, still the only thing
                    that creates a `notifications` row)
  VoiceSession      a bounded conversational interaction (app/voice_session_manager.py)

One worker question normally creates: 1 WorkerQuestion, 1 AttentionRequest,
N ContactAttempts, 0..N Notifications, 0..N VoiceSessions.

AttentionRequest creation must be idempotent and tied to verified source
state — never created from UI rendering, repeated polling, notification
delivery, or SSE replay. See get_or_create()'s dedup_key.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import app.database as db
from app import db_async as adb
from app import interruption_policy
from app.contact_channels import get_channel, CHANNEL_IN_APP, CHANNEL_PUSH, CHANNEL_VOICE_SESSION

logger = logging.getLogger(__name__)

# Set once at startup (app/main.py) so every transition can broadcast a
# real-time UI update without threading conn_manager through every call
# site (many of which — task_manager.py, opencode_supervisor.py,
# supervisor tools — already have their own conn_manager reference for
# other purposes; this avoids a parallel plumbing requirement just for
# attention state broadcasts). None-safe: if unset (e.g. a unit test that
# never calls set_broadcast_hook), transitions simply don't broadcast.
_broadcast_hook = None


def set_broadcast_hook(conn_manager) -> None:
    global _broadcast_hook
    _broadcast_hook = conn_manager


async def _broadcast_attention_update(attention_request_id: str, event: str) -> None:
    if _broadcast_hook is None:
        return
    row = await adb.get_attention_request(attention_request_id)
    if not row:
        return
    try:
        await _broadcast_hook.broadcast({
            "type": event,
            "attention_request_id": row["attention_request_id"],
            "status": row["status"],
            "attention_type": row["attention_type"],
            "summary": row["summary"],
            "task_id": row["task_id"],
            "deferred_until": row.get("deferred_until"),
        })
    except Exception as e:
        logger.warning("attention update broadcast failed (state unaffected): %s", e)


ATTENTION_TYPE_QUESTION = "QUESTION"
ATTENTION_TYPE_PERMISSION = "PERMISSION"
ATTENTION_TYPE_TASK_FAILURE = "TASK_FAILURE"
ATTENTION_TYPE_TASK_COMPLETION = "TASK_COMPLETION"
ATTENTION_TYPE_SUPERVISOR_ESCALATION = "SUPERVISOR_ESCALATION"

STATUS_PENDING = "pending"
STATUS_CONTACTING = "contacting"
STATUS_DEFERRED = "deferred"
STATUS_RESOLVING = "resolving"
STATUS_RESOLVED = "resolved"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

# Explicit legal-transition map: to_status -> allowed current (from) statuses.
# Enforced atomically by db.transition_attention_status()'s conditional
# UPDATE (Phase 21 concurrency protection) — a transition whose current
# status isn't in this list for the target is rejected, not guessed around.
_LEGAL_TRANSITIONS = {
    STATUS_CONTACTING: (STATUS_PENDING, STATUS_DEFERRED),
    STATUS_PENDING: (STATUS_CONTACTING, STATUS_DEFERRED, STATUS_RESOLVING),
    STATUS_DEFERRED: (STATUS_PENDING, STATUS_CONTACTING),
    STATUS_RESOLVING: (STATUS_PENDING, STATUS_CONTACTING, STATUS_DEFERRED),
    STATUS_RESOLVED: (STATUS_RESOLVING,),
    STATUS_CANCELLED: (STATUS_PENDING, STATUS_CONTACTING, STATUS_DEFERRED, STATUS_RESOLVING),
    STATUS_EXPIRED: (STATUS_PENDING, STATUS_CONTACTING, STATUS_DEFERRED),
}

_ACTION_TO_CHANNEL = {
    interruption_policy.ACTION_IN_APP: CHANNEL_IN_APP,
    interruption_policy.ACTION_PUSH: CHANNEL_PUSH,
    interruption_policy.ACTION_VOICE_WHEN_AVAILABLE: CHANNEL_VOICE_SESSION,
    interruption_policy.ACTION_ESCALATE: CHANNEL_PUSH,  # M8: escalation reuses PUSH; no separate transport yet
}


async def _transition(attention_request_id: str, to_status: str, **extra_fields) -> bool:
    froms = _LEGAL_TRANSITIONS[to_status]
    ok = await adb.transition_attention_status(attention_request_id, froms, to_status, **extra_fields)
    if not ok:
        current = await adb.get_attention_request(attention_request_id)
        logger.info(
            "attention transition rejected: id=%s to=%s current_status=%s (illegal or lost race)",
            attention_request_id, to_status, current["status"] if current else "missing",
        )
    else:
        await _broadcast_attention_update(attention_request_id, f"attention_{to_status}")
    return ok


def _connected(conn_manager) -> bool:
    """TD-029: user-facing surfaces only — dashboard observers must not count."""
    try:
        return bool(conn_manager.has_user_surfaces())
    except Exception:
        return False


async def get_or_create(
    conn_manager,
    *,
    conversation_id: str | None,
    task_id: str | None,
    source_type: str,
    source_id: str,
    attention_type: str,
    urgency: str,
    summary: str,
    context_json: str | None = None,
) -> dict:
    """Idempotent by (source_type, source_id) — the deterministic source
    correlation required by Phase 3. Safe to call more than once for the
    same underlying event (a second call finds `created=False` and neither
    re-contacts nor duplicates)."""
    dedup_key = f"{source_type}:{source_id}"
    attention_request_id = f"attn_{uuid.uuid4().hex[:12]}"
    row = await adb.create_attention_request(
        attention_request_id=attention_request_id,
        conversation_id=conversation_id,
        task_id=task_id,
        source_type=source_type,
        source_id=source_id,
        attention_type=attention_type,
        urgency=urgency,
        summary=summary,
        context_json=context_json,
        contact_policy=None,
        dedup_key=dedup_key,
    )
    logger.info(
        "attention %s: id=%s type=%s source=%s:%s task_id=%s",
        "created" if row["created"] else "deduplicated",
        row["attention_request_id"], attention_type, source_type, source_id, task_id,
    )
    if row["created"]:
        await initiate_contact(conn_manager, row)
        row = await adb.get_attention_request(row["attention_request_id"]) or row
    return row


async def initiate_contact(conn_manager, attention_row: dict) -> None:
    """Applies InterruptionPolicy and, if it calls for contact, creates one
    ContactAttempt idempotently and executes it via the chosen channel.
    Always returns the request to PENDING afterward (still unresolved, now
    waiting on the user) except when policy itself decided to defer.

    Not module-private despite no leading underscore convention change
    elsewhere in this file: app/attention_scheduler.py calls this directly
    to re-run the exact same policy-decision-and-contact flow when a
    deferred/backed-off request becomes due — "due" is just "time to
    re-evaluate InterruptionPolicy again", not a distinct code path."""
    decision = interruption_policy.decide(
        attention_row, connected=_connected(conn_manager),
        prior_contact_count=attention_row.get("contact_attempt_count", 0),
    )
    logger.info("contact policy decision: id=%s decision=%s", attention_row["attention_request_id"], decision)

    if decision == interruption_policy.ACTION_SILENT:
        delay = interruption_policy.next_retry_delay_minutes()
        await asyncio.to_thread(_schedule_retry, attention_row["attention_request_id"], delay)
        return

    if decision == interruption_policy.ACTION_DEFER:
        # Policy-driven defer (e.g. quiet hours) — distinct from a
        # user-requested defer: no deferred_until is set (that's reserved
        # for explicit user intent), just a bounded retry later.
        delay = interruption_policy.next_retry_delay_minutes()
        await asyncio.to_thread(_schedule_retry, attention_row["attention_request_id"], delay)
        return

    channel_name = _ACTION_TO_CHANNEL.get(decision)
    if not channel_name:
        logger.warning("No channel mapped for interruption decision %s — treating as silent", decision)
        return

    if not await _transition(attention_row["attention_request_id"], STATUS_CONTACTING):
        return  # lost a race (e.g. already being deferred/cancelled) — do not contact

    channel = get_channel(channel_name)
    contact_attempt_id = f"cta_{uuid.uuid4().hex[:12]}"
    await adb.create_contact_attempt(contact_attempt_id, attention_row["attention_request_id"], channel_name)
    logger.info("contact attempt planned: id=%s attention_id=%s channel=%s",
                contact_attempt_id, attention_row["attention_request_id"], channel_name)

    try:
        result = await channel.attempt_contact(conn_manager, attention_row, contact_attempt_id)
    except Exception as e:
        logger.warning("Contact attempt failed with an exception (attention state unaffected): %s", e)
        result = {"status": "failed", "result": str(e), "error_code": "exception", "notification_id": None}

    await adb.update_contact_attempt_status(
        contact_attempt_id, result["status"], result.get("result"), result.get("error_code"),
    )
    await adb.record_attention_contact(attention_row["attention_request_id"])
    logger.info("contact attempt executed: id=%s status=%s result=%s",
                contact_attempt_id, result["status"], result.get("result"))

    await _transition(attention_row["attention_request_id"], STATUS_PENDING)
    try:
        await conn_manager.broadcast({
            "type": "attention_created",
            "attention_request_id": attention_row["attention_request_id"],
            "attention_type": attention_row["attention_type"],
            "summary": attention_row["summary"],
            "task_id": attention_row["task_id"],
            "urgency": attention_row["urgency"],
        })
    except Exception as e:
        logger.warning("attention_created broadcast failed: %s", e)


def _schedule_retry(attention_request_id: str, delay_minutes: int) -> None:
    next_at = (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).isoformat().replace("+00:00", "Z")
    db.set_attention_next_contact(attention_request_id, next_at)


# ── Defer / resume ────────────────────────────────────────────────────

async def defer(attention_request_id: str, deferred_until_iso: str) -> bool:
    """User-requested deferral (Phase 7). Only affects the AttentionRequest
    — never touches the underlying WorkerQuestion, never answers or
    cancels it."""
    ok = await _transition(attention_request_id, STATUS_DEFERRED, deferred_until=deferred_until_iso, next_contact_at=None)
    if ok:
        logger.info("attention deferred: id=%s until=%s", attention_request_id, deferred_until_iso)
    return ok


async def mark_due(attention_request_id: str) -> bool:
    """Scheduler calls this when deferred_until (or next_contact_at) has
    passed — returns the request to PENDING so it gets re-evaluated by
    InterruptionPolicy on the next contact pass, exactly like a fresh
    creation would be."""
    ok = await _transition(attention_request_id, STATUS_PENDING, deferred_until=None, next_contact_at=None)
    if ok:
        logger.info("attention due: id=%s", attention_request_id)
    return ok


# ── Resolution ────────────────────────────────────────────────────────

async def begin_resolving(attention_request_id: str) -> bool:
    return await _transition(attention_request_id, STATUS_RESOLVING)


async def resolve(attention_request_id: str, resolution_type: str, resolution_value: str | None) -> bool:
    """Must only be called *after* native answer/permission delivery has
    already succeeded (Phase 2: 'do not mark resolved before native action
    succeeds'). Callers are expected to call begin_resolving() first, then
    perform the real native delivery, then resolve() only on success."""
    ok = await _transition(
        attention_request_id, STATUS_RESOLVED,
        resolved_at=db.utcnow(), resolution_type=resolution_type, resolution_value=resolution_value,
    )
    if ok:
        logger.info("attention resolved: id=%s resolution_type=%s", attention_request_id, resolution_type)
    return ok


async def fail_resolving(attention_request_id: str) -> bool:
    """Native delivery failed — return to PENDING, never RESOLVED."""
    ok = await _transition(attention_request_id, STATUS_PENDING)
    if ok:
        logger.info("attention resolution attempt failed, returned to pending: id=%s", attention_request_id)
    return ok


async def resolve_for_source(source_type: str, source_id: str, resolution_type: str, resolution_value: str | None) -> bool:
    """Convenience wrapper used by callers that already know native
    delivery succeeded (e.g. OpenCodeSupervisor.answer_question after
    adapter.reply_question() returns without raising) — looks up the
    correlated AttentionRequest and resolves it in one guarded step."""
    row = await adb.get_attention_request_by_source(source_type, source_id)
    if not row:
        return False
    if row["status"] in (STATUS_RESOLVED, STATUS_CANCELLED, STATUS_EXPIRED):
        return False
    await begin_resolving(row["attention_request_id"])
    return await resolve(row["attention_request_id"], resolution_type, resolution_value)


# ── Cancellation / invalidation (Phase 20) ─────────────────────────────

async def cancel(attention_request_id: str) -> bool:
    ok = await _transition(attention_request_id, STATUS_CANCELLED)
    if ok:
        logger.info("attention cancelled: id=%s", attention_request_id)
    return ok


async def cancel_for_task(task_id: str) -> int:
    """A task being cancelled invalidates any of its still-unresolved
    AttentionRequests (PENDING/CONTACTING/DEFERRED -> CANCELLED). Does not
    touch already-resolved/cancelled/expired ones."""
    count = 0
    for row in await adb.get_attention_requests_for_task(task_id):
        if row["status"] in (STATUS_PENDING, STATUS_CONTACTING, STATUS_DEFERRED):
            if await cancel(row["attention_request_id"]):
                count += 1
    return count


async def cancel_for_source(source_type: str, source_id: str) -> bool:
    row = await adb.get_attention_request_by_source(source_type, source_id)
    if not row:
        return False
    if row["status"] not in (STATUS_PENDING, STATUS_CONTACTING, STATUS_DEFERRED):
        return False
    return await cancel(row["attention_request_id"])


async def expire(attention_request_id: str) -> bool:
    ok = await _transition(attention_request_id, STATUS_EXPIRED)
    if ok:
        logger.info("attention expired: id=%s", attention_request_id)
    return ok
