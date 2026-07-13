"""Normalized worker-attention event shape (Milestone 8 Phase 16).

Removes OpenCode-specific assumptions from attention creation — the mock
worker and OpenCode's question/permission/failure paths both funnel
through this same normalized shape before reaching AttentionManager, which
never needs to understand OpenCode HTTP details (those stay in
app/integrations/opencode_adapter.py). This is not a new worker
integration — no new worker type is added, this only normalizes the two
that already exist.
"""
from dataclasses import dataclass, field

QUESTION_REQUIRED = "QUESTION_REQUIRED"
PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
TASK_FAILED = "TASK_FAILED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"  # reserved; not yet produced by either worker

_ATTENTION_TYPE_BY_EVENT = {
    QUESTION_REQUIRED: "QUESTION",
    PERMISSION_REQUIRED: "PERMISSION",
    TASK_FAILED: "TASK_FAILURE",
    REVIEW_REQUIRED: "SUPERVISOR_ESCALATION",
}


@dataclass
class WorkerAttentionEvent:
    worker_type: str        # "mock_worker" | "opencode"
    worker_task_id: str     # Jarvis task_id
    event_type: str         # one of the *_REQUIRED / TASK_FAILED constants above
    source_id: str          # question_id / permission request_id / task_id
    summary: str            # lock-screen-safe, generic (Phase 15) — never raw path/question text
    conversation_id: str | None = None
    options: list | None = None
    urgency: str = "HIGH"
    metadata: dict = field(default_factory=dict)

    @property
    def attention_type(self) -> str:
        return _ATTENTION_TYPE_BY_EVENT[self.event_type]

    @property
    def source_type(self) -> str:
        """Preserves the exact source_type strings already used throughout
        the pre-Milestone-8 codebase (notification dedup keys, deep links:
        "local_question", "opencode_question", "opencode_permission",
        "local_task", "opencode_task") so nothing downstream has to change."""
        if self.event_type == TASK_FAILED:
            return "opencode_task" if self.worker_type == "opencode" else "local_task"
        if self.event_type == PERMISSION_REQUIRED:
            return "opencode_permission"
        return "opencode_question" if self.worker_type == "opencode" else "local_question"


async def create_attention(conn_manager, event: WorkerAttentionEvent) -> dict:
    """The single entry point both worker integrations call — see
    app/task_manager.py::_handle_question and
    app/integrations/opencode_supervisor.py::_emit_question/_emit_permission/
    _handle_session_failed."""
    from app import attention_manager as am

    return await am.get_or_create(
        conn_manager,
        conversation_id=event.conversation_id,
        task_id=event.worker_task_id,
        source_type=event.source_type,
        source_id=event.source_id,
        attention_type=event.attention_type,
        urgency=event.urgency,
        summary=event.summary,
        context_json=None,
    )
