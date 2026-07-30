"""Tests for the schema migration framework (app/migrations.py).

No dedicated test file existed for this before — the version>=1
short-circuit bug (fixed alongside these tests) had zero coverage,
which is exactly how it went unnoticed: every test used a fresh temp
DB (version 0), where the buggy early-return path was never exercised.
"""
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import migrations


@pytest.fixture
def conn():
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    c = sqlite3.connect(path)
    yield c
    c.close()
    os.unlink(path)


def test_fresh_db_applies_all_migrations(conn):
    applied = migrations.migrate(conn)
    assert applied == [1, 2, 3]
    assert migrations.current_version(conn) == 3
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    assert row is not None
    row_plans = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='plans'"
    ).fetchone()
    assert row_plans is not None
    row_memories = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone()
    assert row_memories is not None


def test_migrate_twice_applies_nothing_the_second_time(conn):
    migrations.migrate(conn)
    applied = migrations.migrate(conn)
    assert applied == []


def test_baseline_adoption_records_version_1_and_applies_later_migrations(conn):
    # Simulate a pre-migrations-module DB: the tasks table exists, but no
    # schema_version table -- migrate() must recognize this as "already at
    # version 1" without re-running _migration_001's CREATE TABLE (which
    # would raise "table already exists" if it tried).
    conn.execute("CREATE TABLE tasks (task_id TEXT)")
    conn.commit()
    applied = migrations.migrate(conn)
    assert applied == [1, 2, 3]
    assert migrations.current_version(conn) == 3


def test_a_db_already_at_version_2_still_receives_later_migrations(conn, monkeypatch):
    """Regression test for the exact bug found in Month 1 Week 3 (plan
    executor): migrate() used to `return []` unconditionally for any DB
    already at version >= 1, silently skipping every later migration.
    This is the case that matters most in production -- the real
    jarvis.db, and every baseline-adopted DB, is always at version 2+
    after its first migrate() call.

    The synthetic next-version migration is derived from
    max(existing versions) + 1 rather than hardcoded, so this test never
    collides with a real migration added later (this exact collision --
    `UNIQUE constraint failed: schema_version.version` -- happened once
    already when a hardcoded version=2 here collided with the real
    migration 2 added in the plan executor task)."""
    migrations.migrate(conn)
    starting_version = migrations.current_version(conn)
    assert starting_version >= 2

    applied_marker = []
    next_version = starting_version + 1

    def _synthetic_migration(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE synthetic_next_table (id INTEGER PRIMARY KEY)")
        applied_marker.append(True)

    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        migrations.MIGRATIONS + [(next_version, "synthetic_test_migration", _synthetic_migration)],
    )

    applied = migrations.migrate(conn)
    assert applied == [next_version]
    assert applied_marker == [True]
    assert migrations.current_version(conn) == next_version
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='synthetic_next_table'"
    ).fetchone()
    assert row is not None


def test_baseline_adopted_db_also_receives_later_migrations(conn, monkeypatch):
    """The other broken path: baseline adoption used to `return [1]`
    immediately, never falling through to check for version 2+.

    Synthetic version derived dynamically -- see the comment on
    test_a_db_already_at_version_2_still_receives_later_migrations for why."""
    conn.execute("CREATE TABLE tasks (task_id TEXT)")
    conn.commit()

    existing_versions = [v for v, _, _ in migrations.MIGRATIONS]
    next_version = max(existing_versions) + 1

    def _synthetic_migration(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE synthetic_next_table_2 (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        migrations.MIGRATIONS + [(next_version, "synthetic_test_migration", _synthetic_migration)],
    )

    applied = migrations.migrate(conn)
    assert applied == existing_versions + [next_version]
    assert migrations.current_version(conn) == next_version
