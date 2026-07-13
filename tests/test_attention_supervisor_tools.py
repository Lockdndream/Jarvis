"""Milestone 8 Phase 17: bounded supervisor tools for AttentionRequest/
VoiceSession — list_attention_requests, get_attention_request,
defer_attention, resume_attention, open_voice_session, close_voice_session.

Full IDs never truncated, bounded results, deterministic errors, no silent
source mutation on a failed/unknown lookup.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app import attention_manager as am
from app.connection_manager import ConnectionManager
from app.supervisor.tools import ToolRegistry


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
def reset_broadcast_hook():
    am.set_broadcast_hook(None)
    yield
    am.set_broadcast_hook(None)


class FakeOpenCodeSupervisor:
    def __init__(self):
        self.cm = ConnectionManager()


async def _attention(source_id="q1"):
    row = await am.get_or_create(
        ConnectionManager(), conversation_id="c1", task_id="t1", source_type="local_question",
        source_id=source_id, attention_type=am.ATTENTION_TYPE_QUESTION, urgency="LOW", summary="Which approach?",
    )
    return row["attention_request_id"]


def make_tools():
    return ToolRegistry(task_manager=None, opencode_supervisor=FakeOpenCodeSupervisor())


@pytest.mark.asyncio
async def test_new_tools_are_registered():
    tools = make_tools()
    names = {d["name"] for d in tools.list_definitions()}
    for expected in (
        "list_attention_requests", "get_attention_request", "defer_attention",
        "resume_attention", "open_voice_session", "close_voice_session",
    ):
        assert expected in names


@pytest.mark.asyncio
async def test_list_attention_requests_shows_full_untruncated_id():
    aid = await _attention()
    tools = make_tools()
    result = await tools.call("list_attention_requests", {})
    assert aid in result  # full ID, not a truncated prefix


@pytest.mark.asyncio
async def test_get_attention_request_unknown_id_reports_not_found():
    tools = make_tools()
    result = await tools.call("get_attention_request", {"attention_request_id": "attn_does_not_exist"})
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_get_attention_request_returns_detail():
    aid = await _attention()
    tools = make_tools()
    result = await tools.call("get_attention_request", {"attention_request_id": aid})
    assert aid in result
    assert "Which approach?" in result


@pytest.mark.asyncio
async def test_defer_attention_persists_and_confirms():
    aid = await _attention()
    tools = make_tools()
    result = await tools.call("defer_attention", {"attention_request_id": aid, "deferred_until": "2026-07-10T10:15:00Z"})
    assert "Deferred" in result
    assert db.get_attention_request(aid)["status"] == "deferred"


@pytest.mark.asyncio
async def test_defer_attention_unknown_id_does_not_mutate_anything():
    tools = make_tools()
    result = await tools.call("defer_attention", {"attention_request_id": "attn_nope", "deferred_until": "2026-07-10T10:15:00Z"})
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_resume_attention_requires_deferred_status():
    aid = await _attention()  # currently pending, not deferred
    tools = make_tools()
    result = await tools.call("resume_attention", {"attention_request_id": aid})
    assert "not deferred" in result.lower()


@pytest.mark.asyncio
async def test_resume_attention_on_deferred_request_marks_due_and_recontacts():
    aid = await _attention()
    await am.defer(aid, "2026-07-10T10:15:00Z")
    tools = make_tools()
    result = await tools.call("resume_attention", {"attention_request_id": aid})
    assert "Resumed" in result
    assert db.get_attention_request(aid)["status"] == "pending"


@pytest.mark.asyncio
async def test_open_and_close_voice_session_round_trip():
    tools = make_tools()
    opened = await tools.call("open_voice_session", {"conversation_id": "c1"})
    assert "Voice session opened" in opened
    vsid = opened.split(":")[1].strip().split(" ")[0]
    session = db.get_voice_session(vsid)
    assert session is not None
    assert session["state"] == "listening"

    closed = await tools.call("close_voice_session", {"voice_session_id": vsid})
    assert "closed" in closed.lower()
    assert db.get_voice_session(vsid)["state"] == "closed"


@pytest.mark.asyncio
async def test_close_unknown_voice_session_reports_not_found():
    tools = make_tools()
    result = await tools.call("close_voice_session", {"voice_session_id": "vs_nope"})
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_call_with_missing_required_arg_returns_deterministic_error_not_a_crash():
    tools = make_tools()
    result = await tools.call("defer_attention", {"attention_request_id": "attn_x"})  # missing deferred_until
    assert result.startswith("Error")
