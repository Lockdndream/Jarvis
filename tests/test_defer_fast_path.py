"""Milestone 8 Phase 18: natural-language deferral fast path.

Deterministic, LLM-free, checked before both bound-command and general
deterministic-command resolution (see Supervisor.process_message). Follows
the same harness pattern as tests/test_voice_fast_paths.py.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app import attention_manager as am
from app.supervisor.supervisor import _resolve_defer_command


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


class _FakeConnManager:
    _connections = []

    def has_user_surfaces(self) -> bool:
        return False

    async def broadcast(self, msg):
        pass


async def _attention(source_id="q1"):
    row = await am.get_or_create(
        _FakeConnManager(), conversation_id="c1", task_id="t1", source_type="local_question",
        source_id=source_id, attention_type=am.ATTENTION_TYPE_QUESTION, urgency="LOW", summary="s",
    )
    return row["attention_request_id"]


@pytest.mark.asyncio
async def test_non_deferral_text_falls_through_to_llm():
    await _attention()
    result = await _resolve_defer_command("Answer B.", None)
    assert result is None


@pytest.mark.asyncio
async def test_unbound_single_active_request_defers_it():
    aid = await _attention()
    result = await _resolve_defer_command("Come back in 15 minutes.", None)
    assert "Okay" in result
    assert db.get_attention_request(aid)["status"] == "deferred"


@pytest.mark.asyncio
async def test_unbound_zero_active_requests_falls_through_to_llm():
    result = await _resolve_defer_command("Come back in 15 minutes.", None)
    assert result is None


@pytest.mark.asyncio
async def test_unbound_multiple_active_requests_asks_and_defers_none():
    aid1 = await _attention("q1")
    aid2 = await _attention("q2")
    result = await _resolve_defer_command("Come back in 15 minutes.", None)
    assert "2" in result
    assert "which" in result.lower()
    assert db.get_attention_request(aid1)["status"] != "deferred"
    assert db.get_attention_request(aid2)["status"] != "deferred"


@pytest.mark.asyncio
async def test_bound_request_defers_exactly_that_one_even_with_others_active():
    bound_aid = await _attention("q1")
    other_aid = await _attention("q2")  # unrelated, must not be touched or ambiguity-checked
    result = await _resolve_defer_command("Come back in 15 minutes.", bound_aid)
    assert "Okay" in result
    assert db.get_attention_request(bound_aid)["status"] == "deferred"
    assert db.get_attention_request(other_aid)["status"] != "deferred"


@pytest.mark.asyncio
async def test_bound_request_already_resolved_reports_instead_of_acting():
    aid = await _attention()
    await am.resolve_for_source("local_question", "q1", "answered", "x")
    result = await _resolve_defer_command("Come back in 15 minutes.", aid)
    assert "already resolved" in result.lower()


@pytest.mark.asyncio
async def test_vague_phrase_asks_for_clarification_and_defers_nothing():
    aid = await _attention()
    result = await _resolve_defer_command("later", None)
    assert result == "When should I come back?"
    assert db.get_attention_request(aid)["status"] != "deferred"


@pytest.mark.asyncio
async def test_confirmation_message_never_guesses_a_time_for_vague_phrase():
    await _attention()
    result = await _resolve_defer_command("not now", None)
    assert "minute" not in result.lower()
    assert "hour" not in result.lower()


@pytest.mark.asyncio
async def test_primary_scenario_phrase_exact_wording():
    aid = await _attention()
    result = await _resolve_defer_command("Not now. Come back in fifteen minutes.", None)
    assert result.startswith("Okay.")
    row = db.get_attention_request(aid)
    assert row["status"] == "deferred"
    assert row["deferred_until"] is not None
