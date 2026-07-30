"""Tests for the memory persistence layer (app/memory.py)."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
import app.memory as memory
import app.db_async as db_async


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


# ── Basic CRUD ──────────────────────────────────────────────────────


def test_store_memory_creates_retrievable_row():
    mem_id = memory.store_memory(
        category="explicit",
        content="User prefers dark mode",
        project="jarvis",
        source="user_explicit",
        source_id="conv_abc123",
        metadata='{"confidence": "high"}',
    )
    assert mem_id.startswith("mem_")

    rows = memory.get_memories_by_source("user_explicit", "conv_abc123")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == mem_id
    assert row["category"] == "explicit"
    assert row["project"] == "jarvis"
    assert row["content"] == "User prefers dark mode"
    assert row["source"] == "user_explicit"
    assert row["source_id"] == "conv_abc123"
    assert row["metadata"] == '{"confidence": "high"}'
    assert row["created_at"] == row["updated_at"]
    assert row["expires_at"] is None


def test_store_memory_truncates_long_content():
    long_content = "x" * 5000
    mem_id = memory.store_memory(category="episodic", content=long_content)
    row = memory.get_memories_by_source("conversation", None)[0]
    assert row["id"] == mem_id
    assert row["content"].endswith("... (truncated)")
    assert len(row["content"]) <= memory.CONTENT_MAX


# ── Retrieval ───────────────────────────────────────────────────────


def test_retrieve_memories_finds_by_keyword():
    memory.store_memory(category="episodic", content="The authentication module uses OAuth2")
    results = memory.retrieve_memories("authentication")
    assert len(results) >= 1
    assert any("OAuth2" in r["content"] for r in results)


def test_retrieve_memories_fts_injection_safety():
    """Hyphens, quotes, and literal AND/OR must not raise FTS5 syntax errors.

    The assertion that matters is that these calls complete at all --
    if _sanitize_fts_query failed to escape a token, sqlite3 would raise
    OperationalError and the test would fail via exception, not via a
    false assertion."""
    memory.store_memory(category="episodic", content="auth module design discussion")
    assert isinstance(memory.retrieve_memories("auth-module design"), list)
    assert isinstance(memory.retrieve_memories('contains "quotes"'), list)
    assert isinstance(memory.retrieve_memories("this AND that"), list)
    # None of the above should raise; a real keyword should still match.
    results = memory.retrieve_memories("auth module")
    assert len(results) >= 1


def test_retrieve_memories_respects_project_scoping():
    memory.store_memory(
        category="episodic", content="shared memory about deployment", project="jarvis"
    )
    memory.store_memory(
        category="episodic", content="shared memory about deployment", project="other"
    )
    jarvis_results = memory.retrieve_memories("deployment", project="jarvis")
    assert len(jarvis_results) >= 1
    assert all(r["project"] == "jarvis" for r in jarvis_results)
    other_results = memory.retrieve_memories("deployment", project="other")
    assert len(other_results) >= 1
    assert all(r["project"] == "other" for r in other_results)


def test_retrieve_memories_excludes_expired():
    memory.store_memory(
        category="episodic",
        content="temporary fact about bananas",
        expires_at="2000-01-01T00:00:00Z",
    )
    memory.store_memory(
        category="episodic",
        content="permanent fact about bananas",
    )
    results = memory.retrieve_memories("bananas")
    assert len(results) == 1
    assert results[0]["content"] == "permanent fact about bananas"


def test_retrieve_memories_empty_tokenization_returns_empty():
    assert memory.retrieve_memories("a to") == []
    assert memory.retrieve_memories("I") == []


# ── FTS / table synchronization ─────────────────────────────────────


def test_update_and_delete_keep_fts_synced():
    mem_id = memory.store_memory(category="episodic", content="alpha keyword is present")
    assert len(memory.retrieve_memories("alpha")) == 1
    assert len(memory.retrieve_memories("beta")) == 0

    memory.update_memory(mem_id, "beta keyword is present now")
    assert len(memory.retrieve_memories("alpha")) == 0
    assert len(memory.retrieve_memories("beta")) == 1

    memory.delete_memory(mem_id)
    assert len(memory.retrieve_memories("alpha")) == 0
    assert len(memory.retrieve_memories("beta")) == 0


# ── Core facts ────────────────────────────────────────────────────────


def test_get_core_facts_only_returns_core_facts():
    memory.store_memory(category="core_fact", content="Core fact one")
    memory.store_memory(category="episodic", content="Episodic memory")
    memory.store_memory(category="explicit", content="Explicit preference")
    results = memory.get_core_facts()
    assert len(results) == 1
    assert results[0]["category"] == "core_fact"
    assert results[0]["content"] == "Core fact one"


def test_get_core_facts_excludes_expired():
    memory.store_memory(category="core_fact", content="expired core fact", expires_at="2000-01-01T00:00:00Z")
    memory.store_memory(category="core_fact", content="valid core fact")
    results = memory.get_core_facts()
    assert len(results) == 1
    assert results[0]["content"] == "valid core fact"


# ── Recent memories ───────────────────────────────────────────────────


def test_get_recent_memories_ordering_and_limit():
    ids = []
    for i in range(5):
        mem_id = memory.store_memory(category="episodic", content=f"memory {i}")
        ids.append(mem_id)
    recent = memory.get_recent_memories(limit=3)
    assert len(recent) == 3
    # Most recent first
    assert recent[0]["content"] == "memory 4"
    assert recent[1]["content"] == "memory 3"
    assert recent[2]["content"] == "memory 2"


def test_get_recent_memories_project_filter():
    memory.store_memory(category="episodic", content="jarvis memory", project="jarvis")
    memory.store_memory(category="episodic", content="other memory", project="other")
    jarvis = memory.get_recent_memories(project="jarvis")
    assert len(jarvis) == 1
    assert jarvis[0]["project"] == "jarvis"


# ── Async wrappers ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_wrapper_store_and_retrieve():
    mem_id = await db_async.store_memory(
        category="explicit", content="async wrapped memory", project="jarvis"
    )
    assert mem_id.startswith("mem_")
    results = await db_async.retrieve_memories("async wrapped", project="jarvis")
    assert len(results) == 1
    assert results[0]["content"] == "async wrapped memory"

    await db_async.delete_memory(mem_id)
    results = await db_async.retrieve_memories("async wrapped")
    assert results == []


def test_update_memory_missing_id_is_noop():
    memory.update_memory("mem_doesnotexist", "new content")
    assert memory.get_memories_by_source("conversation", None) == []


def test_delete_memory_missing_id_is_noop():
    memory.delete_memory("mem_doesnotexist")
    assert memory.get_memories_by_source("conversation", None) == []
