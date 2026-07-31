"""Tests for the catch_me_up supervisor tool."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
import app.memory as memory
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


def make_registry(plan_executor=True):
    return ToolRegistry(plan_executor=plan_executor)


def _set_memory_created_at(mem_id: str, created_at: str) -> None:
    """Backdate a memory for deterministic since-window tests."""
    conn = db.get_conn()
    conn.execute(
        "UPDATE memories SET created_at=? WHERE id=?",
        (created_at, mem_id),
    )
    conn.commit()
    conn.close()


def _make_paused_plan(title: str = "Paused plan") -> str:
    plan_id = "plan_paused_001"
    db.create_plan_record(plan_id, title)
    db.create_plan_step_record(
        "step_001", plan_id, 0,
        description="do something", worker_name="dummy",
    )
    db.update_plan_status(plan_id, "paused", current_step_index=0)
    return plan_id


def _make_failed_plan(title: str = "Failed plan") -> str:
    plan_id = "plan_failed_001"
    db.create_plan_record(plan_id, title)
    db.create_plan_step_record(
        "step_002", plan_id, 0,
        description="do something", worker_name="dummy",
    )
    db.update_plan_status(plan_id, "failed", current_step_index=0)
    return plan_id


@pytest.mark.asyncio
async def test_catch_me_up_no_since_no_prior_conversations():
    registry = make_registry()
    result = await registry.call("catch_me_up", {})
    assert "no recent activity" in result.lower() or "nothing happened" in result.lower()


@pytest.mark.asyncio
async def test_catch_me_up_explicit_since_filters_memories():
    registry = make_registry()

    # Memory before the window.
    old_id = memory.store_memory(
        category="episodic",
        content="Old event before since",
        source="conversation",
        source_id="conv_old",
    )
    _set_memory_created_at(old_id, "2026-07-01T00:00:00Z")

    # Memory after the window.
    memory.store_memory(
        category="episodic",
        content="New event after since",
        source="conversation",
        source_id="conv_new",
    )

    since = "2026-07-15T00:00:00Z"
    result = await registry.call("catch_me_up", {"since": since})
    assert "New event after since" in result
    assert "Old event before since" not in result


@pytest.mark.asyncio
async def test_catch_me_up_defaults_to_since_last_conversation():
    registry = make_registry()
    conv_a = "conv_a1234567890"
    conv_b = "conv_b1234567890"

    # First conversation has two messages; the boundary for the current
    # conversation is the most recent message of the previous conversation.
    db.save_conversation_message(conv_a, "user", "first conversation", trace_id=None)

    memory.store_memory(
        category="episodic",
        content="Middle activity between conversations",
        source="conversation",
        source_id="conv_middle",
    )

    # Second message of conv_a sets the boundary timestamp.
    db.save_conversation_message(conv_a, "assistant", "response", trace_id=None)

    db.save_conversation_message(conv_b, "user", "current conversation", trace_id=None)

    # Memory created after conv_b started should be included.
    recent_id = memory.store_memory(
        category="episodic",
        content="Recent activity after current conversation",
        source="conversation",
        source_id="conv_b",
    )
    _set_memory_created_at(recent_id, db.utcnow())

    result = await registry.call("catch_me_up", {}, conversation_id=conv_b)
    assert "Recent activity after current conversation" in result
    # The middle memory predates the previous conversation's last activity.
    assert "Middle activity between conversations" not in result


@pytest.mark.asyncio
async def test_catch_me_up_mentions_paused_plan():
    registry = make_registry()
    _make_paused_plan("Paused plan alpha")

    result = await registry.call("catch_me_up", {})
    assert "Paused plan alpha" in result
    assert "paused" in result.lower()
    assert "retry" in result.lower()
    assert "skip" in result.lower()
    assert "abort" in result.lower()


@pytest.mark.asyncio
async def test_catch_me_up_mentions_failed_plan():
    registry = make_registry()
    _make_failed_plan("Failed plan beta")

    result = await registry.call("catch_me_up", {})
    assert "Failed plan beta" in result
    assert "failed" in result.lower()


@pytest.mark.asyncio
async def test_catch_me_up_escalation_not_tied_to_plan_is_mentioned():
    registry = make_registry()
    db.create_attention_request(
        attention_request_id="attn_001",
        conversation_id=None,
        task_id=None,
        source_type="task_failure",
        source_id="task_001",
        attention_type="SUPERVISOR_ESCALATION",
        urgency="HIGH",
        summary="A standalone task failed escalation",
        context_json=None,
        contact_policy=None,
        dedup_key="task_failure:task_001",
    )

    result = await registry.call("catch_me_up", {})
    assert "A standalone task failed escalation" in result


@pytest.mark.asyncio
async def test_catch_me_up_escalation_tied_to_paused_plan_is_not_duplicated():
    registry = make_registry()
    plan_id = _make_paused_plan("Paused plan gamma")
    # This escalation is tied to the same paused plan via task_id.
    db.create_attention_request(
        attention_request_id="attn_002",
        conversation_id=None,
        task_id=plan_id,
        source_type="plan_step",
        source_id="step_001",
        attention_type="SUPERVISOR_ESCALATION",
        urgency="HIGH",
        summary="Paused plan gamma escalation",
        context_json=None,
        contact_policy=None,
        dedup_key="plan_step:step_001",
    )

    result = await registry.call("catch_me_up", {})
    assert "Paused plan gamma" in result
    # The escalation's summary should not appear because the paused plan already covers it.
    assert result.count("Paused plan gamma") == 1


@pytest.mark.asyncio
async def test_catch_me_up_registered_and_callable():
    registry = make_registry()
    defs = registry.list_definitions()
    names = {d["name"] for d in defs}
    assert "catch_me_up" in names

    schema = registry.get("catch_me_up")["parameters"]
    assert "since" in schema["properties"]
    assert "project" in schema["properties"]
    assert "conversation_id" not in schema["properties"]
    assert schema["required"] == []

    result = await registry.call("catch_me_up", {})
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_catch_me_up_call_without_conversation_id_still_works():
    """Backward compatibility: old callers omit conversation_id entirely."""
    registry = make_registry()
    result = await registry.call("catch_me_up", {})
    assert "no recent activity" in result.lower() or "nothing happened" in result.lower()


@pytest.mark.asyncio
async def test_catch_me_up_other_tools_ignore_conversation_id():
    """Only catch_me_up receives conversation_id; other tools are unaffected."""
    registry = make_registry()
    result = await registry.call("what_do_you_remember", {}, conversation_id="conv_any")
    assert "don't have any memories" in result.lower()
