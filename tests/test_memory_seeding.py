"""Tests for core-fact seeding (memory.seed_core_facts)."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
import app.memory as memory


def _sample_projects():
    return {
        "jarvis": {"display_name": "Jarvis", "path": "/tmp/jarvis"},
        "website": {"display_name": "Personal Website", "path": "/tmp/website"},
    }


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
def isolate_projects(monkeypatch):
    """Prevent the real projects.json from affecting seeding tests."""
    monkeypatch.setattr(memory, "get_projects", lambda: {})


def _seeded_contents():
    return {row["content"] for row in memory.get_core_facts()}


def test_seed_core_facts_inserts_fixed_preferences_on_empty_db():
    assert memory.seed_core_facts() == 4
    facts = memory.get_core_facts()
    assert len(facts) == 4
    assert all(f["category"] == "core_fact" for f in facts)
    assert all(f["source"] == "startup_seed" for f in facts)
    contents = _seeded_contents()
    assert (
        "Local-first: Jarvis avoids cloud dependencies for core operation; the laptop is the brain and the phone is the interface."
        in contents
    )
    assert (
        "Evidence over inference: work is not marked complete based on silence or assumption — verifiable evidence is required."
        in contents
    )
    assert (
        "The supervisor routes tasks to workers; it never directly opens files or runs shell commands itself."
        in contents
    )
    assert (
        "No over-engineering: prefer additive, minimal changes over new infrastructure (no Redis/Celery/Docker/frontend frameworks)."
        in contents
    )


def test_seed_core_facts_is_idempotent():
    assert memory.seed_core_facts() == 4
    assert memory.seed_core_facts() == 0
    assert len(memory.get_core_facts()) == 4


def test_seed_core_facts_includes_user_name_when_configured(monkeypatch):
    monkeypatch.setenv("JARVIS_USER_NAME", "Alice")
    count = memory.seed_core_facts()
    assert count == 5
    contents = _seeded_contents()
    assert "The user's name is Alice." in contents
    assert memory.seed_core_facts() == 0


def test_seed_core_facts_includes_combined_projects_fact(monkeypatch):
    monkeypatch.setattr(memory, "get_projects", _sample_projects)
    count = memory.seed_core_facts()
    assert count == 5
    contents = _seeded_contents()
    assert "Known projects: Jarvis, Personal Website." in contents
    assert sum(1 for c in contents if c.startswith("Known projects:")) == 1
    assert memory.seed_core_facts() == 0


def test_seed_core_facts_retrievable_via_get_core_facts():
    memory.seed_core_facts()
    facts = memory.get_core_facts()
    assert len(facts) == 4
    assert all(f["category"] == "core_fact" for f in facts)
    assert all(f["source"] == "startup_seed" for f in facts)


def test_seed_core_facts_combined_content_under_token_budget(monkeypatch):
    monkeypatch.setenv("JARVIS_USER_NAME", "Alexander")
    monkeypatch.setattr(memory, "get_projects", _sample_projects)
    memory.seed_core_facts()
    facts = memory.get_core_facts()
    total_chars = sum(len(f["content"]) for f in facts)
    assert total_chars < 2000
