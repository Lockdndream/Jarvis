"""Notification creation and delivery (Milestone 7 Phases 6/8/14).

The single entry point used by every producer of a verified attention
event (app/integrations/opencode_supervisor.py, app/task_manager.py).
Never called with unverified LLM prose — only from persisted
question/permission/task state (principle 3). Wraps:

  1. Classification via app.attention_policy — decides whether this kind
     of event warrants a notification at all.
  2. Idempotent DB creation keyed on dedup_key (app.database.create_notification)
     — one underlying verified event produces exactly one logical
     notification row, no matter how many times this function is called
     for it (SSE replay, reconnect, restart, retry — Phase 14).
  3. Foreground delivery via WebSocket broadcast — always attempted for a
     newly-created notification.
  4. Best-effort Web Push delivery (app.push) — only for newly-created
     notifications, only if configured.

Delivery attempts (steps 3/4) only ever run once per notification, exactly
because step 2 is idempotent: a duplicate call for the same dedup_key finds
`created=False` and returns without re-broadcasting or re-pushing.
"""
import logging
import uuid

import app.database as db
from app import attention_policy
from app import push

logger = logging.getLogger(__name__)


async def notify(
    conn_manager,
    kind: str,
    *,
    conversation_id: str | None,
    task_id: str | None,
    source_type: str,
    source_id: str | None,
    title: str,
    body: str,
) -> dict | None:
    """Classify `kind` via AttentionPolicy and, if it warrants a
    notification, create it (idempotently) and deliver it.

    Returns the notification row dict, or None if policy says this event
    is timeline-only (no notification created at all — e.g. task_started,
    or task_completed while notify_on_completion is disabled).
    """
    decision = attention_policy.decide(kind)
    if attention_policy.ACTION_NOTIFY not in decision["actions"]:
        return None

    dedup_key = f"{kind}:{source_id or task_id}"
    notification_id = f"notif_{uuid.uuid4().hex[:12]}"
    row = db.create_notification(
        notification_id=notification_id,
        conversation_id=conversation_id,
        task_id=task_id,
        source_type=source_type,
        source_id=source_id,
        notification_type=decision["notification_type"],
        title=title,
        body=body,
        priority=decision["priority"],
        dedup_key=dedup_key,
    )

    if not row["created"]:
        logger.debug("Notification dedup hit for %s — not re-delivering", dedup_key)
        return row

    payload = {
        "type": "notification",
        "notification_id": row["notification_id"],
        "notification_type": row["notification_type"],
        "title": row["title"],
        "body": row["body"],
        "priority": row["priority"],
        "conversation_id": row["conversation_id"],
        "task_id": row["task_id"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "created_at": row["created_at"],
    }
    try:
        await conn_manager.broadcast(payload)
    except Exception as e:
        logger.warning("Notification broadcast failed (notification row still persisted): %s", e)

    db.mark_notification_delivered(row["notification_id"])

    try:
        await push.send_push_to_all(
            row["title"], row["body"], row["notification_type"],
            row["task_id"], row["conversation_id"], row["notification_id"],
        )
    except Exception as e:
        logger.warning("Push delivery failed (underlying task/question state unaffected): %s", e)

    return row
