import json
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .connection_manager import ConnectionManager
from .database import (
    init_db,
    mark_running_tasks_interrupted,
    mark_running_opencode_tasks_interrupted,
    save_event,
    get_recent_events,
    get_conversation_messages,
    is_valid_conversation_id,
    new_conversation_id,
    get_pending_notifications,
    get_task,
    get_question_record,
    get_notification,
    mark_notification_read,
    save_push_subscription,
    delete_push_subscription,
    get_setting,
    set_setting,
)
from .executor import Executor
from .task_manager import TaskManager
from .integrations.opencode_supervisor import OpenCodeSupervisor
from .supervisor.supervisor import Supervisor
from . import push as push_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("jarvis")

# Bounded conversation history sent to the browser on handshake/reconnect —
# never the full unbounded conversation.
CONVERSATION_HISTORY_LIMIT = 20

conn_manager = ConnectionManager()
task_manager = TaskManager(conn_manager)
opencode_supervisor = OpenCodeSupervisor(conn_manager, task_manager)
executor = Executor(task_manager, opencode_supervisor)
supervisor = Supervisor(task_manager, opencode_supervisor)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    mark_running_tasks_interrupted()
    mark_running_opencode_tasks_interrupted()
    await opencode_supervisor.start()
    logger.info("Jarvis server started")
    yield
    await opencode_supervisor.stop()
    await task_manager.shutdown()
    logger.info("Jarvis server shut down")


app = FastAPI(title="Jarvis", lifespan=lifespan)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/manifest.json")
async def manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"), media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    # Served from the root path (not /static/sw.js) so its default scope is
    # "/" — a service worker's scope is the directory it's served from.
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"), media_type="application/javascript")


def _require_api_token(authorization: str | None = Header(default=None)) -> None:
    """Optional shared-secret gate for push-subscription writes (Milestone 7
    Phase 3: "push subscription endpoints must be authenticated" when
    external access is introduced). If JARVIS_API_TOKEN is unset, this is a
    no-op — consistent with the project's existing LAN-only/no-auth
    prototype trust model (Known Limitation #1, unchanged since Milestone 1).
    Set JARVIS_API_TOKEN before exposing Jarvis beyond a trusted LAN."""
    required = os.environ.get("JARVIS_API_TOKEN")
    if not required:
        return
    if authorization != f"Bearer {required}":
        raise HTTPException(status_code=401, detail="Unauthorized")


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: dict
    conversation_id: str | None = None


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


class SettingsUpdateRequest(BaseModel):
    notify_on_completion: bool | None = None


@app.get("/api/vapid-public-key")
async def vapid_public_key():
    key = push_module.get_vapid_public_key()
    return {"vapid_public_key": key, "push_configured": push_module.vapid_configured()}


@app.post("/api/push/subscribe")
async def push_subscribe(body: PushSubscribeRequest, _=Depends(_require_api_token)):
    p256dh = body.keys.get("p256dh")
    auth_key = body.keys.get("auth")
    if not body.endpoint or not p256dh or not auth_key:
        raise HTTPException(status_code=400, detail="Malformed subscription")
    save_push_subscription(body.endpoint, p256dh, auth_key, body.conversation_id)
    return {"status": "subscribed"}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubscribeRequest, _=Depends(_require_api_token)):
    delete_push_subscription(body.endpoint)
    return {"status": "unsubscribed"}


@app.get("/api/settings")
async def get_settings():
    from . import attention_policy
    return {"notify_on_completion": attention_policy.notify_on_completion()}


@app.post("/api/settings")
async def update_settings(body: SettingsUpdateRequest):
    if body.notify_on_completion is not None:
        set_setting("notify_on_completion", "true" if body.notify_on_completion else "false")
    from . import attention_policy
    return {"notify_on_completion": attention_policy.notify_on_completion()}


@app.get("/api/task/{task_id}")
async def api_get_task(task_id: str):
    """Non-sensitive task summary for deep-link resolution when a task is
    not (or no longer) present in a client's in-memory active-task state
    (e.g. it finished before the client connected). Never returns
    credentials, filesystem contents, or raw stdout."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task["task_id"],
        "name": task["name"],
        "status": task["status"],
        "started_at": task["started_at"],
        "completed_at": task["completed_at"],
    }


@app.get("/api/question/{question_id}")
async def api_get_question(question_id: str):
    """Covers both questions and permissions — both live in the `questions`
    table (Known Limitation #14). Lets a deep link detect a stale/already-
    resolved item and avoid showing answer controls for it (Phase 9)."""
    q = get_question_record(question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    return {
        "question_id": q["question_id"],
        "task_id": q["task_id"],
        "question": q["question"],
        "context": q["context"],
        "status": q["status"],
        "answer": q["answer"],
    }


@app.get("/api/notification/{notification_id}")
async def api_get_notification(notification_id: str):
    """Click-through target for a notification (Phase 9). Marks it read
    (idempotent) and returns the routing identifiers a client needs to open
    the right conversation/task/question — never the notification body a
    second time beyond what was already shown on the lock screen."""
    n = get_notification(notification_id)
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    mark_notification_read(notification_id)
    return {
        "notification_id": n["notification_id"],
        "notification_type": n["notification_type"],
        "conversation_id": n["conversation_id"],
        "task_id": n["task_id"],
        "source_type": n["source_type"],
        "source_id": n["source_id"],
        "status": "read",
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await conn_manager.connect(ws)
    # Stable per-connection conversation identity (Milestone 6 Phase 7).
    # Set by an explicit "conversation_init" handshake if the client sends
    # one; otherwise lazily minted on the first user_message so older/other
    # clients (e.g. validation scripts) that never opted into the handshake
    # keep working exactly as before, just without cross-turn continuity.
    conversation_id: str | None = None

    events = get_recent_events(100)
    await ws.send_text(json.dumps({"type": "history", "events": events}))

    running = task_manager.get_running_tasks_info()
    if running:
        await ws.send_text(
            json.dumps({"type": "running_tasks", "tasks": running})
        )

    pending = task_manager.get_pending_questions_info()
    if pending:
        await ws.send_text(
            json.dumps({"type": "pending_questions", "questions": pending})
        )

    pending_notifications = get_pending_notifications()
    if pending_notifications:
        await ws.send_text(
            json.dumps({
                "type": "pending_notifications",
                "notifications": [
                    {
                        "type": "notification",
                        "notification_id": n["notification_id"],
                        "notification_type": n["notification_type"],
                        "title": n["title"],
                        "body": n["body"],
                        "priority": n["priority"],
                        "conversation_id": n["conversation_id"],
                        "task_id": n["task_id"],
                        "source_type": n["source_type"],
                        "source_id": n["source_id"],
                        "created_at": n["created_at"],
                    }
                    for n in pending_notifications
                ],
            })
        )

    oc_status = await opencode_supervisor.get_status()
    await ws.send_text(
        json.dumps({
            "type": "opencode_status",
            "server_alive": oc_status["server_alive"],
            "running_tasks": oc_status["running_tasks"],
            "pending_questions": oc_status["pending_questions"],
        })
    )

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)

            if data.get("type") == "conversation_init":
                requested = data.get("conversation_id")
                if requested and is_valid_conversation_id(requested):
                    conversation_id = requested
                else:
                    conversation_id = new_conversation_id()
                history = [
                    {"role": m["role"], "content": m["content"], "created_at": m["created_at"]}
                    for m in get_conversation_messages(conversation_id, limit=CONVERSATION_HISTORY_LIMIT)
                    if m["role"] in ("user", "assistant")
                ]
                await ws.send_text(
                    json.dumps({
                        "type": "conversation_ready",
                        "conversation_id": conversation_id,
                        "history": history,
                    })
                )
                continue

            if data.get("type") == "user_message":
                content = data.get("content", "")
                msg_conv_id = data.get("conversation_id")
                if msg_conv_id and is_valid_conversation_id(msg_conv_id):
                    conversation_id = msg_conv_id
                elif conversation_id is None:
                    conversation_id = new_conversation_id()
                save_event("user_message", content)
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "user_message",
                            "timestamp": _now(),
                            "content": content,
                        }
                    )
                )

                if content.strip().startswith("/"):
                    async for ev_type, ev_content in executor.execute(content):
                        save_event(ev_type, ev_content)
                        await ws.send_text(
                            json.dumps(
                                {
                                    "type": ev_type,
                                    "timestamp": _now(),
                                    "content": ev_content,
                                }
                            )
                        )
                else:
                    await ws.send_text(
                        json.dumps({"type": "supervisor_thinking", "timestamp": _now()})
                    )
                    result = await supervisor.process_message(content, conversation_id)
                    conversation_id = result.get("conversation_id") or conversation_id
                    response_text = result.get("response", "")
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "supervisor_message",
                                "timestamp": _now(),
                                "content": response_text,
                                "conversation_id": result.get("conversation_id", ""),
                            }
                        )
                    )
    except WebSocketDisconnect:
        conn_manager.disconnect(ws)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
