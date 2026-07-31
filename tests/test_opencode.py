"""Tests for OpenCode integration (Milestone 4)."""
import asyncio
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app.integrations.opencode_adapter import OpenCodeAdapter
from app.integrations.opencode_supervisor import OpenCodeSupervisor


@pytest.fixture(autouse=True)
def test_db():
    old_path = db.DB_PATH
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = path
    db.init_db()
    yield
    db.DB_PATH = old_path
    if os.path.exists(path):
        os.unlink(path)


# ── Fake OpenCode HTTP Server ─────────────────────────────────────


class FakeOpenCodeServer:
    """In-process fake OpenCode HTTP server for testing."""

    def __init__(self):
        from socket import AF_INET, SOCK_STREAM, socket

        sock = socket(AF_INET, SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()

        self.sessions: dict[str, dict] = {}
        self.questions: list[dict] = []
        self.permissions: list[dict] = []
        self.permission_replies: list[dict] = []
        self.messages: dict[str, list] = {}
        self.last_prompt_model: dict | None = None
        self._app = self._build_app()
        self._server = None

        self.base_url = f"http://127.0.0.1:{self.port}"

    def _build_app(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        app = FastAPI()
        svc = self  # capture reference

        @app.get("/global/health")
        async def health():
            return {"healthy": True, "version": "1.15.10"}

        @app.post("/session")
        async def create_session():
            import uuid
            sid = f"ses_test_{uuid.uuid4().hex[:8]}"
            svc.sessions[sid] = {"id": sid, "status": "running"}
            return {"id": sid}

        @app.get("/session/{session_id}")
        async def get_session(session_id: str):
            return svc.sessions.get(session_id, {})

        @app.post("/session/{session_id}/prompt_async")
        async def prompt_async(session_id: str, request: Request):
            body = await request.json()
            text = ""
            for part in body.get("parts", []):
                if part.get("type") == "text":
                    text += part.get("text", "")
            if session_id not in svc.messages:
                svc.messages[session_id] = []
            svc.messages[session_id].append({
                "id": f"msg_{len(svc.messages[session_id])}",
                "role": "user",
                "content": text,
            })
            svc.sessions[session_id]["status"] = "running"
            svc.last_prompt_model = body.get("model")
            return "", 204

        @app.post("/session/{session_id}/abort")
        async def abort_session(session_id: str):
            if session_id in svc.sessions:
                svc.sessions[session_id]["status"] = "cancelled"
            return True

        @app.get("/api/session/{session_id}/message")
        async def get_messages_event_log(session_id: str, limit: int = 20):
            # Milestone 9A finding: this is the REAL server's actual shape for
            # this URL — a session-lifecycle event log (model-switched/
            # agent-switched), never the conversation itself. Modeled here
            # deliberately so a regression test can prove the adapter no
            # longer calls this wrong URL.
            return {"items": [
                {"id": "evt_fake_1", "type": "model-switched", "model": {"id": "fake-model"}},
                {"id": "evt_fake_2", "type": "agent-switched", "agent": "build"},
            ]}

        @app.get("/session/{session_id}/message")
        async def get_messages(session_id: str, limit: int = 20):
            # The REAL correct endpoint (no /api/ prefix) — real shape is a
            # bare list of {"info": {..., "role": ...}, "parts": [...]}.
            msgs = svc.messages.get(session_id, [])
            return [
                {
                    "info": {"id": m["id"], "role": m["role"]},
                    "parts": [{"type": "text", "text": m["content"]}],
                }
                for m in msgs[-limit:]
            ]

        @app.get("/question")
        async def get_questions():
            return svc.questions

        @app.post("/question/{request_id}/reply")
        async def reply_question(request_id: str, request: Request):
            await request.json()
            svc.questions = [q for q in svc.questions if q.get("requestID") != request_id]
            return "", 204

        @app.post("/question/{request_id}/reject")
        async def reject_question(request_id: str):
            svc.questions = [q for q in svc.questions if q.get("requestID") != request_id]
            return "", 204

        @app.get("/permission")
        async def get_permissions():
            return svc.permissions

        @app.post("/permission/{request_id}/reply")
        async def reply_permission(request_id: str, request: Request):
            # Mirrors OpenCode's real schema for this endpoint (GET /doc,
            # additionalProperties: false, "reply" required, enum
            # once/always/reject) -- a real-device 400 (Interaction Layer
            # Step 5) found that app/integrations/opencode_adapter.py used
            # to send {"approved": bool} instead, which this fake server
            # previously accepted unconditionally, providing zero coverage
            # against the real API's actual contract.
            body = await request.json()
            if set(body.keys()) - {"reply", "message"} or "reply" not in body or body["reply"] not in ("once", "always", "reject"):
                return JSONResponse(status_code=400, content={"error": "invalid reply body"})
            svc.permission_replies.append(body)
            svc.permissions = [p for p in svc.permissions if p.get("requestID") != request_id]
            return "", 204

        @app.post("/global/dispose")
        async def dispose():
            return True

        return app

    async def start(self):
        import uvicorn
        config = uvicorn.Config(self._app, host="127.0.0.1", port=self.port, log_level="error")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        for i in range(50):
            try:
                import httpx
                async with httpx.AsyncClient() as c:
                    r = await c.get(f"{self.base_url}/global/health", timeout=2)
                    if r.status_code == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.1)
        raise RuntimeError("Fake server did not start")

    async def stop(self):
        if self._server:
            self._server.should_exit = True
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    def add_question(self, request_id: str, question: str, options: list | None = None):
        self.questions.append({
            "requestID": request_id,
            "question": question,
            "options": options or [],
            "context": "test",
            "sessionID": "ses_test",
        })

    def add_permission(self, request_id: str, action: str, path: str):
        self.permissions.append({
            "requestID": request_id,
            "action": action,
            "path": path,
            "sessionID": "ses_test",
        })

    def add_message(self, session_id: str, role: str, content: str):
        if session_id not in self.messages:
            self.messages[session_id] = []
        self.messages[session_id].append({
            "id": f"msg_{len(self.messages[session_id])}",
            "role": role,
            "content": content,
        })


# ── Tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_health():
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        health = await adapter.health()
        assert health["healthy"] is True
        assert health["version"] == "1.15.10"
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_adapter_create_session():
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        sid = await adapter.create_session(directory="/tmp")
        assert sid.startswith("ses_test_")
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_adapter_send_prompt():
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        sid = await adapter.create_session(directory="/tmp")
        await adapter.send_prompt(sid, "/tmp", "Say hello")
        messages = await adapter.get_messages(sid, "/tmp")
        assert len(messages) >= 1
        assert messages[0]["info"]["role"] == "user"
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_send_prompt_always_pins_an_explicit_free_model():
    """Milestone 9B.0 finding: left unpinned, OpenCode picks its own default
    provider/model among whatever credentials happen to be visible to the
    subprocess — verified real to silently run a paid OpenAI model via an
    inherited ambient OPENAI_API_KEY. send_prompt() must always send an
    explicit model field so OpenCode never falls back to its own default
    selection, and that model must pass the same free-only rules as
    Jarvis's own supervisor LLM."""
    from app.integrations import opencode_adapter as oca

    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        sid = await adapter.create_session(directory="/tmp")
        await adapter.send_prompt(sid, "/tmp", "Say hello")

        assert fake.last_prompt_model == {
            "providerID": oca.DEFAULT_OPENCODE_PROVIDER_ID,
            "modelID": oca.DEFAULT_OPENCODE_MODEL_ID,
        }
        assert (
            oca.DEFAULT_OPENCODE_MODEL_ID.endswith(":free")
            or oca.DEFAULT_OPENCODE_MODEL_ID == "openrouter/free"
            # JARVIS_OPENCODE_ALLOW_PAID may be set (dev-only escape hatch,
            # see validate_opencode_model) — the one explicitly-allowlisted
            # paid model is a legitimate default too, still policy-bound,
            # just not free.
            or oca.DEFAULT_OPENCODE_MODEL_ID == oca.ALLOWED_PAID_OPENCODE_MODEL_ID
        )
    finally:
        await fake.stop()


def test_validate_opencode_model_rejects_paid_model_by_default(monkeypatch):
    """Milestone 9B.0: JARVIS_OPENCODE_ALLOW_PAID is unset/false by
    default — the paid-model escape hatch must not be implicitly open."""
    from app.integrations.opencode_adapter import validate_opencode_model, ALLOWED_PAID_OPENCODE_MODEL_ID
    from app.supervisor.llm import ModelNotAllowedError

    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    monkeypatch.delenv("JARVIS_OPENCODE_ALLOW_PAID", raising=False)
    with pytest.raises(ModelNotAllowedError):
        validate_opencode_model(ALLOWED_PAID_OPENCODE_MODEL_ID)


def test_validate_opencode_model_allows_exact_paid_model_when_flag_set(monkeypatch):
    from app.integrations.opencode_adapter import validate_opencode_model, ALLOWED_PAID_OPENCODE_MODEL_ID

    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    monkeypatch.setenv("JARVIS_OPENCODE_ALLOW_PAID", "true")
    validate_opencode_model(ALLOWED_PAID_OPENCODE_MODEL_ID)  # must not raise


def test_validate_opencode_model_still_rejects_other_paid_models_when_flag_set(monkeypatch):
    """The flag allows exactly one named model, not "all paid models"."""
    from app.integrations.opencode_adapter import validate_opencode_model
    from app.supervisor.llm import ModelNotAllowedError

    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    monkeypatch.setenv("JARVIS_OPENCODE_ALLOW_PAID", "true")
    with pytest.raises(ModelNotAllowedError):
        validate_opencode_model("openai/gpt-5.3-chat-latest")


def test_validate_opencode_model_free_models_still_work_regardless_of_flag(monkeypatch):
    from app.integrations.opencode_adapter import validate_opencode_model

    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    for flag in ("true", "false", None):
        if flag is None:
            monkeypatch.delenv("JARVIS_OPENCODE_ALLOW_PAID", raising=False)
        else:
            monkeypatch.setenv("JARVIS_OPENCODE_ALLOW_PAID", flag)
        validate_opencode_model("meta-llama/llama-3.3-70b-instruct:free")  # must not raise


@pytest.mark.asyncio
async def test_get_messages_hits_the_real_conversation_endpoint_not_the_event_log():
    """Milestone 9A finding, 2026-07-10: get_messages() used to call
    /api/session/{id}/message, which is a session-lifecycle EVENT log
    (model-switched/agent-switched entries — verified directly against the
    real isolated OpenCode server) and never contains the actual
    conversation. The real message history lives at the plain (no /api/
    prefix) /session/{id}/message. This test would FAIL against the old
    route (the event log never contains the submitted text at all) and
    PASSES against the corrected route."""
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        sid = await adapter.create_session(directory="/tmp")
        marker = "UNIQUE_MARKER_b7f3a1"
        await adapter.send_prompt(sid, "/tmp", marker)
        messages = await adapter.get_messages(sid, "/tmp")
        all_text = " ".join(
            p.get("text", "") for m in messages for p in m.get("parts", [])
        )
        assert marker in all_text, (
            f"submitted prompt text not found in retrieved messages "
            f"(would be the case if still hitting the wrong /api/-prefixed "
            f"event-log endpoint): {messages!r}"
        )
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_adapter_abort():
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        sid = await adapter.create_session(directory="/tmp")
        await adapter.abort_session(sid, "/tmp")
        session = await adapter.get_session(sid, "/tmp")
        assert session["status"] == "cancelled"
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_adapter_questions():
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        fake.add_question("q1", "What editor?", ["Vim", "VS Code"])
        qs = await adapter.get_questions(directory="/tmp")
        assert len(qs) == 1
        assert qs[0]["question"] == "What editor?"
        assert qs[0]["options"] == ["Vim", "VS Code"]

        await adapter.reply_question("q1", "/tmp", "VS Code")
        qs = await adapter.get_questions(directory="/tmp")
        assert len(qs) == 0
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_adapter_question_reject():
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        fake.add_question("q-reject", "Proceed?")
        await adapter.reject_question("q-reject", "/tmp")
        qs = await adapter.get_questions(directory="/tmp")
        assert len(qs) == 0
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_adapter_permissions():
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        fake.add_permission("p1", "read", "/foo/bar.txt")
        ps = await adapter.get_permissions(directory="/tmp")
        assert len(ps) == 1
        assert ps[0]["action"] == "read"
        assert ps[0]["path"] == "/foo/bar.txt"

        await adapter.reply_permission("p1", "/tmp", True)
        ps = await adapter.get_permissions(directory="/tmp")
        assert len(ps) == 0
        # Regression: OpenCode's real API rejects {"approved": bool}
        # (additionalProperties: false, "reply" required) -- confirm the
        # adapter sends the real shape, not the old one that produced a
        # live 400 Bad Request (Interaction Layer Step 5).
        assert fake.permission_replies == [{"reply": "once"}]
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_adapter_reply_permission_reject_sends_reject():
    fake = FakeOpenCodeServer()
    await fake.start()
    try:
        adapter = OpenCodeAdapter(base_url=fake.base_url)
        fake.add_permission("p1", "read", "/foo/bar.txt")
        await adapter.reply_permission("p1", "/tmp", False)
        assert fake.permission_replies == [{"reply": "reject"}]
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_database_opencode_task_crud():
    db.create_task_record("oc-test-1", "OpenCode Test", "test instruction")
    db.create_opencode_task_record("oc-test-1", "ses_test_123", "/tmp", "test instruction")

    task = db.get_opencode_task("oc-test-1")
    assert task is not None
    assert task["session_id"] == "ses_test_123"
    assert task["status"] == "running"

    db.update_opencode_task_status("oc-test-1", "completed")
    task = db.get_opencode_task("oc-test-1")
    assert task["status"] == "completed"

    task2 = db.get_opencode_task_by_session("ses_test_123")
    assert task2 is not None
    assert task2["task_id"] == "oc-test-1"


@pytest.mark.asyncio
async def test_database_opencode_mark_interrupted():
    """Milestone 6: a Jarvis restart marks non-terminal OpenCode tasks
    'degraded', not 'failed' — unlike a local subprocess, an OpenCode
    session lives in OpenCode's own persistent DB and restarting Jarvis does
    not prove it actually failed. Falsely claiming failure would violate the
    'no false failure/success claims' requirement."""
    db.create_task_record("oc-inter-1", "OC Task", "test")
    db.create_opencode_task_record("oc-inter-1", "ses_inter", "/tmp", "test")
    db.mark_running_opencode_tasks_interrupted()
    task = db.get_opencode_task("oc-inter-1")
    assert task["status"] == "degraded"
    assert task in db.get_opencode_degraded_tasks()


@pytest.mark.asyncio
async def test_events_normalize_question():
    from app.integrations.opencode_events import normalize_question
    oc_q = {"requestID": "q1", "question": "Proceed?", "options": ["Yes", "No"], "context": "approval"}
    result = normalize_question(oc_q, "task-1")
    assert result is not None
    assert result["event"] == "question"
    assert result["request_id"] == "q1"
    assert result["question"] == "Proceed?"
    assert result["options"] == ["Yes", "No"]
    assert result["task_id"] == "task-1"
    assert result["source"] == "opencode"


@pytest.mark.asyncio
async def test_events_normalize_permission():
    from app.integrations.opencode_events import normalize_permission
    oc_p = {"requestID": "p1", "action": "write", "path": "/tmp/test.txt"}
    result = normalize_permission(oc_p, "task-1")
    assert result is not None
    assert result["event"] == "permission"
    assert result["request_id"] == "p1"
    assert result["action"] == "write"
    assert result["path"] == "/tmp/test.txt"
    assert result["task_id"] == "task-1"
    assert result["source"] == "opencode"


def _real_sse_frame(directory, evt_type, properties, evt_id="evt_test1"):
    """Build an SSE frame matching the real OpenCode v1.15.10 GlobalEvent
    envelope, as captured by direct probe against the live server
    (2026-07-09) and cross-checked against the OpenAPI /doc schema. There is
    no top-level `event:` SSE field in real frames — only `data:`."""
    return {
        "data": {
            "directory": directory,
            "project": "global",
            "payload": {"id": evt_id, "type": evt_type, "properties": properties},
        }
    }


@pytest.mark.asyncio
async def test_events_process_sse_question_asked_triggers_poll():
    from app.integrations.opencode_events import process_sse_event
    sse = _real_sse_frame("/tmp/proj", "question.asked", {"sessionID": "ses_sse", "id": "que_1", "questions": []})
    results = process_sse_event(sse, "task-sse")
    assert len(results) == 1
    assert results[0]["event"] == "question_poll_trigger"
    assert results[0]["directory"] == "/tmp/proj"


@pytest.mark.asyncio
async def test_events_process_sse_permission_asked_triggers_poll():
    from app.integrations.opencode_events import process_sse_event
    sse = _real_sse_frame("/tmp/proj", "permission.asked", {"sessionID": "ses_sse", "id": "per_1", "permission": "write"})
    results = process_sse_event(sse, "task-sse")
    assert len(results) == 1
    assert results[0]["event"] == "permission_poll_trigger"


@pytest.mark.asyncio
async def test_events_process_sse_session_error_is_failure_evidence():
    from app.integrations.opencode_events import process_sse_event
    sse = _real_sse_frame(None, "session.error", {"sessionID": "ses_sse", "error": {"name": "UnknownError"}})
    results = process_sse_event(sse, "task-sse")
    assert len(results) == 1
    assert results[0]["event"] == "session_failed"
    assert results[0]["session_id"] == "ses_sse"


@pytest.mark.asyncio
async def test_events_process_sse_session_idle():
    from app.integrations.opencode_events import process_sse_event
    sse = _real_sse_frame(None, "session.idle", {"sessionID": "ses_sse"})
    results = process_sse_event(sse, "task-sse")
    assert len(results) == 1
    assert results[0]["event"] == "session_idle"


@pytest.mark.asyncio
async def test_events_process_sse_activity_events():
    from app.integrations.opencode_events import process_sse_event
    for evt_type in ("message.part.delta", "session.next.tool.called", "session.next.step.started", "session.diff"):
        sse = _real_sse_frame(None, evt_type, {"sessionID": "ses_sse"})
        results = process_sse_event(sse, "task-sse")
        assert len(results) == 1, f"expected activity evidence for {evt_type}"
        assert results[0]["event"] == "activity"
        assert results[0]["evidence_type"] == evt_type


@pytest.mark.asyncio
async def test_events_process_sse_ignores_uninteresting_event_types():
    from app.integrations.opencode_events import process_sse_event
    for evt_type in ("server.connected", "server.heartbeat", "tui.toast.show", "lsp.updated"):
        sse = _real_sse_frame(None, evt_type, {})
        results = process_sse_event(sse, "task-sse")
        assert results == [], f"{evt_type} should be silently ignored, not raise or route anywhere"


@pytest.mark.asyncio
async def test_events_process_sse_malformed_frame_does_not_raise():
    from app.integrations.opencode_events import process_sse_event
    assert process_sse_event({}, "task-sse") == []
    assert process_sse_event({"data": "not a dict"}, "task-sse") == []
    assert process_sse_event({"data": {"payload": "not a dict"}}, "task-sse") == []
    assert process_sse_event({"data": {"payload": {"properties": {}}}}, "task-sse") == []  # missing "type"

    # properties of the wrong type must not raise — degrade to empty, not crash
    results = process_sse_event({"data": {"payload": {"type": "session.error", "properties": "bad"}}}, "task-sse")
    assert len(results) == 1
    assert results[0]["event"] == "session_failed"
    assert results[0]["session_id"] == ""


@pytest.mark.asyncio
async def test_events_normalize_message():
    from app.integrations.opencode_events import normalize_message
    oc_msg = {"id": "msg_1", "role": "assistant", "content": "Hello world"}
    result = normalize_message(oc_msg, "task-1", "ses_1")
    assert result is not None
    assert result["role"] == "assistant"
    assert result["content"] == "Hello world"


# ── Regression: WebSocket double-encoding bug ──────────────────────
#
# ConnectionManager.broadcast() does its own json.dumps(data) before calling
# ws.send_text(). OpenCodeSupervisor._notify_broadcast() previously called
# self.cm.broadcast(json.dumps(msg)), so a dict got json.dumps'd twice: once
# by _notify_broadcast, once by broadcast(). The client received a JSON
# string literal containing escaped JSON text instead of a JSON object, so
# json.loads() on the client yielded a Python str, not a dict.


class RecordingWebSocket:
    """Minimal fake WebSocket that records raw text sent by ConnectionManager."""

    def __init__(self):
        self.sent: list[str] = []

    async def accept(self):
        pass

    async def send_text(self, text: str):
        self.sent.append(text)


@pytest.mark.asyncio
async def test_notify_broadcast_sends_json_object_not_string():
    """OpenCodeSupervisor broadcasts must arrive client-side as JSON objects."""
    cm = ConnectionManager()
    ws = RecordingWebSocket()
    await cm.connect(ws)

    supervisor = OpenCodeSupervisor.__new__(OpenCodeSupervisor)
    supervisor.cm = cm

    await supervisor._notify_broadcast({
        "type": "opencode_message",
        "task_id": "oc_test123",
        "role": "assistant",
        "content": "hello from opencode",
    })

    assert len(ws.sent) == 1
    parsed = json.loads(ws.sent[0])
    assert isinstance(parsed, dict), (
        f"broadcast payload was double-encoded: expected a dict, "
        f"got {type(parsed).__name__} ({parsed!r})"
    )
    assert parsed["type"] == "opencode_message"
    assert parsed["content"] == "hello from opencode"


@pytest.mark.asyncio
async def test_notify_broadcast_survives_disconnected_client():
    """Broadcast failures to one client must not raise out of _notify_broadcast."""
    cm = ConnectionManager()
    supervisor = OpenCodeSupervisor.__new__(OpenCodeSupervisor)
    supervisor.cm = cm

    # No connected clients at all — cm.broadcast() must be a no-op, and
    # _notify_broadcast must not raise even if the manager errors internally.
    await supervisor._notify_broadcast({"type": "opencode_error", "task_id": "t1"})
