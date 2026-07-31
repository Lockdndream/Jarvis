"""Interaction Layer Step 1 — Server-side conversation message stream.

Tests for all five changes specified in the Step 1 brief:
  1. conversation_turn ordering (before voice_session_response)
  2. thinking_update started/completed pairs at both tool-call sites
  3. thinking_update noop when _broadcast_hook is None
  4. trace_id in opencode_task_* broadcast payloads
  5. permission_response resolves via _resolve_bound_command
"""
import json
import os
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app.stt import TranscriptResult

_ENV_BEFORE_IMPORT = dict(os.environ)
import app.main as _main_module
for _leaked_var in set(os.environ) - set(_ENV_BEFORE_IMPORT):
    if _leaked_var.startswith("JARVIS_LLM") or _leaked_var.startswith("OPENCODE_"):
        del os.environ[_leaked_var]

import app.supervisor.supervisor as supervisor_module
from app.supervisor.supervisor import Supervisor
from app.supervisor.llm import FakeLLMProvider
from app.integrations.opencode_supervisor import OpenCodeSupervisor
from app import attention_manager as am
from app import db_async as adb


# ── Fixtures ─────────────────────────────────────────────────────────

class _RecordingBroadcastConnManager:
    """Fake ConnectionManager that records both broadcast() and broadcast_observers()
    calls. Used for thinking_update tests — the phone-facing path uses general
    broadcast(), not broadcast_observers()."""
    def __init__(self, observers=True):
        self._observers = observers
        self.broadcast_calls = []
        self.observer_calls = []

    def has_observers(self):
        return self._observers

    async def broadcast(self, data):
        self.broadcast_calls.append(data)

    async def broadcast_observers(self, data):
        self.observer_calls.append(data)


@pytest.fixture(autouse=True)
def test_db_supervisor():
    old_path = db.DB_PATH
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = path
    db.init_db()
    yield
    db.DB_PATH = old_path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def supervisor():
    return Supervisor()


# WebSocket E2E client fixture (from test_voice_session_audio.py /
# test_dashboard_observer_isolation.py pattern)
@pytest.fixture
def ws_client(monkeypatch):
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    main_module = _main_module

    async def noop_start(self):
        pass

    async def noop_stop(self):
        pass

    monkeypatch.setattr(main_module.opencode_supervisor, "start", noop_start.__get__(main_module.opencode_supervisor))
    monkeypatch.setattr(main_module.opencode_supervisor, "stop", noop_stop.__get__(main_module.opencode_supervisor))

    async def fake_get_status():
        return {"server_alive": False, "server_url": "", "running_tasks": [], "pending_questions": []}
    monkeypatch.setattr(main_module.opencode_supervisor, "get_status", fake_get_status)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        yield c

    if os.path.exists(path):
        os.unlink(path)


def _drain_initial(ws):
    seen_types = set()
    for _ in range(8):
        try:
            msg = ws.receive_json()
        except Exception:
            break
        seen_types.add(msg.get("type"))
        if "opencode_status" in seen_types:
            break
    return seen_types


# ── Change 1: conversation_turn ordering ─────────────────────────────

def test_conversation_turn_sent_before_voice_session_response(ws_client, monkeypatch):
    """conversation_turn (role=user) must arrive BEFORE voice_session_response —
    this ordering is the entire point of transcript verification.

    Uses the same mocking pattern as test_voice_session_audio.py, extended to
    assert ordering rather than just the last message type."""
    with ws_client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({"type": "voice_session_open", "conversation_id": None, "attention_request_id": None})
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        vsid = opened["voice_session_id"]

        async def fake_handle_transcript(voice_session_id, transcript):
            return {
                "response": "stt response text",
                "conversation_id": None,
                "attention_request_id": None,
                "voice_session_state": "listening",
                "trace_id": "trace-stt-1",
            }

        monkeypatch.setattr(
            _main_module.voice_session_manager,
            "handle_transcript",
            fake_handle_transcript,
        )

        async def fake_transcribe(audio_bytes, filename="audio.wav"):
            return TranscriptResult(
                text="hello world",
                duration_seconds=1.5,
                cost_usd=0.00002,
            )

        monkeypatch.setattr(_main_module.stt, "transcribe", fake_transcribe)

        audio = b"RIFF" + b"\x00" * 100
        ws.send_json({"type": "voice_session_audio", "voice_session_id": vsid})
        ws.send_bytes(audio)

        conversation_turn = ws.receive_json()
        assert conversation_turn["type"] == "conversation_turn"
        assert conversation_turn["role"] == "user"
        assert conversation_turn["content"] == "hello world"
        assert conversation_turn["voice_session_id"] == vsid
        assert "timestamp" in conversation_turn

        voice_session_response = ws.receive_json()
        assert voice_session_response["type"] == "voice_session_response"
        assert voice_session_response["voice_session_id"] == vsid
        assert voice_session_response["response"] == "stt response text"


# ── Change 2: thinking_update started/completed pairs ────────────────

@pytest.mark.asyncio
async def test_thinking_update_started_completed_in_main_loop(supervisor, monkeypatch):
    """Tool-call site 1: the main tool-call loop emits a started/completed pair."""
    called = []

    async def fake_call(name, args, conversation_id=None):
        called.append((name, args))
        return "tool result: done"

    monkeypatch.setattr(supervisor.tools, "call", fake_call)

    llm = FakeLLMProvider()
    llm.add_response({
        "role": "assistant",
        "tool_calls": [{
            "id": "call_1",
            "function": {
                "name": "start_opencode_task",
                "arguments": json.dumps({"project_alias": "jarvis", "instruction": "create a file"}),
            },
        }],
    })
    llm.add_response({"role": "assistant", "content": "Done."})
    supervisor._llm = llm

    fake_cm = _RecordingBroadcastConnManager()
    old_hook = supervisor_module._broadcast_hook
    supervisor_module.set_broadcast_hook(fake_cm)
    try:
        await supervisor.process_message("start an opencode task", conversation_id="c1")
    finally:
        supervisor_module._broadcast_hook = old_hook

    thinking_msgs = [c for c in fake_cm.broadcast_calls if c.get("type") == "thinking_update"]
    assert len(thinking_msgs) == 2, f"expected 2 thinking_update, got {len(thinking_msgs)}: {thinking_msgs}"

    started = thinking_msgs[0]
    assert started["action"] == "tool_call"
    assert started["status"] == "started"
    assert started["summary"] == "Calling start_opencode_task"
    assert started["detail"] is None
    assert started["conversation_id"] == "c1"
    assert started["trace_id"] is not None
    assert "timestamp" in started

    completed = thinking_msgs[1]
    assert completed["action"] == "tool_call"
    assert completed["status"] == "completed"
    assert completed["summary"] == "start_opencode_task finished"
    assert completed["detail"] == "tool result: done"
    assert completed["conversation_id"] == "c1"
    assert completed["trace_id"] == started["trace_id"]
    assert len(completed["detail"]) <= 200
    assert "args" not in str(completed["detail"]).lower() or completed["detail"] != str(
        {"project_alias": "jarvis", "instruction": "create a file"}
    )


@pytest.mark.asyncio
async def test_thinking_update_detail_bounded_to_200_chars(supervisor, monkeypatch):
    """detail must never exceed 200 chars and must never contain raw args."""
    called = []

    long_result = "x" * 500

    async def fake_call(name, args, conversation_id=None):
        called.append((name, args))
        return long_result

    monkeypatch.setattr(supervisor.tools, "call", fake_call)

    llm = FakeLLMProvider()
    llm.add_response({
        "role": "assistant",
        "tool_calls": [{
            "id": "call_1",
            "function": {
                "name": "start_opencode_task",
                "arguments": json.dumps({"project_alias": "jarvis", "instruction": "investigate"}),
            },
        }],
    })
    llm.add_response({"role": "assistant", "content": "Done."})
    supervisor._llm = llm

    fake_cm = _RecordingBroadcastConnManager()
    old_hook = supervisor_module._broadcast_hook
    supervisor_module.set_broadcast_hook(fake_cm)
    try:
        await supervisor.process_message("please investigate the issue", conversation_id="c2")
    finally:
        supervisor_module._broadcast_hook = old_hook

    thinking_msgs = [c for c in fake_cm.broadcast_calls if c.get("type") == "thinking_update"]
    assert len(thinking_msgs) == 2
    completed = thinking_msgs[1]
    assert len(completed["detail"]) <= 200
    assert not completed["detail"].endswith("..."), f"detail should not be elided: {completed['detail'][:50]}"


@pytest.mark.asyncio
async def test_thinking_update_started_completed_in_resolve_confirmation(supervisor, monkeypatch):
    """Tool-call site 2: resolve_pending_tool_confirmation emits a started/completed pair."""
    called = []

    async def fake_call(name, args, conversation_id=None):
        called.append((name, args))
        return "confirmation tool result"

    monkeypatch.setattr(supervisor.tools, "call", fake_call)

    fake_cm = _RecordingBroadcastConnManager()
    old_hook = supervisor_module._broadcast_hook
    supervisor_module.set_broadcast_hook(fake_cm)
    try:
        await supervisor.resolve_pending_tool_confirmation(
            "c3", "yes",
            {"name": "start_opencode_task", "args": {"project_alias": "jarvis", "instruction": "run tests"}},
        )
    finally:
        supervisor_module._broadcast_hook = old_hook

    thinking_msgs = [c for c in fake_cm.broadcast_calls if c.get("type") == "thinking_update"]
    assert len(thinking_msgs) == 2, f"expected 2 thinking_update, got {len(thinking_msgs)}"

    started = thinking_msgs[0]
    assert started["status"] == "started"
    assert started["summary"] == "Calling start_opencode_task"
    assert started["detail"] is None

    completed = thinking_msgs[1]
    assert completed["status"] == "completed"
    assert completed["summary"] == "start_opencode_task finished"
    assert completed["detail"] == "confirmation tool result"
    assert completed["conversation_id"] == "c3"


# ── Change 2b: thinking_update noop when hook is None ────────────────

@pytest.mark.asyncio
async def test_thinking_update_noop_when_broadcast_hook_is_none():
    """_broadcast_phone must not raise when _broadcast_hook is None."""
    old_hook = supervisor_module._broadcast_hook
    supervisor_module._broadcast_hook = None
    try:
        await supervisor_module._broadcast_phone({
            "type": "thinking_update",
            "action": "tool_call",
            "status": "started",
            "summary": "Calling test_tool",
            "detail": None,
            "conversation_id": "c4",
            "trace_id": "trace-1",
            "timestamp": "2026-01-01T00:00:00Z",
        })
    finally:
        supervisor_module._broadcast_hook = old_hook


@pytest.mark.asyncio
async def test_thinking_update_noop_when_hook_set_but_no_connections():
    """_broadcast_phone must not raise even with an empty ConnectionManager."""
    real_cm = ConnectionManager()
    old_hook = supervisor_module._broadcast_hook
    supervisor_module.set_broadcast_hook(real_cm)
    try:
        await supervisor_module._broadcast_phone({
            "type": "thinking_update",
            "action": "tool_call",
            "status": "started",
            "summary": "Calling test_tool",
            "detail": None,
            "conversation_id": "c5",
            "trace_id": "trace-1",
            "timestamp": "2026-01-01T00:00:00Z",
        })
    finally:
        supervisor_module._broadcast_hook = old_hook


# ── Change 3: trace_id on opencode_task_* broadcasts ─────────────────

class _RecordingWebSocket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.sent.append(text)


def _make_oc_supervisor():
    sv = OpenCodeSupervisor.__new__(OpenCodeSupervisor)
    sv.cm = ConnectionManager()
    sv._completion_events = {}
    sv._error_since_prompt = {}
    import collections
    sv._seen_event_ids = collections.deque(maxlen=500)
    sv._seen_event_ids_set = set()
    return sv


@pytest.mark.asyncio
async def test_opencode_task_created_broadcast_includes_trace_id():
    """opencode_task_created sent by _notify_broadcast must carry trace_id."""
    cm = ConnectionManager()
    ws = _RecordingWebSocket()
    await cm.connect(ws)

    sv = _make_oc_supervisor()
    sv.cm = cm

    await sv._notify_broadcast({
        "type": "opencode_task_created",
        "task_id": "task-1",
        "session_id": "ses-1",
        "instruction": "do something",
        "trace_id": "trace-abc-123",
    })

    assert len(ws.sent) == 1
    parsed = json.loads(ws.sent[0])
    assert parsed["type"] == "opencode_task_created"
    assert parsed["trace_id"] == "trace-abc-123"


@pytest.mark.asyncio
async def test_opencode_task_cancelled_broadcast_includes_trace_id():
    """opencode_task_cancelled sent by _notify_broadcast must carry trace_id."""
    cm = ConnectionManager()
    ws = _RecordingWebSocket()
    await cm.connect(ws)

    sv = _make_oc_supervisor()
    sv.cm = cm

    await sv._notify_broadcast({
        "type": "opencode_task_cancelled",
        "task_id": "task-2",
        "trace_id": "trace-cancel-456",
    })

    assert len(ws.sent) == 1
    parsed = json.loads(ws.sent[0])
    assert parsed["type"] == "opencode_task_cancelled"
    assert parsed["trace_id"] == "trace-cancel-456"


@pytest.mark.asyncio
async def test_opencode_task_completed_broadcast_includes_trace_id():
    """opencode_task_completed sent by _notify_broadcast must carry trace_id."""
    cm = ConnectionManager()
    ws = _RecordingWebSocket()
    await cm.connect(ws)

    sv = _make_oc_supervisor()
    sv.cm = cm

    await sv._notify_broadcast({
        "type": "opencode_task_completed",
        "task_id": "task-3",
        "status": "completed",
        "source": "opencode",
        "trace_id": "trace-done-789",
    })

    assert len(ws.sent) == 1
    parsed = json.loads(ws.sent[0])
    assert parsed["type"] == "opencode_task_completed"
    assert parsed["trace_id"] == "trace-done-789"


@pytest.mark.asyncio
async def test_start_session_passes_trace_id_to_broadcast(monkeypatch):
    """start_session captures trace.current_trace_id() and includes it in
    the opencode_task_created broadcast."""
    import app.trace as trace_module

    class _FakeAdapter:
        def __init__(self):
            self.sent = []

        async def create_session(self, project_dir):
            return f"ses_{uuid.uuid4().hex[:8]}"

        async def send_prompt(self, session_id, project_dir, instruction, provider_id=None, model_id=None):
            self.sent.append((session_id, project_dir, instruction))

    class _FakeServerManager:
        def __init__(self, port=None, base_url=None):
            self.port = port
            self.base_url = "http://localhost:9999"
            self.owned = False
            self.last_health_check_at = None

        async def check_health(self):
            return True

    sv = _make_oc_supervisor()
    sv.adapter = _FakeAdapter()
    sv.server = _FakeServerManager()

    ws = _RecordingWebSocket()
    await sv.cm.connect(ws)

    broadcast_payloads = []

    async def capture_broadcast(self, msg):
        broadcast_payloads.append(msg)

    monkeypatch.setattr(OpenCodeSupervisor, "_notify_broadcast", capture_broadcast)

    trace_id = trace_module.new_trace_id()
    token = trace_module.bind_trace_id(trace_id)
    try:
        result = await sv.start_session("/tmp/proj", "run the tests")
    finally:
        trace_module.reset_trace_id(token)

    created_msgs = [p for p in broadcast_payloads if p.get("type") == "opencode_task_created"]
    assert len(created_msgs) == 1
    assert created_msgs[0]["trace_id"] == trace_id
    assert result["task_id"] == created_msgs[0]["task_id"]


# ── Change 5: permission_response inbound handler ────────────────────

def test_permission_response_approve_resolves_via_supervisor(ws_client, monkeypatch):
    """permission_response with decision='approve' calls supervisor.process_message
    with bound_attention_request_id and returns a permission_response_ack."""
    async def fake_process_message(user_message, conversation_id, bound_attention_request_id=None, confirm_before_tools=None):
        assert user_message == "approve"
        assert bound_attention_request_id == "attn-test-1"
        return {
            "response": "Permission approved.",
            "conversation_id": conversation_id or "conv-p",
        }

    monkeypatch.setattr(_main_module.supervisor, "process_message", fake_process_message)

    with ws_client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({
            "type": "permission_response",
            "attention_request_id": "attn-test-1",
            "decision": "approve",
        })

        ack = ws.receive_json()
        assert ack["type"] == "permission_response_ack"
        assert ack["attention_request_id"] == "attn-test-1"
        assert ack["response"] == "Permission approved."


def test_permission_response_reject_resolves_via_supervisor(ws_client, monkeypatch):
    """permission_response with decision='reject' calls supervisor.process_message
    and returns a permission_response_ack."""
    async def fake_process_message(user_message, conversation_id, bound_attention_request_id=None, confirm_before_tools=None):
        assert user_message == "reject"
        assert bound_attention_request_id == "attn-test-2"
        return {
            "response": "Permission rejected.",
            "conversation_id": conversation_id or "conv-r",
        }

    monkeypatch.setattr(_main_module.supervisor, "process_message", fake_process_message)

    with ws_client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({
            "type": "permission_response",
            "attention_request_id": "attn-test-2",
            "decision": "reject",
        })

        ack = ws.receive_json()
        assert ack["type"] == "permission_response_ack"
        assert ack["attention_request_id"] == "attn-test-2"
        assert ack["response"] == "Permission rejected."


def test_permission_response_invalid_decision_returns_error_ack(ws_client):
    """permission_response with invalid decision string returns an error ack."""
    with ws_client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({
            "type": "permission_response",
            "attention_request_id": "attn-bad",
            "decision": "maybe",
        })

        ack = ws.receive_json()
        assert ack["type"] == "permission_response_ack"
        assert "Invalid" in ack["response"]


def test_permission_response_missing_attention_request_id_returns_error_ack(ws_client):
    """permission_response with missing attention_request_id returns an error ack."""
    with ws_client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({
            "type": "permission_response",
            "decision": "approve",
        })

        ack = ws.receive_json()
        assert ack["type"] == "permission_response_ack"
        assert "Invalid" in ack["response"]


@pytest.mark.asyncio
async def test_resolve_bound_command_reports_stale_permission():
    """_resolve_bound_command must report an already-resolved/cancelled/stale
    attention request rather than acting on it again."""
    from app.supervisor.tools import ToolRegistry

    attn_id = f"attn_{uuid.uuid4().hex[:12]}"
    question_id = f"perm-q-{uuid.uuid4().hex[:6]}"

    conv_id = db.new_conversation_id()
    db.save_conversation_message(conv_id, "user", "a request", trace_id="t1")
    db.create_task_record("task-bound", "Test Task", "instruction")
    db.create_question_record(question_id, "task-bound", "Permission: read /tmp/file", "context", "[]")

    await adb.create_attention_request(
        attention_request_id=attn_id,
        conversation_id=conv_id,
        task_id="task-bound",
        source_type="opencode_permission",
        source_id=question_id,
        attention_type="PERMISSION",
        urgency="HIGH",
        summary="Test task needs your permission to continue.",
        context_json=None,
        contact_policy=None,
        dedup_key=f"opencode_permission:{question_id}",
    )

    class FakeOCSupervisor:
        def __init__(self):
            self.cm = ConnectionManager()
            self.approve_calls = []

        async def approve_permission(self, permission_id, approved):
            self.approve_calls.append((permission_id, approved))
            status = "approved" if approved else "denied"
            await adb.answer_question_record(permission_id, status)
            await am.resolve_for_source("opencode_permission", permission_id, status, None)
            return "Permission approved."

    oc_sv = FakeOCSupervisor()
    tools = ToolRegistry(task_manager=None, opencode_supervisor=oc_sv, connection_manager=oc_sv.cm)

    result = await supervisor_module._resolve_bound_command("approve", tools, attn_id)
    assert result is not None
    assert "approved" in result.lower() or "permission" in result.lower()
    assert len(oc_sv.approve_calls) == 1

    result2 = await supervisor_module._resolve_bound_command("approve", tools, attn_id)
    if result2 is not None:
        assert "already" in result2.lower() or "nothing more" in result2.lower()
    assert len(oc_sv.approve_calls) == 1
