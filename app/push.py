"""Web Push delivery (Milestone 7 Phase 8).

Best-effort only: a failed or unconfigured push delivery never alters the
underlying task/question/notification state (Phase 8 requirement 10) —
foreground delivery via the existing WebSocket broadcast (see
app/notifications.py) is the primary, always-attempted channel; push is an
additional proactive channel for when the phone is backgrounded or the app
is closed.

Payloads intentionally carry minimal information (Phase 15): a short title
and a truncated body, never raw task stdout, never a full instruction that
might contain a sensitive filesystem path. Full detail is only available
inside the authenticated app after the notification is opened.

Requires JARVIS_VAPID_PUBLIC_KEY / JARVIS_VAPID_PRIVATE_KEY to be set (see
scripts/generate_vapid_keys.py). If unset, every function here is a no-op —
this is the expected, supported state for a prototype that has not opted
into push (see SESSION.md Milestone 7 Phase 8 for the "no fake background
push" decision).
"""
import asyncio
import json
import logging
import os

import app.database as db

logger = logging.getLogger(__name__)

_MAX_TITLE_LEN = 80
_MAX_BODY_LEN = 120


class _SubscriptionExpired(Exception):
    pass


def vapid_configured() -> bool:
    return bool(os.environ.get("JARVIS_VAPID_PRIVATE_KEY")) and bool(os.environ.get("JARVIS_VAPID_PUBLIC_KEY"))


def get_vapid_public_key() -> str | None:
    """Safe to expose to any client — this is the *public* half of the
    VAPID keypair, by design (browsers use it to verify a push subscription
    is being created on behalf of this server, and that later push messages
    actually originate from it)."""
    return os.environ.get("JARVIS_VAPID_PUBLIC_KEY")


async def send_push_to_all(
    title: str,
    body: str,
    notification_type: str,
    task_id: str | None,
    conversation_id: str | None,
    notification_id: str,
) -> int:
    """Best-effort push to every stored subscription. Returns the count of
    successful deliveries (0 if push is unconfigured or there are no
    subscriptions — never an error in that case). Expired/invalid
    subscriptions (HTTP 404/410 from the push service) are removed
    automatically; every other failure is logged and otherwise ignored."""
    if not vapid_configured():
        logger.debug("Push not configured (no VAPID keys set) — foreground delivery already attempted, skipping push")
        return 0

    subscriptions = db.get_push_subscriptions()
    if not subscriptions:
        return 0

    payload = json.dumps({
        "title": (title or "Jarvis")[:_MAX_TITLE_LEN],
        "body": (body or "")[:_MAX_BODY_LEN],
        "notification_type": notification_type,
        "notification_id": notification_id,
        "task_id": task_id,
        "conversation_id": conversation_id,
    })

    delivered = 0
    for sub in subscriptions:
        try:
            await asyncio.to_thread(_send_one, sub, payload)
            delivered += 1
        except _SubscriptionExpired:
            logger.info("Push subscription expired/invalid, removing endpoint (not logged in full)")
            db.delete_push_subscription(sub["endpoint"])
        except Exception as e:
            logger.warning("Push delivery to one subscription failed (ignored, underlying state unaffected): %s", e)
    return delivered


_PUSH_TTL_SECONDS = 12 * 60 * 60  # 12h — see real-phone finding below

def _send_one(sub: dict, payload: str) -> None:
    from pywebpush import webpush, WebPushException

    subscription_info = {
        "endpoint": sub["endpoint"],
        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
    }
    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=os.environ["JARVIS_VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": os.environ.get("JARVIS_VAPID_SUBJECT", "mailto:admin@localhost")},
            # Real-phone finding (Milestone 7 Phase 18, 2026-07-09): pywebpush
            # defaults to ttl=0, which per the Web Push spec means "attempt
            # delivery once, drop it rather than retry" if the push service
            # can't reach the device immediately. A non-zero TTL lets the
            # push service hold and retry instead of silently discarding it.
            ttl=_PUSH_TTL_SECONDS,
            # Second real-phone finding: TTL alone did not fix it — a real
            # backgrounded, battery-unrestricted installed PWA still only
            # displayed the notification after being foregrounded. That
            # pattern (delivered-but-deferred until user activity) matches
            # Android Doze mode holding *normal*-priority FCM messages for
            # the next maintenance window. RFC 8030's Urgency header is
            # exactly the push-protocol lever for this — "high" urgency is
            # mapped by FCM to a high-priority (Doze-bypassing) message.
            headers={"Urgency": "high"},
        )
    except WebPushException as e:
        response = getattr(e, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
        if status in (404, 410):
            raise _SubscriptionExpired(str(e))
        raise
