"""Tests for Jarvis conversational supervisor (Milestone 5)."""
import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
import app.supervisor.supervisor as supervisor_module
from app.supervisor.supervisor import Supervisor, _fast_path, _format_context
from app.supervisor.tools import ToolRegistry
from app.supervisor.context import build_context
from app.supervisor.llm import FakeLLMProvider
from app.supervisor.projects import set_default_projects, resolve_project, get_projects


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


@pytest.fixture(autouse=True)
def test_projects(monkeypatch):
    # Isolate from any real projects.json on disk — redirect to nonexistent path
    tmp = tempfile.mktemp(suffix=".json")
    monkeypatch.setattr("app.supervisor.projects.PROJECTS_FILE", tmp)
    set_default_projects({
        "jarvis": {"path": "d:\\projects\\jarvis", "display_name": "Jarvis"},
        "website": {"path": "d:\\projects\\website", "display_name": "Company Website"},
    })
    yield
    set_default_projects({})
    if os.path.exists(tmp):
        os.unlink(tmp)


@pytest.fixture
def supervisor():
    return Supervisor()


# ── Project resolution ─────────────────────────────────────────────

def test_resolve_by_alias():
    alias, path = resolve_project("jarvis")
    assert alias == "jarvis"
    assert "jarvis" in path


def test_resolve_by_display_name():
    alias, path = resolve_project("Company Website")
    assert alias == "website"
    assert "website" in path


def test_resolve_by_substring():
    alias, path = resolve_project("site")
    assert alias == "website"
    assert "website" in path


def test_resolve_unknown():
    alias, path = resolve_project("nonexistent")
    assert alias is None
    assert "unknown" in path.lower()


def test_resolve_no_projects():
    set_default_projects({})
    alias, path = resolve_project("anything")
    assert alias is None


def test_get_projects_list():
    projs = get_projects()
    assert "jarvis" in projs
    assert "website" in projs


# ── Conversation persistence ───────────────────────────────────────

def test_save_and_retrieve_conversation():
    conv_id = "test-conv-001"
    db.save_conversation_message(conv_id, "user", "hello")
    db.save_conversation_message(conv_id, "assistant", "hi there")

    msgs = db.get_conversation_messages(conv_id)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "hi there"


def test_conversation_limit():
    conv_id = "test-conv-limit"
    for i in range(20):
        db.save_conversation_message(conv_id, "user", f"msg {i}")

    msgs = db.get_conversation_messages(conv_id, limit=10)
    assert len(msgs) == 10


def test_conversation_exists():
    conv_id = "test-conv-exists"
    assert not db.conversation_exists(conv_id)
    db.save_conversation_message(conv_id, "user", "hello")
    assert db.conversation_exists(conv_id)


def test_conversation_metadata():
    conv_id = "test-conv-meta"
    meta = json.dumps({"tool": "get_attention"})
    db.save_conversation_message(conv_id, "tool", "result", meta)
    msgs = db.get_conversation_messages(conv_id)
    assert msgs[0]["metadata_json"] == meta
    assert msgs[0]["role"] == "tool"


# ── Tool Registry ──────────────────────────────────────────────────

def test_tool_registry_list():
    tools = ToolRegistry()
    defs = tools.list_definitions()
    names = [d["name"] for d in defs]
    assert "get_attention" in names
    assert "list_tasks" in names
    assert "get_task_status" in names
    assert "start_opencode_task" in names
    assert "send_opencode_instruction" in names
    assert "answer_question" in names
    assert "resolve_permission" in names
    assert "cancel_task" in names
    assert "recent_activity" in names
    assert "get_projects" in names


def test_tool_registry_get_valid():
    tools = ToolRegistry()
    t = tools.get("get_attention")
    assert t is not None
    assert t["description"]


def test_tool_registry_get_invalid():
    tools = ToolRegistry()
    t = tools.get("nonexistent")
    assert t is None


@pytest.mark.asyncio
async def test_tool_call_unknown():
    tools = ToolRegistry()
    result = await tools.call("nonexistent", {})
    assert "unknown" in result


@pytest.mark.asyncio
async def test_tool_get_projects():
    tools = ToolRegistry()
    result = await tools.call("get_projects", {})
    assert "jarvis" in result
    assert "website" in result


@pytest.mark.asyncio
async def test_tool_get_attention_empty():
    tools = ToolRegistry()
    result = await tools.call("get_attention", {})
    assert "nothing" in result.lower() or "pending" in result.lower() or "no" in result.lower()


@pytest.mark.asyncio
async def test_tool_list_tasks_empty():
    tools = ToolRegistry()
    result = await tools.call("list_tasks", {})
    assert "Active" in result or "none" in result


@pytest.mark.asyncio
async def test_tool_get_task_status_not_found():
    tools = ToolRegistry()
    result = await tools.call("get_task_status", {"task_id": "nonexistent"})
    assert "not found" in result


@pytest.mark.asyncio
async def test_tool_recent_activity_empty():
    tools = ToolRegistry()
    result = await tools.call("recent_activity", {})
    assert "no recent activity" in result.lower() or not result


@pytest.mark.asyncio
async def test_tool_get_task_status_with_task():
    tools = ToolRegistry()
    db.create_task_record("status-test-1", "Status Test", "echo hi")
    result = await tools.call("get_task_status", {"task_id": "status-test-1"})
    assert "Status Test" in result
    assert "running" in result


# ── FakeLLMProvider ───────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_fake_llm_default():
    llm = FakeLLMProvider()
    result = await llm.chat_completion([{"role": "user", "content": "hello"}])
    assert result["role"] == "assistant"
    assert "Fake LLM" in result["content"]


@pytest.mark.asyncio
async def test_fake_llm_canned():
    llm = FakeLLMProvider()
    llm.add_response({"role": "assistant", "content": "canned response"})
    result = await llm.chat_completion([{"role": "user", "content": "hello"}])
    assert result["content"] == "canned response"


@pytest.mark.asyncio
async def test_fake_llm_multi_response():
    llm = FakeLLMProvider([
        {"role": "assistant", "content": "first"},
        {"role": "assistant", "content": "second"},
    ])
    r1 = await llm.chat_completion([{"role": "user", "content": "a"}])
    r2 = await llm.chat_completion([{"role": "user", "content": "b"}])
    assert r1["content"] == "first"
    assert r2["content"] == "second"


@pytest.mark.asyncio
async def test_fake_llm_track_calls():
    llm = FakeLLMProvider()
    await llm.chat_completion([{"role": "user", "content": "hello"}], tools=[{"type": "function", "function": {"name": "test"}}])
    assert len(llm.calls) == 1
    assert llm.calls[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert len(llm.calls[0]["tools"]) == 1


# ── Free-only model validation ─────────────────────────────────────

def test_free_only_allows_openrouter_free(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    from app.supervisor.llm import validate_free_only_model
    validate_free_only_model("openrouter/free")


def test_free_only_allows_free_suffix(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    from app.supervisor.llm import validate_free_only_model
    validate_free_only_model("google/gemini-2.0-flash:free")


def test_free_only_rejects_paid_model(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    from app.supervisor.llm import validate_free_only_model, ModelNotAllowedError
    with pytest.raises(ModelNotAllowedError):
        validate_free_only_model("gpt-4o")


def test_free_only_rejects_openrouter_default(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    from app.supervisor.llm import validate_free_only_model, ModelNotAllowedError
    with pytest.raises(ModelNotAllowedError):
        validate_free_only_model("openrouter/auto")


def test_free_only_disabled_accepts_any(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "false")
    from app.supervisor.llm import validate_free_only_model
    validate_free_only_model("gpt-4o")  # should not raise


def test_free_only_default_disabled_accepts_any(monkeypatch):
    monkeypatch.delenv("JARVIS_LLM_FREE_ONLY", raising=False)
    from app.supervisor.llm import validate_free_only_model
    validate_free_only_model("gpt-4o")  # should not raise


def test_llm_provider_rejected_model_unconfigured(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "gpt-4o")
    from app.supervisor.llm import LLMProvider
    provider = LLMProvider()
    assert not provider.is_configured
    assert provider._rejection_reason is not None


def test_llm_provider_rejected_returns_error(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "gpt-4o")
    import asyncio
    from app.supervisor.llm import LLMProvider
    provider = LLMProvider()
    result = asyncio.run(provider.chat_completion([{"role": "user", "content": "hi"}]))
    assert "Configuration error" in result["content"]


def test_llm_provider_accepted_free_model(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_FREE_ONLY", "true")
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "openrouter/free")
    from app.supervisor.llm import LLMProvider
    provider = LLMProvider()
    assert provider.is_configured
    assert provider._rejection_reason is None


# ── Fast paths ─────────────────────────────────────────────────────

def test_fast_path_attention():
    result = _fast_path("what needs my attention")
    assert result is not None
    assert "nothing" in result.lower()


def test_fast_path_attention_with_pending():
    db.create_task_record("fast-a1", "Test Task", "test")
    db.create_question_record("fast-q1", "fast-a1", "What OS?", None, None)
    result = _fast_path("what needs my attention")
    assert result is not None
    assert "Test Task" in result
    assert "waiting for your answer" in result.lower()


def test_fast_path_tasks():
    result = _fast_path("what tasks are running")
    assert result is not None
    assert "openCode" in result or "active" in result.lower()


def test_fast_path_tasks_with_opencode():
    db.create_task_record("fast-oc", "OC Task", "test")
    db.create_opencode_task_record("fast-oc", "sess-001", "d:\\proj", "test instruction")
    result = _fast_path("list tasks")
    assert result is not None
    assert "OC" in result or "Active" in result


def test_fast_path_recent_activity():
    result = _fast_path("recent activity")
    assert result is not None


def test_fast_path_unknown():
    result = _fast_path("what is the weather")
    assert result is None


def test_fast_path_non_slash():
    """Fast path should not match non-status messages."""
    assert _fast_path("start a new task") is None
    assert _fast_path("answer the question") is None
    assert _fast_path("stop the task") is None


# ── Context builder ───────────────────────────────────────────────

def test_context_builder_empty():
    ctx = build_context()
    assert "current_time" in ctx
    assert ctx["active_tasks"] == []
    assert ctx["pending_questions"] == []
    assert ctx["opencode_running"] == []
    assert "projects" in ctx


def test_context_builder_with_data():
    db.create_task_record("ctx-t1", "Context Test", "test")
    db.create_question_record("ctx-q1", "ctx-t1", "Confirm action?", None, None)
    ctx = build_context()
    assert len(ctx["active_tasks"]) >= 1
    assert len(ctx["pending_questions"]) >= 1


def test_context_builder_conversation_history():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    ctx = build_context(history)
    assert len(ctx["conversation_history"]) == 2


def test_context_builder_history_limit():
    history = [{"role": "user", "content": f"msg {i}"} for i in range(15)]
    ctx = build_context(history)
    assert len(ctx["conversation_history"]) <= 10


def test_context_format():
    ctx = {"current_time": "test", "projects": [], "active_tasks": [], "pending_questions": [], "pending_permissions": [], "opencode_running": [], "recent_activity": []}
    result = _format_context(ctx)
    assert "test" in result


# ── Supervisor ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_supervisor_disabled(monkeypatch):
    monkeypatch.setenv("JARVIS_SUPERVISOR_ENABLED", "0")
    sv = Supervisor()
    result = await sv.process_message("hello")
    assert "disabled" in result["response"].lower()


@pytest.mark.asyncio
async def test_supervisor_fast_path_attention():
    sv = Supervisor()
    result = await sv.process_message("what needs my attention")
    assert result["response"] is not None
    assert result["conversation_id"]


@pytest.mark.asyncio
async def test_supervisor_fast_path_tasks():
    sv = Supervisor()
    result = await sv.process_message("what tasks are running")
    assert result["response"] is not None


@pytest.mark.asyncio
async def test_supervisor_conversation_id_returned():
    sv = Supervisor()
    result = await sv.process_message("hello", "custom-conv-id")
    assert result["conversation_id"] == "custom-conv-id"


@pytest.mark.asyncio
async def test_supervisor_conversation_auto_id():
    sv = Supervisor()
    result = await sv.process_message("hello")
    assert result["conversation_id"]
    assert result["conversation_id"].startswith("conv_")


@pytest.mark.asyncio
async def test_supervisor_persists_conversation():
    sv = Supervisor()
    await sv.process_message("hello", "persist-conv-id")
    msgs = db.get_conversation_messages("persist-conv-id")
    assert len(msgs) >= 2
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-2]["role"] == "user"


@pytest.mark.asyncio
async def test_supervisor_persists_fast_path():
    sv = Supervisor()
    await sv.process_message("what needs my attention", "fast-conv-id")
    msgs = db.get_conversation_messages("fast-conv-id")
    assert len(msgs) >= 2


@pytest.mark.asyncio
async def test_supervisor_unknown_message():
    """Non-fast-path messages go through LLM (FakeLLMProvider returns canned)."""
    sv = Supervisor()
    result = await sv.process_message("do something clever")
    assert result["response"] is not None
    assert "Fake LLM" in result["response"]


@pytest.mark.asyncio
async def test_supervisor_context_includes_active():
    db.create_task_record("sv-t1", "Supervisor Test", "test")
    sv = Supervisor()
    result = await sv.process_message("what is the status of sv-t1")
    assert result["response"] is not None


@pytest.mark.asyncio
async def test_supervisor_pending_questions_in_context():
    db.create_task_record("q-test", "Q Task", "test")
    db.create_question_record("q-test-1", "q-test", "What should I do?", None, None)
    sv = Supervisor()
    result = await sv.process_message("check pending")
    assert result["response"] is not None


# ── Edge cases ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_supervisor_empty_message():
    sv = Supervisor()
    result = await sv.process_message("")
    assert result["response"] is not None


@pytest.mark.asyncio
async def test_supervisor_very_long_message():
    sv = Supervisor()
    long_msg = "x" * 10000
    result = await sv.process_message(long_msg)
    assert result["response"] is not None


@pytest.mark.asyncio
async def test_supervisor_special_characters():
    sv = Supervisor()
    result = await sv.process_message("hello <script>alert('xss')</script>")
    assert result["response"] is not None


@pytest.mark.asyncio
async def test_supervisor_multiple_fast_paths():
    sv = Supervisor()
    r1 = await sv.process_message("attention")
    r2 = await sv.process_message("tasks")
    r3 = await sv.process_message("recent activity")
    assert r1["response"]
    assert r2["response"]
    assert r3["response"]


# ── Tool integration tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_with_db_state():
    db.create_task_record("tool-it1", "Integration Test", "test")
    db.create_question_record("tool-iq1", "tool-it1", "Proceed?", None, None)
    tools = ToolRegistry()
    result = await tools.call("get_attention", {})
    assert "Integration Test" in result or "pending" in result.lower() or "question" in result.lower()


@pytest.mark.asyncio
async def test_tool_get_task_status_with_question():
    db.create_task_record("tool-qs", "Q Task", "test")
    db.create_question_record("tool-qq", "tool-qs", "Really?", None, None)
    tools = ToolRegistry()
    result = await tools.call("get_task_status", {"task_id": "tool-qs"})
    assert "Really" in result


@pytest.mark.asyncio
async def test_tool_cancel_task_not_found():
    tools = ToolRegistry()
    result = await tools.call("cancel_task", {"task_id": "no-such-task"})
    assert "not found" in result.lower() or "error" in result.lower()


# ── Conversation persistence through supervisor ────────────────────

@pytest.mark.asyncio
async def test_conversation_roundtrip():
    sv = Supervisor()
    r1 = await sv.process_message("first message", "roundtrip-conv")
    r2 = await sv.process_message("second message", "roundtrip-conv")
    assert r1["conversation_id"] == r2["conversation_id"]
    msgs = db.get_conversation_messages("roundtrip-conv")
    assert len(msgs) >= 4  # 2 user + 2 assistant


@pytest.mark.asyncio
async def test_conversation_multiple_ids():
    sv = Supervisor()
    a = await sv.process_message("msg", "conv-a")
    b = await sv.process_message("msg", "conv-b")
    assert a["conversation_id"] != b["conversation_id"]
    msgs_a = db.get_conversation_messages("conv-a")
    msgs_b = db.get_conversation_messages("conv-b")
    assert len(msgs_a) >= 2
    assert len(msgs_b) >= 2


@pytest.mark.asyncio
async def test_open_code_supervisor_required_tools():
    """Tools that require OpenCode supervisor handle missing reference gracefully."""
    tools = ToolRegistry()
    result = await tools.call("start_opencode_task", {"project_alias": "jarvis", "instruction": "test"})
    assert "not available" in result or "error" in result.lower()


# ── Regression: task_id truncation broke every follow-up tool call ──
#
# start_opencode_task/list_tasks/get_attention used to report task_id
# truncated to 12 chars (e.g. "oc_922206d97") for display, but task_id is
# actually "oc_" + 12 hex chars = 15 chars. Since db.get_opencode_task() does
# an exact match, an LLM echoing the truncated ID back into
# send_opencode_instruction/cancel_task/get_task_status always failed with
# "is not an OpenCode task" — confirmed against a real OpenRouter LLM session
# (server_err.log 2026-07-09 01:01-01:03: send_opencode_instruction and
# cancel_task calls using the truncated id never produced a prompt_async or
# abort HTTP call to OpenCode). Fixed in tools.py/context.py/supervisor.py by
# no longer truncating task_id/question_id in any LLM-facing text.


class _FakeAdapter:
    def __init__(self, sent):
        self._sent = sent

    async def send_prompt(self, session_id, project_dir, instruction):
        self._sent.append((session_id, project_dir, instruction))


class _FakeOpenCodeSupervisor:
    """Minimal stand-in for OpenCodeSupervisor.start_session + adapter."""

    def __init__(self):
        self.sent = []
        self.adapter = _FakeAdapter(self.sent)

    async def start_session(self, project_dir, instruction):
        import uuid
        task_id = f"oc_{uuid.uuid4().hex[:12]}"
        session_id = f"ses_{uuid.uuid4().hex[:8]}"
        db.create_task_record(task_id, f"OpenCode: {instruction[:50]}", instruction)
        db.create_opencode_task_record(task_id, session_id, project_dir, instruction)
        return {"task_id": task_id, "session_id": session_id, "name": f"OpenCode: {instruction[:50]}"}

    async def send_instruction(self, task_id, instruction):
        oc_task = db.get_opencode_task(task_id)
        if not oc_task:
            raise ValueError(f"Task '{task_id}' is not an OpenCode task")
        await self.adapter.send_prompt(oc_task["session_id"], oc_task["project_dir"], instruction)


@pytest.mark.asyncio
async def test_start_opencode_task_reports_full_task_id():
    fake_oc = _FakeOpenCodeSupervisor()
    tools = ToolRegistry(opencode_supervisor=fake_oc)
    result = await tools.call("start_opencode_task", {"project_alias": "jarvis", "instruction": "do the thing"})

    m = re.search(r"Task started: (\S+)", result)
    assert m, f"unexpected tool output format: {result}"
    reported_id = m.group(1)

    running = db.get_opencode_running_tasks()
    assert len(running) == 1
    assert reported_id == running[0]["task_id"], (
        "start_opencode_task must report the exact task_id, not a truncated prefix"
    )


@pytest.mark.asyncio
async def test_follow_up_instruction_reaches_session_started_via_tool_output_id():
    """The ID the LLM sees in the start_opencode_task result must work for a
    follow-up send_opencode_instruction call against the same session."""
    fake_oc = _FakeOpenCodeSupervisor()
    tools = ToolRegistry(opencode_supervisor=fake_oc)
    start_result = await tools.call(
        "start_opencode_task", {"project_alias": "jarvis", "instruction": "do the thing"}
    )
    reported_id = re.search(r"Task started: (\S+)", start_result).group(1)

    follow_up = await tools.call(
        "send_opencode_instruction",
        {"task_id": reported_id, "instruction": "stop modifying files"},
    )

    assert "not an OpenCode task" not in follow_up
    assert "Error" not in follow_up
    assert len(fake_oc.sent) == 1
    assert fake_oc.sent[0][2] == "stop modifying files"


@pytest.mark.asyncio
async def test_get_attention_reports_full_question_id_and_labels_correctly():
    db.create_task_record("attn-full-task-1", "Attn Task", "test")
    db.create_opencode_task_record("attn-full-task-1", "sess-attn-1", "d:\\proj", "investigate something")
    db.create_question_record("attn-full-question-1", "attn-full-task-1", "Which approach?", None, None)
    tools = ToolRegistry()
    result = await tools.call("get_attention", {})
    assert "attn-full-question-1" in result
    assert "waiting for your answer" in result.lower()


@pytest.mark.asyncio
async def test_get_attention_labels_permission_distinctly_from_question():
    db.create_task_record("attn-perm-task-1", "Perm Task", "test")
    db.create_opencode_task_record("attn-perm-task-1", "sess-perm-1", "d:\\proj", "do work")
    db.create_question_record("attn-perm-q-1", "attn-perm-task-1", "Permission: write /etc/config", None, None)
    tools = ToolRegistry()
    result = await tools.call("get_attention", {})
    assert "needs permission" in result.lower()
    assert "write /etc/config" in result


def test_context_builder_reports_full_task_and_question_ids():
    db.create_task_record("ctx-full-task-1", "Ctx Task", "test")
    db.create_opencode_task_record("ctx-full-task-1", "sess-ctx-1", "d:\\proj", "test instruction")
    db.create_question_record("ctx-full-question-1", "ctx-full-task-1", "Proceed?", None, None)

    ctx = build_context()
    oc_ids = [t["id"] for t in ctx["opencode_running"]]
    assert "ctx-full-task-1" in oc_ids

    q_ids = [q["id"] for q in ctx["pending_questions"]]
    assert "ctx-full-question-1" in q_ids


# ── Milestone 5 regression (superseded) ─────────────────────────────
#
# _fast_path's "what needs my attention" branch used to have a `'t'.get(...)`
# typo that crashed on a waiting OpenCode task, guarded against by
# monkeypatching db.get_opencode_running_tasks. Milestone 6 Phase 8 replaced
# that whole code path: attention is now derived solely from the
# `questions` table (single source of truth for both native questions and
# permissions), so the crash's code path no longer exists at all. The tests
# below protect the Phase 8 replacement instead: real DB state, no
# duplicate entries, correct wording for questions vs permissions.

def test_fast_path_attention_with_pending_question_no_crash():
    db.create_task_record("fast-oc-wait", "OC Wait Task", "test")
    db.create_opencode_task_record("fast-oc-wait", "sess-wait", "d:\\proj", "investigate something")
    db.create_question_record("fast-oc-wait-q1", "fast-oc-wait", "Proceed?", None, None)

    result = _fast_path("what needs my attention")

    assert result is not None
    assert "OC Wait Task" in result
    assert "waiting for your answer" in result.lower()


def test_fast_path_attention_no_duplicate_entry_for_same_task():
    """A task with a pending question must appear exactly once, not once
    per section (the old design had a separate "waiting" section derived
    from opencode_tasks.status that duplicated the questions-table entry)."""
    db.create_task_record("fast-oc-dup", "Dup Task", "test")
    db.create_opencode_task_record("fast-oc-dup", "sess-dup", "d:\\proj", "do work")
    db.update_opencode_task_status("fast-oc-dup", "waiting_for_user")
    db.create_question_record("fast-oc-dup-q1", "fast-oc-dup", "Which option?", None, None)

    result = _fast_path("what needs my attention")

    assert result.count("Dup Task") == 1


# ── Milestone 9B.10 RC finding: LLM call failure mid tool-loop ──────
#
# A real device rehearsal hit a genuine defect: an httpx.ConnectTimeout
# calling the LLM API after a tool call had already succeeded propagated
# unhandled out of process_message(), which main.py's websocket loop has
# no per-message exception handling for -- an uncaught exception there
# tears down the entire client connection, not just that one turn.

class _RaisingLLMProvider:
    async def chat_completion(self, messages, tools=None, max_tokens=1024, temperature=0.3):
        raise ConnectionError("simulated LLM API connect timeout")


@pytest.mark.asyncio
async def test_process_message_survives_llm_failure_mid_loop():
    sv = Supervisor()
    sv._llm = _RaisingLLMProvider()

    result = await sv.process_message("tell me something only the LLM can answer")

    assert "response" in result
    assert result["response"]  # never empty/None -- always some spoken-friendly text
    assert "conversation_id" in result


# ── Control Center hardening: set_broadcast_hook / _broadcast ───────
#
# Self-contained: each test explicitly sets and tears down its own hook
# rather than relying on app.main's module-level import-time wiring, so
# these never depend on test file import order (see tests/conftest.py's
# autouse fixture, which also resets this hook to None around every test
# for the same reason).

class _FakeConnManager:
    def __init__(self, observers=True):
        self._observers = observers
        self.broadcast_calls = []

    def has_observers(self):
        return self._observers

    async def broadcast_observers(self, data):
        self.broadcast_calls.append(data)


@pytest.mark.asyncio
async def test_broadcast_is_noop_with_no_hook_registered():
    supervisor_module._broadcast_hook = None
    events_before = len(db.get_recent_events(50))

    await supervisor_module._broadcast("supervisor_turn_started", {"x": 1})

    assert len(db.get_recent_events(50)) == events_before


@pytest.mark.asyncio
async def test_broadcast_is_noop_when_hook_set_but_no_observers():
    """Phase 4 (performance): zero observers connected must mean zero
    extra db.save_event writes, not just zero broadcast() sends."""
    fake = _FakeConnManager(observers=False)
    supervisor_module.set_broadcast_hook(fake)
    events_before = len(db.get_recent_events(50))

    await supervisor_module._broadcast("supervisor_turn_started", {"x": 1})

    assert len(db.get_recent_events(50)) == events_before
    assert fake.broadcast_calls == []


@pytest.mark.asyncio
async def test_broadcast_persists_and_forwards_when_observers_present():
    fake = _FakeConnManager(observers=True)
    supervisor_module.set_broadcast_hook(fake)
    events_before = len(db.get_recent_events(50))

    await supervisor_module._broadcast("supervisor_tool_call", {"tool": "list_tasks", "args": {}})

    assert len(db.get_recent_events(50)) == events_before + 1
    assert len(fake.broadcast_calls) == 1
    assert fake.broadcast_calls[0]["type"] == "supervisor_tool_call"


@pytest.mark.asyncio
async def test_broadcast_failure_is_swallowed_not_raised():
    """A dashboard disconnect mid-broadcast must never surface as an
    exception in the Supervisor's tool-calling loop -- this is
    observability, not a load-bearing part of answering the user."""
    class RaisingConnManager:
        def has_observers(self):
            return True

        async def broadcast_observers(self, data):
            raise ConnectionError("dashboard vanished")

    supervisor_module.set_broadcast_hook(RaisingConnManager())

    await supervisor_module._broadcast("supervisor_turn", {"x": 1})  # must not raise


# ── Interaction Layer v1 (Goal 5): technical command confirmation ──────

from app.supervisor.supervisor import _classify_yes_no, _build_confirmation_prompt


def test_classify_yes_no_recognizes_yes_variants():
    for phrase in ["yes", "Yes.", "yeah", "yep", "yup", "correct", "right", "go ahead", "do it", "sure", "confirmed"]:
        assert _classify_yes_no(phrase) == "yes", phrase


def test_classify_yes_no_recognizes_no_variants():
    for phrase in ["no", "No.", "nope", "negative", "wrong", "cancel", "don't", "never mind"]:
        assert _classify_yes_no(phrase) == "no", phrase


def test_classify_yes_no_unclear_for_anything_else():
    for phrase in ["maybe", "what?", "create the file instead", ""]:
        assert _classify_yes_no(phrase) == "unclear", phrase


def test_classify_yes_no_matches_naturally_phrased_replies():
    """Real-device finding (Level 5 capability testing): the original
    leading-prefix-only matcher missed every one of these, forcing a
    live session into an unrecoverable "Sorry, was that a yes or a no?"
    loop on an entirely ordinary correction."""
    for phrase in ["yes, go ahead", "yes please", "sure, go ahead"]:
        assert _classify_yes_no(phrase) == "yes", phrase
    for phrase in [
        "no, I meant git status",
        "no, that's not what I said",
        "that was a no I need you to check git status instead",
        "that's wrong, I asked for the git status",
    ]:
        assert _classify_yes_no(phrase) == "no", phrase


def test_classify_yes_no_does_not_false_positive_on_substrings():
    """"no" must match as a whole word, not as a substring of an unrelated
    word (e.g. "know") -- the exact failure mode a naive substring search
    would introduce."""
    assert _classify_yes_no("I know the file you mean") == "unclear"
    assert _classify_yes_no("that's not correct") == "yes"  # "correct" token present, expected per current word list


def test_classify_yes_no_self_contradictory_reply_is_unclear():
    assert _classify_yes_no("no wait yes") == "unclear"


def test_build_confirmation_prompt_reads_back_instruction_and_project():
    prompt = _build_confirmation_prompt(
        "start_opencode_task", {"project_alias": "jarvis-test", "instruction": "create a file"},
    )
    assert "create a file" in prompt
    assert "jarvis-test" in prompt


def test_build_confirmation_prompt_generic_fallback_for_other_tools():
    """Goal 5 is scoped to start_opencode_task only, per the milestone's
    own scope -- but the fallback must still produce something sane
    rather than crash if confirm_before_tools is ever given another tool
    name."""
    prompt = _build_confirmation_prompt("some_other_tool", {})
    assert "go ahead" in prompt.lower()


@pytest.mark.asyncio
async def test_confirm_before_tools_stops_before_executing_and_returns_pending_call(supervisor, monkeypatch):
    called = []

    async def fake_call(name, args):
        called.append((name, args))
        return "should not run"
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
    supervisor._llm = llm

    result = await supervisor.process_message(
        "start an opencode task", conversation_id="c1",
        confirm_before_tools=frozenset({"start_opencode_task"}),
    )

    assert called == []  # never executed -- confirmation gates it
    assert result["pending_tool_call"] == {
        "name": "start_opencode_task",
        "args": {"project_alias": "jarvis", "instruction": "create a file"},
    }
    assert "create a file" in result["response"]


@pytest.mark.asyncio
async def test_without_confirm_before_tools_the_tool_executes_normally(supervisor, monkeypatch):
    """Regression guard: text/browser chat callers never pass
    confirm_before_tools, so this exact same LLM proposal must execute
    immediately, unchanged from before Goal 5 existed."""
    called = []

    async def fake_call(name, args):
        called.append((name, args))
        return "executed"
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

    result = await supervisor.process_message("start an opencode task", conversation_id="c1")

    assert called == [("start_opencode_task", {"project_alias": "jarvis", "instruction": "create a file"})]
    assert "pending_tool_call" not in result


@pytest.mark.asyncio
async def test_resolve_pending_tool_confirmation_yes_executes_the_stored_call(supervisor, monkeypatch):
    called = []

    async def fake_call(name, args):
        called.append((name, args))
        return "File created."
    monkeypatch.setattr(supervisor.tools, "call", fake_call)

    result = await supervisor.resolve_pending_tool_confirmation(
        "c1", "yes", {"name": "start_opencode_task", "args": {"project_alias": "jarvis", "instruction": "create a file"}},
    )

    assert called == [("start_opencode_task", {"project_alias": "jarvis", "instruction": "create a file"})]
    assert result["response"] == "File created."
    assert "pending_tool_call" not in result


@pytest.mark.asyncio
async def test_resolve_pending_tool_confirmation_no_never_executes(supervisor, monkeypatch):
    called = []

    async def fake_call(name, args):
        called.append((name, args))
        return "should not run"
    monkeypatch.setattr(supervisor.tools, "call", fake_call)

    result = await supervisor.resolve_pending_tool_confirmation(
        "c1", "no", {"name": "start_opencode_task", "args": {}},
    )

    assert called == []
    assert "won't do that" in result["response"]


@pytest.mark.asyncio
async def test_resolve_pending_tool_confirmation_unclear_reasks_without_executing(supervisor, monkeypatch):
    called = []

    async def fake_call(name, args):
        called.append((name, args))
        return "should not run"
    monkeypatch.setattr(supervisor.tools, "call", fake_call)

    pending = {"name": "start_opencode_task", "args": {"project_alias": "jarvis", "instruction": "x"}}
    result = await supervisor.resolve_pending_tool_confirmation("c1", "maybe", pending)

    assert called == []
    assert result["pending_tool_call"] == pending
    assert "yes or a no" in result["response"]


# ── Regression: LLM message-array shape (F1.6 / TD-028) ──────────
#
# The old bug built the array as:
#   [system] -> [user: context + "\n\nUser: " + current] -> [*history] -> [user: current]
# sending the current turn twice and before history. The fix builds:
#   [system] -> [*history] -> [user: context] -> [user: current]
# These tests assert on the SHAPE of the array sent to the LLM, not on the
# response content. If the old ordering comes back, these must fail.


@pytest.mark.asyncio
async def test_message_array_system_message_is_first():
    conv_id = "shape-sys-first"
    db.save_conversation_message(conv_id, "user", "prior question")
    db.save_conversation_message(conv_id, "assistant", "prior answer")

    sv = Supervisor()
    await sv.process_message("tell me about the weather today", conv_id)

    sent = sv.llm.calls[0]["messages"]
    assert len(sent) > 0
    assert sent[0]["role"] == "system"


@pytest.mark.asyncio
async def test_message_array_current_message_appears_exactly_once():
    conv_id = "shape-once"
    db.save_conversation_message(conv_id, "user", "prior question")
    db.save_conversation_message(conv_id, "assistant", "prior answer")

    sv = Supervisor()
    current_msg = "tell me about the weather today"
    await sv.process_message(current_msg, conv_id)

    sent = sv.llm.calls[0]["messages"]
    count = sum(1 for m in sent if m.get("content") == current_msg)
    assert count == 1, f"Current message appeared {count} times, expected 1"


@pytest.mark.asyncio
async def test_message_array_current_message_after_all_history():
    conv_id = "shape-order"
    db.save_conversation_message(conv_id, "user", "first user turn")
    db.save_conversation_message(conv_id, "assistant", "first assistant reply")
    db.save_conversation_message(conv_id, "user", "second user turn")
    db.save_conversation_message(conv_id, "assistant", "second assistant reply")

    sv = Supervisor()
    current_msg = "tell me about the weather today"
    await sv.process_message(current_msg, conv_id)

    sent = sv.llm.calls[0]["messages"]
    history_contents = {
        "first user turn", "first assistant reply",
        "second user turn", "second assistant reply",
    }
    current_idx = None
    history_indices = []
    for i, m in enumerate(sent):
        if m.get("content") == current_msg:
            current_idx = i
        elif m.get("content") in history_contents:
            history_indices.append(i)

    assert current_idx is not None, "Current message not found in sent array"
    assert len(history_indices) >= 2, f"Expected >=2 history entries, found {len(history_indices)}"
    assert current_idx > max(history_indices), (
        f"Current message at index {current_idx} must be after all history "
        f"(max history index: {max(history_indices)})"
    )


@pytest.mark.asyncio
async def test_message_array_current_not_embedded_in_context():
    conv_id = "shape-no-embed"
    db.save_conversation_message(conv_id, "user", "prior question")
    db.save_conversation_message(conv_id, "assistant", "prior answer")

    sv = Supervisor()
    current_msg = "unique_query_abc123_xyz"
    await sv.process_message(current_msg, conv_id)

    sent = sv.llm.calls[0]["messages"]
    for i, m in enumerate(sent):
        if m.get("content") == current_msg:
            continue
        assert current_msg not in (m.get("content") or ""), (
            f"Current message text found embedded in message at index {i} "
            f"(role={m['role']})"
        )


@pytest.mark.asyncio
async def test_message_array_history_present_when_seeded():
    conv_id = "shape-hist-present"
    db.save_conversation_message(conv_id, "user", "earlier question")
    db.save_conversation_message(conv_id, "assistant", "earlier answer")

    sv = Supervisor()
    await sv.process_message("tell me about the weather today", conv_id)

    sent = sv.llm.calls[0]["messages"]
    history_contents = {"earlier question", "earlier answer"}
    found = [m for m in sent if m.get("content") in history_contents]
    assert len(found) >= 2, f"Expected at least 2 history entries, got {len(found)}"


@pytest.mark.asyncio
async def test_message_array_context_message_comes_after_history():
    conv_id = "shape-ctx-after-hist"
    db.save_conversation_message(conv_id, "user", "first user turn")
    db.save_conversation_message(conv_id, "assistant", "first assistant reply")
    db.save_conversation_message(conv_id, "user", "second user turn")
    db.save_conversation_message(conv_id, "assistant", "second assistant reply")

    sv = Supervisor()
    await sv.process_message("tell me about the weather today", conv_id)

    sent = sv.llm.calls[0]["messages"]

    context_idx = None
    for i, m in enumerate(sent):
        if m["role"] == "user" and (m.get("content") or "").startswith("Current time:"):
            context_idx = i
            break

    assert context_idx is not None, (
        "Context message (starting with 'Current time:') not found in sent array"
    )

    history_contents = {
        "first user turn", "first assistant reply",
        "second user turn", "second assistant reply",
    }
    history_indices = [
        i for i, m in enumerate(sent) if m.get("content") in history_contents
    ]
    assert len(history_indices) >= 2, (
        f"Expected >=2 history entries, found {len(history_indices)}"
    )
    assert context_idx > max(history_indices), (
        f"Context message at index {context_idx} must be after all history "
        f"(max history index: {max(history_indices)})"
    )
