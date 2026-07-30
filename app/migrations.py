"""Schema versioning and migration framework.

Migrations are applied in ascending version order, each in its own
transaction.  migrate() is idempotent: calling it twice applies nothing
the second time.

Baseline adoption — when an existing jarvis.db already has the full
schema but no schema_version table (because it was created before this
module existed), migrate() records version 1 as already-applied without
re-executing its DDL.  The heuristic is: if schema_version does not exist
but the tasks table does, the database is at version 1.
"""

import sqlite3
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        return row[0] if row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _migration_001(conn: sqlite3.Connection) -> None:
    """Codify the current production schema: 11 tables + 23 indexes."""

    conn.execute("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            content TEXT,
            trace_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            command TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            exit_code INTEGER,
            trace_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id TEXT UNIQUE NOT NULL,
            task_id TEXT NOT NULL,
            question TEXT NOT NULL,
            context TEXT,
            options_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            asked_at TEXT NOT NULL,
            answered_at TEXT,
            answer TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE opencode_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE NOT NULL REFERENCES tasks(task_id),
            session_id TEXT NOT NULL,
            project_dir TEXT NOT NULL,
            instruction TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_evidence_type TEXT,
            last_evidence_at TEXT,
            trace_id TEXT,
            result_summary TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            trace_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_id TEXT UNIQUE NOT NULL,
            conversation_id TEXT,
            task_id TEXT,
            source_type TEXT NOT NULL,
            source_id TEXT,
            notification_type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'NORMAL',
            dedup_key TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            delivered_at TEXT,
            read_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            conversation_id TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE attention_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attention_request_id TEXT UNIQUE NOT NULL,
            conversation_id TEXT,
            task_id TEXT,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            attention_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            urgency TEXT NOT NULL DEFAULT 'NORMAL',
            summary TEXT NOT NULL,
            context_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deferred_until TEXT,
            resolved_at TEXT,
            resolution_type TEXT,
            resolution_value TEXT,
            contact_policy TEXT,
            last_contact_at TEXT,
            next_contact_at TEXT,
            contact_attempt_count INTEGER NOT NULL DEFAULT 0,
            dedup_key TEXT UNIQUE NOT NULL,
            active_voice_session_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE contact_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_attempt_id TEXT UNIQUE NOT NULL,
            attention_request_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            result TEXT,
            error_code TEXT,
            notification_id TEXT,
            voice_session_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE voice_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voice_session_id TEXT UNIQUE NOT NULL,
            conversation_id TEXT NOT NULL,
            attention_request_id TEXT,
            state TEXT NOT NULL DEFAULT 'idle',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT,
            termination_reason TEXT,
            pending_tool_call TEXT
        )
    """)

    conn.execute("CREATE INDEX idx_attention_conversation_id ON attention_requests(conversation_id)")
    conn.execute("CREATE INDEX idx_attention_dedup_key ON attention_requests(dedup_key)")
    conn.execute("CREATE INDEX idx_attention_source ON attention_requests(source_type, source_id)")
    conn.execute("CREATE INDEX idx_attention_status ON attention_requests(status)")
    conn.execute("CREATE INDEX idx_attention_task_id ON attention_requests(task_id)")
    conn.execute("CREATE INDEX idx_contact_attempts_attention_id ON contact_attempts(attention_request_id)")
    conn.execute("CREATE INDEX idx_conversations_id ON conversations(conversation_id)")
    conn.execute("CREATE INDEX idx_conversations_trace_id ON conversations(trace_id)")
    conn.execute("CREATE INDEX idx_events_id ON events(id)")
    conn.execute("CREATE INDEX idx_events_trace_id ON events(trace_id)")
    conn.execute("CREATE INDEX idx_notifications_dedup_key ON notifications(dedup_key)")
    conn.execute("CREATE INDEX idx_notifications_notification_id ON notifications(notification_id)")
    conn.execute("CREATE INDEX idx_notifications_task_id ON notifications(task_id)")
    conn.execute("CREATE INDEX idx_oc_tasks_session_id ON opencode_tasks(session_id)")
    conn.execute("CREATE INDEX idx_oc_tasks_task_id ON opencode_tasks(task_id)")
    conn.execute("CREATE INDEX idx_oc_tasks_trace_id ON opencode_tasks(trace_id)")
    conn.execute("CREATE INDEX idx_push_subscriptions_endpoint ON push_subscriptions(endpoint)")
    conn.execute("CREATE INDEX idx_questions_question_id ON questions(question_id)")
    conn.execute("CREATE INDEX idx_questions_task_id ON questions(task_id)")
    conn.execute("CREATE INDEX idx_tasks_task_id ON tasks(task_id)")
    conn.execute("CREATE INDEX idx_tasks_trace_id ON tasks(trace_id)")
    conn.execute("CREATE INDEX idx_voice_sessions_attention_id ON voice_sessions(attention_request_id)")
    conn.execute("CREATE INDEX idx_voice_sessions_conversation_id ON voice_sessions(conversation_id)")


def _migration_002(conn: sqlite3.Connection) -> None:
    """Create plans and plan_steps tables for the plan executor."""

    conn.execute("""
        CREATE TABLE plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            current_step_index INTEGER NOT NULL DEFAULT 0,
            context_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE plan_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            step_id TEXT UNIQUE NOT NULL,
            plan_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            description TEXT NOT NULL,
            worker_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            worker_task_id TEXT,
            result TEXT,
            error TEXT,
            started_at TEXT,
            completed_at TEXT,
            depends_on_json TEXT,
            on_failure TEXT NOT NULL DEFAULT 'stop',
            verification TEXT,
            verification_task_id TEXT,
            verification_result TEXT,
            verification_error TEXT
        )
    """)

    conn.execute("CREATE INDEX idx_plan_steps_plan_id ON plan_steps(plan_id)")
    conn.execute("CREATE INDEX idx_plans_status ON plans(status)")


def _migration_003(conn: sqlite3.Connection) -> None:
    """Create memories table and FTS5 index for the v1 memory system."""

    conn.execute("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            project TEXT,
            content TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            metadata TEXT
        )
    """)
    conn.execute("CREATE INDEX idx_memories_category ON memories(category)")
    conn.execute("CREATE INDEX idx_memories_project ON memories(project)")
    conn.execute("CREATE INDEX idx_memories_source ON memories(source, source_id)")

    conn.execute("""
        CREATE VIRTUAL TABLE memories_fts USING fts5(id UNINDEXED, content)
    """)


MIGRATIONS = [
    (1, "current_schema", _migration_001),
    (2, "plans_and_steps", _migration_002),
    (3, "memories", _migration_003),
]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply any pending migrations and return the list of versions applied.

    Idempotent — calling twice applies nothing the second time.

    Baseline adoption: if schema_version does not exist but the tasks
    table does, record version 1 as already-applied without re-running its
    DDL.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)

    ver = current_version(conn)
    applied: list[int] = []

    if ver == 0:
        # Check for baseline adoption
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if row is not None:
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (1, ?)",
                (_utcnow(),),
            )
            applied.append(1)
            ver = 1

    # Deliberately no "if ver >= N: return" short-circuit here — the loop's
    # own "if version > ver" check is what makes this idempotent, for both
    # a freshly-versioned DB and a baseline-adopted one. A short-circuit on
    # ver was previously here and silently skipped every migration after
    # the first for any DB already at version 1 (which includes every
    # baseline-adopted DB and the real jarvis.db) — found while adding the
    # plans/plan_steps migration, which would otherwise never have applied.
    for version, name, fn in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version > ver:
            fn(conn)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _utcnow()),
            )
            applied.append(version)

    conn.commit()
    return applied
