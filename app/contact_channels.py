"""Contact-channel abstraction (Milestone 8 Phase 15).

Prevents AttentionManager from being hardcoded to PWA push. Each channel
implements attempt_contact(); a future native transport (Android, phone
call, SMS) can plug in here without AttentionManager changes. Only
IN_APP/PUSH/VOICE_SESSION are implemented — NATIVE_ANDROID/PHONE_CALL/SMS
are reserved names, deliberately not implemented (Milestone 9 boundary).

Every channel reuses app.notifications.notify() for the actual Notification
artifact/push delivery — this module does not replace or duplicate that
system, it orchestrates *when* and *whether* to invoke it, plus records the
outcome on a ContactAttempt row so AttentionManager has an accurate history
independent of Notification's own dedup bookkeeping.
"""
import logging

from app import db_async as adb
from app import notifications, attention_policy

logger = logging.getLogger(__name__)

CHANNEL_IN_APP = "IN_APP"
CHANNEL_PUSH = "PUSH"
CHANNEL_VOICE_SESSION = "VOICE_SESSION"
# Reserved, not implemented in Milestone 8 (Milestone 9 boundary):
CHANNEL_NATIVE_ANDROID = "NATIVE_ANDROID"
CHANNEL_PHONE_CALL = "PHONE_CALL"
CHANNEL_SMS = "SMS"

_KIND_BY_ATTENTION_TYPE = {
    "QUESTION": attention_policy.KIND_QUESTION_CREATED,
    "PERMISSION": attention_policy.KIND_PERMISSION_CREATED,
    "TASK_FAILURE": attention_policy.KIND_TASK_FAILED,
    "SUPERVISOR_ESCALATION": attention_policy.KIND_SUPERVISOR_ALERT,
}

_TITLES = {
    "QUESTION": "Jarvis needs your answer",
    "PERMISSION": "Jarvis needs a permission decision",
    "TASK_FAILURE": "Jarvis task failed",
    "SUPERVISOR_ESCALATION": "Jarvis needs your attention",
}


class ContactChannel:
    channel_type: str = ""

    def can_contact(self, attention_row: dict, *, connected: bool) -> bool:
        raise NotImplementedError

    async def attempt_contact(self, conn_manager, attention_row: dict, contact_attempt_id: str) -> dict:
        """Returns {"status", "result", "error_code", "notification_id"} —
        status is one of the ContactAttempt STATUS values (Phase 5)."""
        raise NotImplementedError


async def _deliver_via_notifications(conn_manager, attention_row: dict) -> dict | None:
    kind = _KIND_BY_ATTENTION_TYPE.get(attention_row["attention_type"])
    if not kind:
        return None
    return await notifications.notify(
        conn_manager, kind,
        conversation_id=attention_row.get("conversation_id"),
        task_id=attention_row.get("task_id"),
        source_type=attention_row["source_type"],
        source_id=attention_row["source_id"],
        title=_TITLES.get(attention_row["attention_type"], "Jarvis needs your attention"),
        # Phase 15: summary must already be lock-screen-safe/generic by the
        # time it reaches here — AttentionManager is responsible for never
        # putting raw paths/question text into it (see app/worker_events.py).
        body=attention_row["summary"],
    )


class InAppChannel(ContactChannel):
    channel_type = CHANNEL_IN_APP

    def can_contact(self, attention_row, *, connected):
        return connected

    async def attempt_contact(self, conn_manager, attention_row, contact_attempt_id):
        row = await _deliver_via_notifications(conn_manager, attention_row)
        if row is None:
            return {"status": "failed", "result": "policy_declined", "error_code": None, "notification_id": None}
        return {"status": "delivered", "result": "broadcast_ok", "error_code": None, "notification_id": row["notification_id"]}


class PushChannel(ContactChannel):
    channel_type = CHANNEL_PUSH

    def can_contact(self, attention_row, *, connected):
        return True  # attempted regardless of live connection; the push service decides real delivery

    async def attempt_contact(self, conn_manager, attention_row, contact_attempt_id):
        from app import push as push_module

        row = await _deliver_via_notifications(conn_manager, attention_row)
        if row is None:
            return {"status": "failed", "result": "policy_declined", "error_code": None, "notification_id": None}
        # Real-phone finding (Milestone 7.1): the platform cannot prove a
        # human actually saw a push (S24 FE Doze-idle limitation). Never
        # claim more certainty than the platform provides — distinguish
        # "FCM/push service accepted it" from "user received it".
        if push_module.vapid_configured() and await adb.get_push_subscriptions():
            return {
                "status": "attempted",
                "result": "PUSH_ACCEPTED_BY_PUSH_SERVICE (delivery to device not confirmed)",
                "error_code": None,
                "notification_id": row["notification_id"],
            }
        return {
            "status": "delivered",
            "result": "in_app_broadcast_only_no_push_configured_or_subscribed",
            "error_code": None,
            "notification_id": row["notification_id"],
        }


class VoiceInvitationChannel(ContactChannel):
    """Phase 13: the architecture bridge for future proactive voice
    presence. Never auto-starts the microphone — only exposes an
    invitation state to connected foreground clients (call-style UI) and,
    for backgrounded users, still falls back to the same push mechanism."""

    channel_type = CHANNEL_VOICE_SESSION

    def can_contact(self, attention_row, *, connected):
        return True

    async def attempt_contact(self, conn_manager, attention_row, contact_attempt_id):
        try:
            await conn_manager.broadcast({
                "type": "voice_session_invitation",
                "attention_request_id": attention_row["attention_request_id"],
                "summary": attention_row["summary"],
                "task_id": attention_row["task_id"],
                "attention_type": attention_row["attention_type"],
            })
        except Exception as e:
            logger.warning("Voice invitation broadcast failed: %s", e)
        row = await _deliver_via_notifications(conn_manager, attention_row)
        return {
            "status": "attempted",
            "result": "voice_invitation_shown",
            "error_code": None,
            "notification_id": row["notification_id"] if row else None,
        }


_CHANNELS = {
    CHANNEL_IN_APP: InAppChannel,
    CHANNEL_PUSH: PushChannel,
    CHANNEL_VOICE_SESSION: VoiceInvitationChannel,
}


def get_channel(channel_type: str) -> ContactChannel:
    cls = _CHANNELS.get(channel_type)
    if not cls:
        raise ValueError(f"Unknown or unimplemented channel type: {channel_type!r}")
    return cls()
