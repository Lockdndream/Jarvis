"""Build compact supervisor context from deterministic state.

Context includes only information needed for the current conversation turn.
Limits are enforced on events, conversation history, and text length.
No secrets, API keys, tokens, or environment variable values are included.
"""
import json
from datetime import datetime, timezone

import app.database as db
from app.supervisor.projects import get_projects

MAX_EVENTS = 10
MAX_CONVERSATION_TURNS = 10
MAX_TEXT_LENGTH = 2000


def build_context(conversation_history: list | None = None) -> dict:
    """Build compact context for the supervisor."""
    now = datetime.now(timezone.utc)

    tasks = db.get_recent_tasks(20)
    active_tasks = [t for t in tasks if t["status"] in ("running", "waiting_for_user")]
    recent_completed = [t for t in tasks if t["status"] in ("completed", "failed", "cancelled")][:10]

    pending_qs = db.get_pending_questions()
    opencode_running = db.get_opencode_running_tasks()

    # Recent events (filtered meaningful)
    raw_events = db.get_recent_events(50)
    filtered_events = []
    for ev in raw_events:
        ev_type = ev.get("type", "")
        if ev_type in (
            "task_started", "task_completed", "task_failed", "task_cancelled",
            "question_asked", "question_answered", "question_cancelled",
            "opencode_task_created", "opencode_task_completed", "opencode_task_cancelled",
            "opencode_question_answered", "opencode_question_rejected", "opencode_permission_handled",
        ):
            content = ev.get("content", "")
            if content:
                try:
                    parsed = json.loads(content)
                    content = str(parsed)[:200]
                except (json.JSONDecodeError, TypeError):
                    content = content[:200]
            filtered_events.append({
                "type": ev_type,
                "time": (ev.get("timestamp") or "")[11:19],
                "summary": content,
            })
        if len(filtered_events) >= MAX_EVENTS:
            break

    context = {
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "projects": _summarize_projects(),
        # "id"/"task_id" are exact-match DB keys the LLM echoes back into tool
        # calls (send_opencode_instruction, cancel_task, answer_question, ...);
        # truncating them here breaks every follow-up lookup silently.
        "active_tasks": [
            {
                "id": t["task_id"],
                "name": t["name"],
                "status": t["status"],
                "type": "opencode" if db.get_opencode_task(t["task_id"]) else "local",
            }
            for t in active_tasks
        ],
        "pending_questions": [
            {
                "id": q["question_id"],
                "task_id": q["task_id"],
                "question": q["question"][:200],
            }
            for q in pending_qs[:5]
        ],
        "opencode_running": [
            {
                "id": t["task_id"],
                "instruction": (t.get("instruction") or "")[:100],
                "status": t["status"],
            }
            for t in opencode_running[:5]
        ],
        "recent_activity": filtered_events,
    }

    approved = _check_pending_permissions()
    if approved:
        context["pending_permissions"] = approved[:3]

    context["recent_completed"] = [
        {
            "id": t["task_id"][:12],
            "name": t["name"],
            "status": t["status"],
            "exit_code": t.get("exit_code"),
        }
        for t in recent_completed
    ]

    # Conversation history (bounded)
    if conversation_history:
        context["conversation_history"] = conversation_history[-MAX_CONVERSATION_TURNS:]

    return context


def _summarize_projects() -> list[dict]:
    projects = get_projects()
    return [
        {"alias": alias, "display_name": info.get("display_name", alias)}
        for alias, info in projects.items()
    ]


def _check_pending_permissions() -> list[dict]:
    """Check for pending permission requests from OpenCode.

    Permissions are stored in the questions table with 'Permission: ' prefix.
    """
    pending = db.get_pending_questions()
    permissions = []
    for q in pending:
        if q["question"].startswith("Permission:"):
            permissions.append({
                "id": q["question_id"],
                "task_id": q["task_id"],
                "text": q["question"],
            })
    return permissions
