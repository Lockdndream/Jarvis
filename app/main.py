import json
import logging
import os
import uuid
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
    get_unresolved_attention_requests,
    get_attention_request,
)
from .executor import Executor
from .task_manager import TaskManager
from .integrations.opencode_supervisor import OpenCodeSupervisor
from .supervisor.supervisor import Supervisor
from . import push as push_module
from .attention_scheduler import AttentionScheduler
from . import attention_manager
from .voice_session_manager import VoiceSessionManager, VoiceSessionError
from .voice_session_reaper import VoiceSessionReaper
from .integrations.ws_tokens import issue_ws_token, verify_ws_token

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
attention_scheduler = AttentionScheduler(conn_manager)
voice_session_manager = VoiceSessionManager(supervisor)
voice_session_reaper = VoiceSessionReaper(voice_session_manager, conn_manager)
attention_manager.set_broadcast_hook(conn_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    interrupted_task_ids = mark_running_tasks_interrupted()
    mark_running_opencode_tasks_interrupted()
    # Milestone 8.1 real-phone finding: a local task's question/task rows
    # just got force-cancelled/failed above (genuine source invalidation,
    # not an OpenCode task — those keep their AttentionRequest alive via
    # mark_running_opencode_tasks_interrupted()'s 'degraded' state
    # instead) — any AttentionRequest still pointing at that now-dead
    # source must not be left to re-contact the user about it later.
    for task_id in interrupted_task_ids:
        await attention_manager.cancel_for_task(task_id)
    await opencode_supervisor.start()
    await attention_scheduler.start()
    await voice_session_reaper.start()
    logger.info("Jarvis server started")
    yield
    await voice_session_reaper.stop()
    await attention_scheduler.stop()
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


WS_CLOSE_TOKEN_EXPIRED = 4001
WS_CLOSE_TOKEN_INVALID = 4002
WS_CLOSE_TOKEN_MISSING = 4003
WS_CLOSE_AUTH_FAILED = 4004


def _resolve_ws_close_code(ws: WebSocket) -> int | None:
    """Milestone 9B.2 (ADR-014): unified WebSocket authentication. Returns
    None if the handshake may proceed, otherwise the close code to send
    (application-defined range 4000-4999, RFC 6455) — sent AFTER
    ws.accept(), not before, so it reaches the client as a real close
    frame. A pre-accept ws.close() discards its code entirely (uvicorn
    always rejects with a bare HTTP 403 — see the /ws handler's own
    history, and ADR-012's uvicorn-source finding).

    A `?token=` query param, if present, is ALWAYS verified via
    verify_ws_token() regardless of whether JARVIS_API_TOKEN is set — a
    client that fetched a real signed token gets real verification either
    way, not a silent pass-through. Falls back to the deprecated
    Authorization-header path (ADR-011) only when no token param is
    present, preserving that path for already-paired clients during
    migration; still a no-op when JARVIS_API_TOKEN is unset, unchanged
    from ADR-011's original behavior."""
    token = ws.query_params.get("token")
    if token:
        result = verify_ws_token(token)
        if result.ok:
            return None
        return WS_CLOSE_TOKEN_EXPIRED if result.reason == "expired" else WS_CLOSE_TOKEN_INVALID

    required = os.environ.get("JARVIS_API_TOKEN")
    if not required:
        return None
    auth_header = ws.headers.get("authorization")
    if not auth_header:
        return WS_CLOSE_TOKEN_MISSING
    if auth_header == f"Bearer {required}":
        logger.warning("/ws authenticated via deprecated Authorization header — migrate to ?token= (ADR-014)")
        return None
    return WS_CLOSE_AUTH_FAILED


class WsTokenRequest(BaseModel):
    client_id: str | None = None


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


@app.post("/api/ws-token")
async def issue_ws_token_endpoint(body: WsTokenRequest, _=Depends(_require_api_token)):
    """Milestone 9B.2 (ADR-014): issues a short-lived signed token for the
    /ws handshake's ?token= query param. Gated by the same
    _require_api_token dependency as the push endpoints — a no-op when
    JARVIS_API_TOKEN is unset (today's default deployment), a real check
    when set. client_id is informational only (becomes the token's `sub`
    claim); nothing server-side makes an authorization decision based on
    its value."""
    subject = body.client_id or str(uuid.uuid4())
    return issue_ws_token(subject)


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


@app.get("/api/attention/{attention_request_id}")
async def api_get_attention(attention_request_id: str):
    """Deep-link resolution target for a persistent AttentionRequest
    (Milestone 8 analog of the existing /api/question and /api/notification
    endpoints — Phase 9's "no stale answer controls for an already-resolved
    item" requirement applies here too). Never returns raw prompt/context
    text beyond what the in-app UI already shows once authenticated."""
    row = get_attention_request(attention_request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Attention request not found")
    return {
        "attention_request_id": row["attention_request_id"],
        "conversation_id": row["conversation_id"],
        "task_id": row["task_id"],
        "attention_type": row["attention_type"],
        "status": row["status"],
        "summary": row["summary"],
        "urgency": row["urgency"],
        "deferred_until": row["deferred_until"],
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    close_code = _resolve_ws_close_code(ws)
    if close_code is not None:
        # Accept first, then close with a real application close code
        # (ADR-014) — a pre-accept ws.close() discards its code entirely
        # (ADR-012's uvicorn-source finding: always a bare HTTP 403).
        # Accepting first means the client's onClosed(code, ...) actually
        # receives WS_CLOSE_TOKEN_EXPIRED/INVALID/MISSING/AUTH_FAILED,
        # which the required error taxonomy needs.
        await ws.accept()
        await ws.close(code=close_code)
        return
    await conn_manager.connect(ws)
    # Stable per-connection conversation identity (Milestone 6 Phase 7).
    # Set by an explicit "conversation_init" handshake if the client sends
    # one; otherwise lazily minted on the first user_message so older/other
    # clients (e.g. validation scripts) that never opted into the handshake
    # keep working exactly as before, just without cross-turn continuity.
    conversation_id: str | None = None
    # Milestone 9B.4 / TD-002: this connection's currently-open voice
    # session, if any — tracked here (not just client-side) so an abrupt
    # disconnect can release its AttentionRequest lease instead of leaving
    # it permanently held (see the `finally` block below).
    open_voice_session_id: str | None = None

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

    pending_attention = get_unresolved_attention_requests()
    if pending_attention:
        await ws.send_text(
            json.dumps({
                "type": "pending_attention",
                "attention_requests": [
                    {
                        "attention_request_id": a["attention_request_id"],
                        "attention_type": a["attention_type"],
                        "status": a["status"],
                        "summary": a["summary"],
                        "task_id": a["task_id"],
                        "conversation_id": a["conversation_id"],
                        "urgency": a["urgency"],
                        "deferred_until": a["deferred_until"],
                    }
                    for a in pending_attention
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

            if data.get("type") == "device_status":
                # Milestone 9B.2 (ADR-012): capability-advertisement/state-
                # sync message from a native companion (see
                # android/.../core/DeviceStatus.kt). Stored in-memory only,
                # keyed by connection — no DB table, no reasoning about the
                # contents. Not yet read by anything; this is the
                # communication primitive multi-device routing will later
                # build on, not routing logic itself. No reply is sent
                # (fire-and-forget, matching the heartbeat frame's shape).
                conn_manager.set_device_status(ws, data)
                continue

            if data.get("type") == "voice_session_open":
                client_request_id = data.get("client_request_id")
                vs_conv_id = data.get("conversation_id")
                if vs_conv_id and is_valid_conversation_id(vs_conv_id):
                    conversation_id = vs_conv_id
                elif conversation_id is None:
                    conversation_id = new_conversation_id()
                try:
                    session = voice_session_manager.open_session(conversation_id, data.get("attention_request_id"))
                except VoiceSessionError as e:
                    # TD-002: most commonly, another device already holds
                    # the lease on the requested attention_request_id.
                    # client_request_id (ADR-017): a generic, optional
                    # correlation primitive echoed back unchanged so the
                    # specific request that failed can be identified even
                    # though no session was ever created.
                    await ws.send_text(json.dumps({
                        "type": "voice_session_error", "voice_session_id": None, "error": str(e),
                        "client_request_id": client_request_id,
                    }))
                    continue
                open_voice_session_id = session["voice_session_id"]
                await ws.send_text(json.dumps({
                    "type": "voice_session_opened",
                    "voice_session_id": session["voice_session_id"],
                    "state": session["state"],
                    "conversation_id": conversation_id,
                    "attention_request_id": session.get("attention_request_id"),
                    "greeting": session.get("greeting"),
                    "client_request_id": client_request_id,
                }))
                continue

            if data.get("type") == "voice_session_transcript":
                vsid = data.get("voice_session_id")
                transcript = data.get("transcript", "")
                if not vsid:
                    continue
                try:
                    result = await voice_session_manager.handle_transcript(vsid, transcript)
                except VoiceSessionError as e:
                    await ws.send_text(json.dumps({
                        "type": "voice_session_error", "voice_session_id": vsid, "error": str(e),
                    }))
                    continue
                conversation_id = result.get("conversation_id") or conversation_id
                await ws.send_text(json.dumps({
                    "type": "voice_session_response",
                    "voice_session_id": vsid,
                    "response": result.get("response", ""),
                    "conversation_id": result.get("conversation_id"),
                    "attention_request_id": result.get("attention_request_id"),
                    "voice_session_state": result.get("voice_session_state"),
                }))
                continue

            if data.get("type") == "voice_session_close":
                vsid = data.get("voice_session_id")
                if vsid:
                    voice_session_manager.close_session(vsid, reason="client_requested")
                    if vsid == open_voice_session_id:
                        open_voice_session_id = None
                await ws.send_text(json.dumps({
                    "type": "voice_session_closed", "voice_session_id": vsid,
                    "reason": "client_requested",
                }))
                continue

            if data.get("type") == "user_message":
                content = data.get("content", "")
                msg_conv_id = data.get("conversation_id")
                bound_attention_request_id = data.get("bound_attention_request_id")
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
                    result = await supervisor.process_message(
                        content, conversation_id, bound_attention_request_id=bound_attention_request_id,
                    )
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
    except WebSocketDisconnect as e:
        conn_manager.disconnect(ws, code=e.code, reason=e.reason)
    except Exception as e:
        # Milestone 9B.0 Phase 2: a non-WebSocketDisconnect transport error
        # (e.g. a TLS/protocol-level failure) must still be logged with the
        # same connection-lifecycle detail as a clean disconnect, not left
        # to whatever uvicorn's own generic error path happens to show.
        logger.error("WebSocket transport error: %s: %s", type(e).__name__, e)
        conn_manager.disconnect(ws, code=None, reason=f"{type(e).__name__}: {e}")
    finally:
        # Milestone 9B.4 / TD-002: an abrupt disconnect (app killed, network
        # loss) never sends voice_session_close — without this, a session's
        # AttentionRequest lease would stay held forever, permanently
        # blocking any future voice session (on this device or another) for
        # that item. close_session() is itself idempotent/guarded (no-op if
        # already closed), so this is safe even if a close already ran.
        if open_voice_session_id:
            voice_session_manager.close_session(open_voice_session_id, reason="disconnect")


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
