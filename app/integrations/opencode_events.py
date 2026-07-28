"""Normalize OpenCode native events into Jarvis internal events."""
import logging

logger = logging.getLogger(__name__)


def normalize_question(oc_question: dict, task_id: str) -> dict | None:
    """Convert an OpenCode question object to a Jarvis question event.

    Expected OpenCode question shape (inferred from API):
        {
            "requestID": "...",
            "question": "What editor?",
            "options": ["Vim", "VS Code"],
            "context": "select tool",
            "sessionID": "..."
        }
    """
    request_id = oc_question.get("requestID") or oc_question.get("id")
    question_text = oc_question.get("question") or oc_question.get("text", "")
    if not request_id or not question_text:
        logger.warning("Skipping malformed question: %s", oc_question)
        return None

    return {
        "event": "question",
        "request_id": request_id,
        "question": question_text,
        "options": oc_question.get("options", []),
        "context": oc_question.get("context", ""),
        "task_id": task_id,
        "source": "opencode",
        "raw": oc_question,
    }


def normalize_permission(oc_permission: dict, task_id: str) -> dict | None:
    """Convert an OpenCode permission request to a Jarvis permission event.

    Expected OpenCode permission shape:
        {
            "requestID": "...",
            "action": "read",
            "path": "/some/file",
            "sessionID": "..."
        }
    """
    request_id = oc_permission.get("requestID") or oc_permission.get("id")
    action = oc_permission.get("action", "unknown")
    path = oc_permission.get("path", oc_permission.get("file", ""))
    if not request_id:
        logger.warning("Skipping malformed permission: %s", oc_permission)
        return None

    return {
        "event": "permission",
        "request_id": request_id,
        "action": action,
        "path": path,
        "task_id": task_id,
        "source": "opencode",
        "raw": oc_permission,
    }


def normalize_message(oc_message: dict, task_id: str, session_id: str) -> dict | None:
    """Convert an OpenCode chat message to a Jarvis content event."""
    role = oc_message.get("role", "unknown")
    content = oc_message.get("content", oc_message.get("text", ""))
    msg_id = oc_message.get("id", "")

    if isinstance(content, list):
        texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        content = "\n".join(texts)

    if not content:
        return None

    return {
        "event": "message",
        "message_id": msg_id,
        "role": role,
        "content": content,
        "task_id": task_id,
        "session_id": session_id,
        "source": "opencode",
        "raw": oc_message,
    }


# Real OpenCode v1.15.10 SSE frames are a `GlobalEvent` envelope:
#   {"directory": "...", "project": "...", "payload": {"id": "evt_...",
#    "type": "session.error", "properties": {...}}}
# There is no top-level SSE `event:` line — every frame is an unnamed
# `data:` line, and the discriminator lives at payload.type. (Verified via
# direct probe against the real server, 2026-07-09; the OpenAPI /doc spec's
# GlobalEvent/Event schemas confirm the full type catalog.) Any Jarvis event
# type not explicitly listed here is intentionally ignored (e.g. server
# heartbeats, TUI-only events, LSP diagnostics) rather than treated as an
# error — Phase 10 requires malformed/unrecognized events to never crash
# supervision.
ACTIVITY_EVENT_PREFIXES = ("session.next.", "message.part.", "session.diff")


def process_sse_event(sse_frame: dict, task_id: str) -> list:
    """Process a single raw SSE frame dict and return zero or more Jarvis
    events. `sse_frame` is the parsed {"event": ..., "data": ...} dict that
    OpenCodeAdapter._parse_sse produces from one `data: <json>` line."""
    envelope = sse_frame.get("data", {})
    if not isinstance(envelope, dict):
        return []

    payload = envelope.get("payload")
    if not isinstance(payload, dict) or "type" not in payload:
        return []

    event_type = payload.get("type", "")
    props = payload.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    directory = envelope.get("directory")
    session_id = props.get("sessionID", "")

    results = []

    if event_type == "question.asked":
        results.append({
            "event": "question_poll_trigger",
            "task_id": task_id,
            "session_id": session_id,
            "directory": directory,
            "source": "opencode",
        })

    elif event_type == "permission.asked":
        results.append({
            "event": "permission_poll_trigger",
            "task_id": task_id,
            "session_id": session_id,
            "directory": directory,
            "source": "opencode",
        })

    elif event_type == "session.error":
        logger.warning("OpenCode session.error for task=%s session=%s: %s", task_id, session_id, props.get("error"))
        results.append({
            "event": "session_failed",
            "task_id": task_id,
            "session_id": session_id,
            "error": props.get("error", {}),
            "source": "opencode",
            "raw": payload,
        })

    elif event_type == "session.idle":
        results.append({
            "event": "session_idle",
            "task_id": task_id,
            "session_id": session_id,
            "source": "opencode",
        })

    elif event_type.startswith(ACTIVITY_EVENT_PREFIXES):
        results.append({
            "event": "activity",
            "task_id": task_id,
            "session_id": session_id,
            "evidence_type": event_type,
            "source": "opencode",
        })

    # Anything else (server.*, tui.*, lsp.*, mcp.*, pty.*, project.*, ...) is
    # not relevant to Jarvis's task lifecycle and is deliberately dropped.

    return results
