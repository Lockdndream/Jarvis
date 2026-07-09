from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class EventType:
    USER_MESSAGE = "user_message"
    COMMAND_STARTED = "command_started"
    COMMAND_OUTPUT = "command_output"
    COMMAND_COMPLETED = "command_completed"
    COMMAND_FAILED = "command_failed"
    SYSTEM_MESSAGE = "system_message"
    HISTORY = "history"
    TASK_STARTED = "task_started"
    TASK_STDOUT = "task_stdout"
    TASK_STDERR = "task_stderr"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    RUNNING_TASKS = "running_tasks"
    QUESTION_ASKED = "question_asked"
    QUESTION_ANSWERED = "question_answered"
    QUESTION_CANCELLED = "question_cancelled"
    PENDING_QUESTIONS = "pending_questions"


class TaskStatus:
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # OpenCode tasks only (Milestone 6): state is unknown/unverifiable, e.g.
    # after a Jarvis restart where the prior session's true native state
    # cannot yet be reconciled. Never silently reported as completed or
    # failed. See OpenCodeSupervisor.reconcile_on_startup().
    DEGRADED = "degraded"


class QuestionStatus:
    PENDING = "pending"
    ANSWERED = "answered"
    CANCELLED = "cancelled"


class NotificationType:
    """Milestone 7. Always derived from verified/persisted state — see
    app/attention_policy.py and app/notifications.py."""
    QUESTION_REQUIRED = "QUESTION_REQUIRED"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    TASK_FAILED = "TASK_FAILED"
    TASK_COMPLETED = "TASK_COMPLETED"
    SUPERVISOR_ALERT = "SUPERVISOR_ALERT"


class NotificationPriority:
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


@dataclass
class Event:
    type: str
    content: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    def to_dict(self):
        return {"type": self.type, "timestamp": self.timestamp, "content": self.content}
