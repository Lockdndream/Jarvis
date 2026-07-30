"""Tests for memory management Supervisor tools."""
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


def make_registry():
    return ToolRegistry()


# ── remember_this ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remember_this_stores_explicit_memory():
    registry = make_registry()
    result = await registry.call("remember_this", {
        "content": "User prefers compact mode",
        "project": "jarvis",
    })
    assert "remember" in result.lower()

    rows = memory.get_memories_by_source("user_explicit", None)
    assert len(rows) == 1
    row = rows[0]
    assert row["category"] == "explicit"
    assert row["source"] == "user_explicit"
    assert row["content"] == "User prefers compact mode"
    assert row["project"] == "jarvis"


@pytest.mark.asyncio
async def test_remember_this_without_project_stores_none():
    registry = make_registry()
    await registry.call("remember_this", {"content": "User likes coffee"})

    rows = memory.get_memories_by_source("user_explicit", None)
    assert len(rows) == 1
    assert rows[0]["project"] is None
    assert rows[0]["content"] == "User likes coffee"


# ── forget_this ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forget_this_by_memory_id_deletes():
    registry = make_registry()
    mem_id = memory.store_memory(
        category="explicit",
        content="Temporary token secret",
        source="user_explicit",
    )

    result = await registry.call("forget_this", {"memory_id": mem_id})
    assert result == "Forgotten."
    assert memory.get_memories_by_source("user_explicit", None) == []


@pytest.mark.asyncio
async def test_forget_this_by_missing_memory_id_is_noop():
    registry = make_registry()
    result = await registry.call("forget_this", {"memory_id": "mem_doesnotexist"})
    assert result == "Forgotten."


@pytest.mark.asyncio
async def test_forget_this_by_query_single_match_deletes():
    registry = make_registry()
    memory.store_memory(
        category="explicit",
        content="User prefers dark mode",
        source="user_explicit",
    )

    result = await registry.call("forget_this", {"query": "dark mode"})
    assert "Forgotten" in result
    assert "User prefers dark mode" in result
    assert memory.get_memories_by_source("user_explicit", None) == []


@pytest.mark.asyncio
async def test_forget_this_by_query_multiple_matches_deletes_neither():
    registry = make_registry()
    memory.store_memory(
        category="explicit",
        content="User prefers dark mode on desktop",
        source="user_explicit",
    )
    memory.store_memory(
        category="explicit",
        content="User prefers dark mode on mobile",
        source="user_explicit",
    )

    result = await registry.call("forget_this", {"query": "dark mode"})
    rows = memory.get_memories_by_source("user_explicit", None)
    assert len(rows) == 2
    assert "2 memories" in result or "found" in result.lower()
    assert "memory_id" in result.lower()


@pytest.mark.asyncio
async def test_forget_this_by_query_no_matches():
    registry = make_registry()
    result = await registry.call("forget_this", {"query": "nonexistent phrase"})
    assert "couldn't find" in result.lower()
    assert "nonexistent phrase" in result


@pytest.mark.asyncio
async def test_forget_this_without_query_or_memory_id_errors():
    registry = make_registry()
    result = await registry.call("forget_this", {})
    assert "query" in result.lower()
    assert "memory_id" in result.lower()


# ── what_do_you_remember ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_what_do_you_remember_lists_memories():
    registry = make_registry()
    mem_id = memory.store_memory(
        category="explicit",
        content="User speaks Spanish",
        source="user_explicit",
    )

    result = await registry.call("what_do_you_remember", {})
    assert mem_id in result
    assert "User speaks Spanish" in result
    assert "explicit" in result


@pytest.mark.asyncio
async def test_what_do_you_remember_filters_by_project():
    registry = make_registry()
    jarvis_id = memory.store_memory(
        category="explicit",
        content="Jarvis deployment target",
        project="jarvis",
        source="user_explicit",
    )
    memory.store_memory(
        category="explicit",
        content="Other project detail",
        project="other",
        source="user_explicit",
    )

    result = await registry.call("what_do_you_remember", {"project": "jarvis"})
    assert jarvis_id in result
    assert "Jarvis deployment target" in result
    assert "Other project detail" not in result


@pytest.mark.asyncio
async def test_what_do_you_remember_empty():
    registry = make_registry()
    result = await registry.call("what_do_you_remember", {})
    assert "don't have any memories" in result.lower()


@pytest.mark.asyncio
async def test_what_do_you_remember_empty_project_filter():
    registry = make_registry()
    result = await registry.call("what_do_you_remember", {"project": "jarvis"})
    assert "don't have any memories" in result.lower()
    assert "jarvis" in result


# ── Tool definitions include memory tools ───────────────────────────


def test_memory_tools_in_definitions():
    registry = make_registry()
    defs = registry.list_definitions()
    names = {d["name"] for d in defs}
    assert "remember_this" in names
    assert "forget_this" in names
    assert "what_do_you_remember" in names


def test_remember_this_schema_requires_content():
    registry = make_registry()
    schema = registry.get("remember_this")["parameters"]
    assert "content" in schema["required"]
    assert "project" not in schema["required"]


def test_forget_this_schema_requires_nothing():
    registry = make_registry()
    schema = registry.get("forget_this")["parameters"]
    assert schema["required"] == []


def test_what_do_you_remember_schema_defaults():
    registry = make_registry()
    schema = registry.get("what_do_you_remember")["parameters"]
    assert "project" not in schema["required"]
    assert "count" not in schema["required"]
    assert schema["properties"]["count"]["default"] == 10
