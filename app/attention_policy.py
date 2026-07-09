"""Bounded, deterministic attention policy (Milestone 7 Phase 7).

Classifies a *verified* event (never LLM prose — see module docstring in
notifications.py) into a set of actions:

  - "timeline"  — render in the event timeline only
  - "attention" — also appear in Needs Your Attention
  - "notify"    — also create a persisted Notification (proactive contact)
  - "speak"     — also eligible for spoken output while foregrounded

No LLM-based importance scoring in Milestone 7 (Phase 7 explicitly defers
this — the deterministic rule table below is deliberately simple and easy
to audit). Revisit only if this proves insufficient in practice.
"""
import os

from app.models import NotificationType, NotificationPriority

ACTION_TIMELINE = "timeline"
ACTION_ATTENTION = "attention"
ACTION_NOTIFY = "notify"
ACTION_SPEAK = "speak"

KIND_QUESTION_CREATED = "question_created"
KIND_PERMISSION_CREATED = "permission_created"
KIND_TASK_FAILED = "task_failed"
KIND_TASK_COMPLETED = "task_completed"
KIND_TASK_STARTED = "task_started"
KIND_ROUTINE_OUTPUT = "routine_output"
KIND_SUPERVISOR_ALERT = "supervisor_alert"


def notify_on_completion() -> bool:
    """Configurable policy for TASK_COMPLETED notifications (Phase 7).

    A `notify_on_completion` row in the `settings` table (client-toggleable
    via GET/POST /api/settings) always wins when present; otherwise falls
    back to the JARVIS_NOTIFY_ON_COMPLETION env var, defaulting to enabled
    — a completed task is the primary payoff of the product goal ("contact
    the user... when a task completes and the result is worth reporting"),
    so it is on by default, unlike routine progress (principle 6).
    """
    import app.database as db  # local import: avoid a hard import cycle with database at module load

    stored = db.get_setting("notify_on_completion")
    if stored is not None:
        return stored == "true"
    return os.environ.get("JARVIS_NOTIFY_ON_COMPLETION", "true").lower() in ("1", "true", "yes")


def decide(kind: str) -> dict:
    """Return {"actions": [...], "notification_type": str|None, "priority": str|None}.

    ALWAYS NOTIFY: question requiring an answer, permission requiring a
    decision, meaningful task failure.
    CONFIGURABLE: task completion (see notify_on_completion()).
    NOT NOTIFIED BY DEFAULT: task started, routine output, and anything
    unrecognized — timeline-only, per principle 6 ("Jarvis must not become
    noisy").
    """
    if kind == KIND_QUESTION_CREATED:
        return {
            "actions": [ACTION_TIMELINE, ACTION_ATTENTION, ACTION_NOTIFY, ACTION_SPEAK],
            "notification_type": NotificationType.QUESTION_REQUIRED,
            "priority": NotificationPriority.HIGH,
        }
    if kind == KIND_PERMISSION_CREATED:
        return {
            "actions": [ACTION_TIMELINE, ACTION_ATTENTION, ACTION_NOTIFY, ACTION_SPEAK],
            "notification_type": NotificationType.PERMISSION_REQUIRED,
            "priority": NotificationPriority.HIGH,
        }
    if kind == KIND_TASK_FAILED:
        return {
            "actions": [ACTION_TIMELINE, ACTION_NOTIFY, ACTION_SPEAK],
            "notification_type": NotificationType.TASK_FAILED,
            "priority": NotificationPriority.HIGH,
        }
    if kind == KIND_TASK_COMPLETED:
        if notify_on_completion():
            return {
                "actions": [ACTION_TIMELINE, ACTION_NOTIFY, ACTION_SPEAK],
                "notification_type": NotificationType.TASK_COMPLETED,
                "priority": NotificationPriority.NORMAL,
            }
        return {"actions": [ACTION_TIMELINE], "notification_type": None, "priority": None}
    if kind == KIND_SUPERVISOR_ALERT:
        return {
            "actions": [ACTION_TIMELINE, ACTION_ATTENTION, ACTION_NOTIFY, ACTION_SPEAK],
            "notification_type": NotificationType.SUPERVISOR_ALERT,
            "priority": NotificationPriority.HIGH,
        }
    # KIND_TASK_STARTED, KIND_ROUTINE_OUTPUT, SSE reconnect noise, internal
    # retries, server lifecycle events, and anything unrecognized.
    return {"actions": [ACTION_TIMELINE], "notification_type": None, "priority": None}
