import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from app.migrations import migrate

DB_PATH = "jarvis.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # F1.13: enable FK enforcement. This is a per-connection pragma that
    # must be set immediately after connect, before any statement that
    # opens a transaction; it is a no-op if executed inside one.
    conn.execute("PRAGMA foreign_keys=ON")
    # ADR-024: bounded wait for lock contention. Per-connection pragma;
    # must be set on every connection created by get_conn().
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def save_event(event_type: str, content: str | None = None, trace_id: str | None = None) -> None:
    conn = get_conn()
    timestamp = utcnow()
    conn.execute(
        "INSERT INTO events (type, timestamp, content, trace_id) VALUES (?, ?, ?, ?)",
        (event_type, timestamp, content, trace_id),
    )
    conn.commit()
    conn.close()


def get_recent_events(limit: int = 100) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT type, timestamp, content FROM events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [
        {"type": r["type"], "timestamp": r["timestamp"], "content": r["content"]}
        for r in reversed(rows)
    ]


def get_events_by_trace_id(trace_id: str) -> list[dict]:
    """ADR-020: every event recorded during one traced unit of work, in
    emission order. The minimal read path proving trace_id actually
    survives end-to-end — the full Execution Trace surface (joining in
    conversations/tasks/opencode_tasks) is a later milestone's job."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, type, timestamp, content, trace_id FROM events WHERE trace_id=? ORDER BY id ASC",
        (trace_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def purge_events_older_than(days: int, now: str | None = None) -> int:
    """ADR-020 retention policy: delete `events` rows older than `days`.
    `events` is the fastest-growing trace-carrying table (one row per
    broadcastable turn/tool-call/lifecycle step) and the one this milestone
    puts a concrete ceiling on; the other eight tables TD-019 already names
    remain open. Returns the number of rows deleted. Not wired to a
    scheduler here — that is an operational/plumbing concern for a later
    milestone; this function is the enforceable policy itself."""
    from datetime import timedelta

    now_dt = datetime.fromisoformat((now or utcnow()).replace("Z", "+00:00"))
    cutoff = (now_dt - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    conn = get_conn()
    cur = conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()
    return cur.rowcount


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_task_record(task_id: str, name: str, command: str, trace_id: str | None = None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO tasks (task_id, name, command, started_at, trace_id) VALUES (?, ?, ?, ?, ?)",
        (task_id, name, command, utcnow(), trace_id),
    )
    conn.commit()
    conn.close()


def update_task_status(task_id: str, status: str, exit_code: int | None = None) -> None:
    conn = get_conn()
    completed_at = utcnow() if status in ("completed", "failed", "cancelled") else None
    conn.execute(
        "UPDATE tasks SET status=?, completed_at=?, exit_code=? WHERE task_id=?",
        (status, completed_at, exit_code, task_id),
    )
    conn.commit()
    conn.close()


def get_task(task_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_recent_tasks(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM tasks ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_running_tasks_interrupted() -> list[str]:
    """Milestone 8 reconnaissance finding (real pre-existing bug, fixed
    here): this used to unconditionally fail every running/waiting task and
    cancel every pending question on restart — including OpenCode-backed
    ones. That's correct for a local subprocess (its stdin pipe and process
    are genuinely gone after a Jarvis restart), but wrong for an OpenCode
    task: the session lives in OpenCode's own persistent, isolated storage
    (Milestone 6.1) independent of the Jarvis process, so a pending
    OpenCode question is still real and still answerable after Jarvis
    restarts. Scoped here to exclude any task_id present in opencode_tasks
    — those are handled instead by mark_running_opencode_tasks_interrupted()
    ('degraded', not a false failure) and, as of Milestone 8, by
    AttentionRequest's own restart-survival (deferred/pending state is
    preserved verbatim across restart).

    Milestone 8.1 real-phone finding (2026-07-10): for a *local* task, this
    function's own cancellation here is itself a genuine source
    invalidation (Phase 20: "source disappears -> terminal state, not
    indefinite contact") — a deferred AttentionRequest whose local question
    just got force-cancelled here must not be left pointing at a dead
    source, waiting to re-contact the user about a question that no longer
    exists. Returns the affected task_ids so the caller (app/main.py's
    lifespan) can cancel any associated AttentionRequest — done at the call
    site, not here, since app/database.py cannot import attention_manager
    (which itself imports this module) without a circular import."""
    conn = get_conn()
    now = utcnow()
    affected = {
        r["task_id"] for r in conn.execute(
            "SELECT task_id FROM tasks WHERE status IN ('running', 'waiting_for_user') "
            "AND task_id NOT IN (SELECT task_id FROM opencode_tasks)"
        ).fetchall()
    }
    affected |= {
        r["task_id"] for r in conn.execute(
            "SELECT task_id FROM questions WHERE status='pending' "
            "AND task_id NOT IN (SELECT task_id FROM opencode_tasks)"
        ).fetchall()
    }
    conn.execute(
        "UPDATE tasks SET status='failed', completed_at=?, exit_code=-1 "
        "WHERE status IN ('running', 'waiting_for_user') "
        "AND task_id NOT IN (SELECT task_id FROM opencode_tasks)",
        (now,),
    )
    conn.execute(
        "UPDATE questions SET status='cancelled', answered_at=? "
        "WHERE status='pending' "
        "AND task_id NOT IN (SELECT task_id FROM opencode_tasks)",
        (now,),
    )
    conn.commit()
    conn.close()
    return sorted(affected)


def create_question_record(question_id: str, task_id: str, question_text: str, context: str | None, options_json: str | None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO questions (question_id, task_id, question, context, options_json, asked_at) VALUES (?, ?, ?, ?, ?, ?)",
        (question_id, task_id, question_text, context, options_json, utcnow()),
    )
    conn.commit()
    conn.close()


def answer_question_record(question_id: str, answer: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE questions SET status='answered', answered_at=?, answer=? WHERE question_id=?",
        (utcnow(), answer, question_id),
    )
    conn.commit()
    conn.close()


def cancel_question_record(question_id: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE questions SET status='cancelled', answered_at=? WHERE question_id=? AND status='pending'",
        (utcnow(), question_id),
    )
    conn.commit()
    conn.close()


def get_question_record(question_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM questions WHERE question_id=?", (question_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_questions() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM questions WHERE status='pending' ORDER BY asked_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── OpenCode tasks ────────────────────────────────────────────────


def create_opencode_task_record(
    task_id: str, session_id: str, project_dir: str, instruction: str | None = None,
    trace_id: str | None = None,
) -> None:
    now = utcnow()
    conn = get_conn()
    conn.execute(
        "INSERT INTO opencode_tasks (task_id, session_id, project_dir, instruction, created_at, updated_at, trace_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, session_id, project_dir, instruction, now, now, trace_id),
    )
    conn.commit()
    conn.close()


def update_opencode_task_status(task_id: str, status: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE opencode_tasks SET status=?, updated_at=? WHERE task_id=?",
        (status, utcnow(), task_id),
    )
    conn.commit()
    conn.close()


def update_opencode_task_evidence(task_id: str, evidence_type: str) -> None:
    """Record the most recent verified native evidence observed for a task
    (e.g. 'message.part.delta', 'session.error', 'session.idle') without
    necessarily changing its status. Used to prove activity rather than
    inferring it from silence."""
    conn = get_conn()
    conn.execute(
        "UPDATE opencode_tasks SET last_evidence_type=?, last_evidence_at=? WHERE task_id=?",
        (evidence_type, utcnow(), task_id),
    )
    conn.commit()
    conn.close()


def update_opencode_task_result(task_id: str, result_summary: str) -> None:
    """Delegated Observation and Reporting milestone: persists the
    substantive result captured at task completion, so a later follow-up
    question can be answered from this row without re-running the task or
    re-querying OpenCode."""
    conn = get_conn()
    conn.execute(
        "UPDATE opencode_tasks SET result_summary=? WHERE task_id=?",
        (result_summary, task_id),
    )
    conn.commit()
    conn.close()


def get_opencode_task(task_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM opencode_tasks WHERE task_id=?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_opencode_task_by_session(session_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM opencode_tasks WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_opencode_running_tasks() -> list[dict]:
    """Active (non-terminal) OpenCode tasks: running, pending, or waiting on
    the user. 'waiting_for_user' is included deliberately — a task blocked
    on a native question/permission is still active, not finished, and must
    be visible to attention/status tooling (Milestone 6 Phase 8 fix; this
    filter previously excluded 'waiting_for_user', which made the OpenCode
    "N task(s) waiting" attention line permanently unreachable)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM opencode_tasks WHERE status IN ('running', 'pending', 'waiting_for_user') ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Conversation persistence ────────────────────────────────────────


def save_conversation_message(
    conversation_id: str, role: str, content: str, metadata_json: str | None = None,
    trace_id: str | None = None,
) -> None:
    now = utcnow()
    conn = get_conn()
    conn.execute(
        "INSERT INTO conversations (conversation_id, role, content, metadata_json, created_at, trace_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (conversation_id, role, content, metadata_json, now, trace_id),
    )
    conn.commit()
    conn.close()


def get_conversation_messages(conversation_id: str, limit: int = 100) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, conversation_id, role, content, metadata_json, created_at, trace_id "
        "FROM conversations WHERE conversation_id=? ORDER BY id ASC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "conversation_id": r["conversation_id"],
            "role": r["role"],
            "content": r["content"],
            "metadata_json": r["metadata_json"],
            "created_at": r["created_at"],
            "trace_id": r["trace_id"],
        }
        for r in rows
    ]


CONVERSATION_ID_PATTERN = re.compile(r"^conv_[0-9a-f]{12}$")


def new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex[:12]}"


def is_valid_conversation_id(conversation_id: str) -> bool:
    """Opaque-identifier format check. Never trust an arbitrary client-
    supplied string as a conversation_id without this — it's used directly
    in a SQL WHERE clause (parameterized, so not an injection vector, but a
    malformed/oversized/hostile value should still never be accepted as a
    conversation identity)."""
    return isinstance(conversation_id, str) and bool(CONVERSATION_ID_PATTERN.match(conversation_id))


def conversation_exists(conversation_id: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM conversations WHERE conversation_id=? LIMIT 1",
        (conversation_id,),
    ).fetchone()
    conn.close()
    return row is not None


# NOTE: _ensure_column is retained for backward compatibility.
# New schema changes must be migrations, not _ensure_column calls.
def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Add a column to an existing table if it's missing (safe migration for
    DBs created before this column existed). SQLite has no
    'ADD COLUMN IF NOT EXISTS', so check PRAGMA table_info first."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db() -> None:
    conn = get_conn()
    migrate(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            content TEXT,
            trace_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
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
        CREATE TABLE IF NOT EXISTS questions (
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
        CREATE TABLE IF NOT EXISTS opencode_tasks (
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
    _ensure_column(conn, "opencode_tasks", "last_evidence_type", "TEXT")
    _ensure_column(conn, "opencode_tasks", "last_evidence_at", "TEXT")
    # Delegated Observation and Reporting milestone: the substantive result
    # of a completed/failed task (the delegated agent's own final text
    # answer, or a fallback derived from tool output) -- additive next to
    # the existing lifecycle columns above, not a replacement for them.
    # Real-device finding this milestone traces back to: Jarvis could track
    # that a task finished but had no way to say what it actually found.
    _ensure_column(conn, "opencode_tasks", "result_summary", "TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
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
        CREATE TABLE IF NOT EXISTS notifications (
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
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            conversation_id TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attention_requests (
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
        CREATE TABLE IF NOT EXISTS contact_attempts (
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
        CREATE TABLE IF NOT EXISTS voice_sessions (
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_id ON events(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_task_id ON tasks(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_question_id ON questions(question_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_task_id ON questions(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oc_tasks_task_id ON opencode_tasks(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oc_tasks_session_id ON opencode_tasks(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_id ON conversations(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_notification_id ON notifications(notification_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_dedup_key ON notifications(dedup_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_task_id ON notifications(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_push_subscriptions_endpoint ON push_subscriptions(endpoint)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attention_dedup_key ON attention_requests(dedup_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attention_source ON attention_requests(source_type, source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attention_task_id ON attention_requests(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attention_conversation_id ON attention_requests(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attention_status ON attention_requests(status)")
    # Milestone 9B.4 / TD-002 / ADR-007: nullable set-if-null ownership
    # lease so two clients (e.g. the PWA and the Android companion) cannot
    # each independently open a VoiceSession bound to the same
    # AttentionRequest with no coordination between them.
    _ensure_column(conn, "attention_requests", "active_voice_session_id", "TEXT")
    # Milestone 9B.10: distinguishes why a VoiceSession closed (explicit
    # client request, WebSocket disconnect, or the idle-timeout reaper) —
    # only ever set on the transition into 'closed', never overwritten
    # afterward. Existing rows from before this column existed simply read
    # NULL, which formats as "n/a" everywhere this is displayed.
    _ensure_column(conn, "voice_sessions", "termination_reason", "TEXT")
    # Interaction Layer v1 (Goal 5): the exact tool name/args a voice
    # session is waiting on a yes/no confirmation for (JSON), set only
    # while state=confirming and cleared the moment it resolves either
    # way. Session-scoped rather than a separate table — at most one
    # pending confirmation can exist per session, by construction (the
    # tool loop stops proposing further actions the moment one is
    # generated; see supervisor.py's pending_confirmation handling).
    _ensure_column(conn, "voice_sessions", "pending_tool_call", "TEXT")
    # ADR-020 (trace_id, Owner Experience M-OX.1): additive migration for
    # DBs created before the trace_id column existed on these four tables.
    _ensure_column(conn, "events", "trace_id", "TEXT")
    _ensure_column(conn, "tasks", "trace_id", "TEXT")
    _ensure_column(conn, "opencode_tasks", "trace_id", "TEXT")
    _ensure_column(conn, "conversations", "trace_id", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contact_attempts_attention_id ON contact_attempts(attention_request_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_sessions_conversation_id ON voice_sessions(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_sessions_attention_id ON voice_sessions(attention_request_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_trace_id ON events(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_trace_id ON tasks(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oc_tasks_trace_id ON opencode_tasks(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_trace_id ON conversations(trace_id)")
    conn.commit()
    conn.close()


def mark_running_opencode_tasks_interrupted() -> None:
    """Mark non-terminal OpenCode tasks 'degraded' (not 'failed') on Jarvis
    restart. Unlike a local subprocess (which Jarvis owns directly and can
    prove is dead), an OpenCode session lives in OpenCode's own persistent
    database independent of the `opencode serve` process — Jarvis restarting
    does not prove the session failed. 'degraded' means "state unknown,
    reconciliation required" rather than a false failure claim. See
    OpenCodeSupervisor.reconcile_on_startup(), which attempts to upgrade
    'degraded' tasks to a verified status once the (fresh) server is up."""
    now = utcnow()
    conn = get_conn()
    conn.execute(
        "UPDATE opencode_tasks SET status='degraded', updated_at=? WHERE status IN ('running', 'pending', 'waiting_for_user')",
        (now,),
    )
    conn.commit()
    conn.close()


def get_opencode_degraded_tasks() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM opencode_tasks WHERE status='degraded' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Notifications (Milestone 7) ──────────────────────────────────────
#
# One underlying verified event (a question created, a permission created,
# a verified task failure, a verified task completion) must produce exactly
# one logical notification row, regardless of how many times the producing
# code path is (re-)entered — SSE replay, reconnect, restart, retry. This is
# enforced at the DB layer via a UNIQUE constraint on dedup_key rather than
# relying on callers to remember not to re-create: create_notification() is
# an idempotent upsert-by-dedup_key, safe to call more than once for the
# same logical event.


def create_notification(
    notification_id: str,
    conversation_id: str | None,
    task_id: str | None,
    source_type: str,
    source_id: str | None,
    notification_type: str,
    title: str,
    body: str,
    priority: str,
    dedup_key: str,
) -> dict:
    """Idempotent by dedup_key. Returns the resulting row plus a 'created'
    flag (False if a notification with this dedup_key already existed —
    the pre-existing row is returned unchanged, never a duplicate)."""
    conn = get_conn()
    now = utcnow()
    cur = conn.execute(
        "INSERT OR IGNORE INTO notifications "
        "(notification_id, conversation_id, task_id, source_type, source_id, "
        " notification_type, title, body, priority, dedup_key, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (notification_id, conversation_id, task_id, source_type, source_id,
         notification_type, title, body, priority, dedup_key, now),
    )
    created = cur.rowcount == 1
    conn.commit()
    row = conn.execute(
        "SELECT * FROM notifications WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    conn.close()
    result = dict(row)
    result["created"] = created
    return result


def get_notification(notification_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM notifications WHERE notification_id=?", (notification_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_notification_by_dedup_key(dedup_key: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM notifications WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_notification_delivered(notification_id: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE notifications SET delivered_at=? WHERE notification_id=? AND delivered_at IS NULL",
        (utcnow(), notification_id),
    )
    conn.commit()
    conn.close()


def mark_notification_read(notification_id: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE notifications SET status='read', read_at=? WHERE notification_id=? AND read_at IS NULL",
        (utcnow(), notification_id),
    )
    conn.commit()
    conn.close()


def get_recent_notifications(limit: int = 50) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def get_pending_notifications() -> list[dict]:
    """Notifications not yet acknowledged (status='pending', i.e. read_at is
    still unset) — resent on every reconnect the same way get_pending_questions()
    is, so a phone that reconnects after missing a notification still learns
    about it. Client-side rendering is keyed by notification_id, so resending
    an already-seen-but-unread notification on reconnect is a safe no-op, not
    a duplicate (Phase 14)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM notifications WHERE status='pending' ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_undelivered_notifications() -> list[dict]:
    """Notifications never yet sent over any channel — used to catch up a
    client that connects after a notification was created while no client
    was attached (e.g. server restart)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM notifications WHERE delivered_at IS NULL ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Push subscriptions (Milestone 7) ─────────────────────────────────


def save_push_subscription(endpoint: str, p256dh: str, auth: str, conversation_id: str | None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO push_subscriptions (endpoint, p256dh, auth, conversation_id, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, "
        "conversation_id=excluded.conversation_id",
        (endpoint, p256dh, auth, conversation_id, utcnow()),
    )
    conn.commit()
    conn.close()


def delete_push_subscription(endpoint: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    conn.commit()
    conn.close()


def get_push_subscriptions() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Settings (Milestone 7) ───────────────────────────────────────────
# Single-row-per-key store for small user-controllable prototype
# preferences (e.g. notify_on_completion). Not for secrets — those live
# only in environment variables, never in this table.


def get_setting(key: str, default: str | None = None) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ── AttentionRequest (Milestone 8) ────────────────────────────────────
#
# The persistent representation that human attention is required — distinct
# from a Notification (a delivery artifact) and from the underlying
# WorkerQuestion/permission/task (the source). See app/attention_manager.py
# for the state machine and orchestration; this module is pure data access,
# same convention as the rest of database.py. Idempotent creation by
# dedup_key (INSERT OR IGNORE), same pattern as create_notification().


def create_attention_request(
    attention_request_id: str,
    conversation_id: str | None,
    task_id: str | None,
    source_type: str,
    source_id: str,
    attention_type: str,
    urgency: str,
    summary: str,
    context_json: str | None,
    contact_policy: str | None,
    dedup_key: str,
) -> dict:
    """Idempotent by dedup_key. Returns the resulting row plus a 'created'
    flag (False if an AttentionRequest for this exact source already
    existed — the pre-existing row is returned unchanged)."""
    conn = get_conn()
    now = utcnow()
    cur = conn.execute(
        "INSERT OR IGNORE INTO attention_requests "
        "(attention_request_id, conversation_id, task_id, source_type, source_id, "
        " attention_type, status, urgency, summary, context_json, created_at, updated_at, "
        " contact_policy, contact_attempt_count, dedup_key) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, ?)",
        (attention_request_id, conversation_id, task_id, source_type, source_id,
         attention_type, urgency, summary, context_json, now, now, contact_policy, dedup_key),
    )
    created = cur.rowcount == 1
    conn.commit()
    row = conn.execute(
        "SELECT * FROM attention_requests WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    conn.close()
    result = dict(row)
    result["created"] = created
    return result


def get_attention_request(attention_request_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM attention_requests WHERE attention_request_id=?", (attention_request_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_attention_request_by_dedup_key(dedup_key: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM attention_requests WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_attention_request_by_source(source_type: str, source_id: str) -> dict | None:
    """Deterministic source correlation (Milestone 8 Phase 3) — the exact
    mapping a later action (defer, answer, cancel) must route through,
    never inferred from UI order, truncated IDs, or LLM memory."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM attention_requests WHERE source_type=? AND source_id=? "
        "ORDER BY id DESC LIMIT 1",
        (source_type, source_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def transition_attention_status(
    attention_request_id: str,
    from_statuses: tuple[str, ...],
    to_status: str,
    **extra_fields: object,
) -> bool:
    """Guarded conditional UPDATE: only transitions if the row's *current*
    status is one of `from_statuses` — this is the concurrency protection
    (Phase 21) against two racing transitions (e.g. answer + defer at the
    same time, or a scheduler tick firing while the user opens the item):
    whichever UPDATE's WHERE clause matches first wins (SQLite's WAL-mode
    single-writer semantics make this atomic); the loser's rowcount is 0
    and it must not report success. Legal-transition *validation* (which
    from_statuses are meaningful for a given to_status) lives in
    app/attention_manager.py, not here — this function only enforces
    whatever the caller asked for, atomically."""
    conn = get_conn()
    now = utcnow()
    set_clauses = ["status=?", "updated_at=?"]
    params: list = [to_status, now]
    for field, value in extra_fields.items():
        set_clauses.append(f"{field}=?")
        params.append(value)
    placeholders = ",".join("?" for _ in from_statuses)
    params.append(attention_request_id)
    params.extend(from_statuses)
    cur = conn.execute(
        f"UPDATE attention_requests SET {', '.join(set_clauses)} "
        f"WHERE attention_request_id=? AND status IN ({placeholders})",
        params,
    )
    conn.commit()
    conn.close()
    return cur.rowcount == 1


def record_attention_contact(attention_request_id: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE attention_requests SET contact_attempt_count=contact_attempt_count+1, "
        "last_contact_at=?, updated_at=? WHERE attention_request_id=?",
        (utcnow(), utcnow(), attention_request_id),
    )
    conn.commit()
    conn.close()


def set_attention_next_contact(attention_request_id: str, next_contact_at: str | None) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE attention_requests SET next_contact_at=?, updated_at=? WHERE attention_request_id=?",
        (next_contact_at, utcnow(), attention_request_id),
    )
    conn.commit()
    conn.close()


def get_due_attention_requests(now: str | None = None) -> list[dict]:
    """Deferred requests whose deferred_until has passed, plus pending/
    contacting requests whose retry-backoff next_contact_at has passed.
    `now` is an injectable ISO-timestamp string (Phase 8: deterministic
    clock for tests — never real-time sleep in a unit test) defaulting to
    the real current time."""
    now = now or utcnow()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM attention_requests WHERE "
        "(status='deferred' AND deferred_until IS NOT NULL AND deferred_until <= ?) "
        "OR (status IN ('pending', 'contacting') AND next_contact_at IS NOT NULL AND next_contact_at <= ?) "
        "ORDER BY id ASC",
        (now, now),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_unresolved_attention_requests() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM attention_requests WHERE status IN "
        "('pending', 'contacting', 'deferred', 'resolving') ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_attention_requests_for_task(task_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM attention_requests WHERE task_id=? ORDER BY created_at ASC", (task_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_attention_requests_for_conversation(conversation_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM attention_requests WHERE conversation_id=? ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── ContactAttempt (Milestone 8) ──────────────────────────────────────


def create_contact_attempt(
    contact_attempt_id: str,
    attention_request_id: str,
    channel: str,
    notification_id: str | None = None,
    voice_session_id: str | None = None,
) -> dict:
    conn = get_conn()
    now = utcnow()
    conn.execute(
        "INSERT INTO contact_attempts "
        "(contact_attempt_id, attention_request_id, channel, status, created_at, "
        " notification_id, voice_session_id) "
        "VALUES (?, ?, ?, 'planned', ?, ?, ?)",
        (contact_attempt_id, attention_request_id, channel, now, notification_id, voice_session_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM contact_attempts WHERE contact_attempt_id=?", (contact_attempt_id,)
    ).fetchone()
    conn.close()
    return dict(row)


def update_contact_attempt_status(
    contact_attempt_id: str,
    status: str,
    result: str | None = None,
    error_code: str | None = None,
) -> None:
    conn = get_conn()
    now = utcnow()
    started_at = now if status == "attempted" else None
    completed_at = now if status in ("delivered", "opened", "failed") else None
    conn.execute(
        "UPDATE contact_attempts SET status=?, result=COALESCE(?, result), error_code=COALESCE(?, error_code), "
        "started_at=COALESCE(started_at, ?), completed_at=COALESCE(?, completed_at) "
        "WHERE contact_attempt_id=?",
        (status, result, error_code, started_at, completed_at, contact_attempt_id),
    )
    conn.commit()
    conn.close()


def get_contact_attempts_for_attention(attention_request_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM contact_attempts WHERE attention_request_id=? ORDER BY id ASC",
        (attention_request_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_most_recent_contact_attempt(attention_request_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM contact_attempts WHERE attention_request_id=? ORDER BY id DESC LIMIT 1",
        (attention_request_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── VoiceSession (Milestone 8) ─────────────────────────────────────────


def open_voice_session_atomic(
    voice_session_id: str,
    conversation_id: str,
    attention_request_id: str | None,
    opening_state: str,
    listening_state: str,
) -> bool:
    """F1.14: the lease claim + session insert + both state transitions as ONE
    transaction. Previously these were four separate connections and four
    commits; a failure after the lease claim left the AttentionRequest with
    active_voice_session_id pointing at a session that was never created —
    and because the lease is a set-if-NULL claim, nothing could ever claim it
    again (Principal Engineer Review 3.7).

    Returns False if the lease could not be claimed (someone else holds it),
    in which case nothing is written at all. Raises on any other failure,
    with the whole sequence rolled back.

    Simplification (brief-approved): the freshly-created session's fixed
    opening→listening path cannot be rejected, so the row is inserted
    directly in `listening_state` rather than idle→opening→listening via
    three separate writes. The resulting row is identical to the old code's
    end state (state=listening, created_at===updated_at since no
    intermediate timestamps are written). The `opening_state` parameter is
    accepted for signature compatibility but unused in this simpler route.
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        now = utcnow()

        if attention_request_id is not None:
            cur = conn.execute(
                "UPDATE attention_requests SET active_voice_session_id=?, updated_at=? "
                "WHERE attention_request_id=? AND active_voice_session_id IS NULL",
                (voice_session_id, now, attention_request_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False

        conn.execute(
            "INSERT INTO voice_sessions "
            "(voice_session_id, conversation_id, attention_request_id, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (voice_session_id, conversation_id, attention_request_id, listening_state, now, now),
        )

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_voice_session(
    voice_session_id: str, conversation_id: str, attention_request_id: str | None = None
) -> dict:
    conn = get_conn()
    now = utcnow()
    conn.execute(
        "INSERT INTO voice_sessions "
        "(voice_session_id, conversation_id, attention_request_id, state, created_at, updated_at) "
        "VALUES (?, ?, ?, 'idle', ?, ?)",
        (voice_session_id, conversation_id, attention_request_id, now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM voice_sessions WHERE voice_session_id=?", (voice_session_id,)
    ).fetchone()
    conn.close()
    return dict(row)


def get_voice_session(voice_session_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM voice_sessions WHERE voice_session_id=?", (voice_session_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_voice_session_state(voice_session_id: str, state: str, termination_reason: str | None = None) -> None:
    conn = get_conn()
    now = utcnow()
    closed_at = now if state == "closed" else None
    conn.execute(
        "UPDATE voice_sessions SET state=?, updated_at=?, closed_at=COALESCE(?, closed_at), "
        "termination_reason=COALESCE(?, termination_reason) "
        "WHERE voice_session_id=?",
        (state, now, closed_at, termination_reason, voice_session_id),
    )
    conn.commit()
    conn.close()


def set_pending_tool_call(voice_session_id: str, name: str, args: dict) -> None:
    """Interaction Layer v1 (Goal 5): persists the exact tool call a voice
    session is awaiting a yes/no confirmation for."""
    conn = get_conn()
    conn.execute(
        "UPDATE voice_sessions SET pending_tool_call=? WHERE voice_session_id=?",
        (json.dumps({"name": name, "args": args}), voice_session_id),
    )
    conn.commit()
    conn.close()


def clear_pending_tool_call(voice_session_id: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE voice_sessions SET pending_tool_call=NULL WHERE voice_session_id=?",
        (voice_session_id,),
    )
    conn.commit()
    conn.close()


def get_idle_voice_sessions(max_idle_seconds: int, now: str | None = None) -> list[dict]:
    """Milestone 9B.10: sessions that never reached a terminal state and
    whose updated_at hasn't moved in over max_idle_seconds — every legal
    transition (including each conversational turn in handle_transcript())
    refreshes updated_at, so an active back-and-forth conversation never
    appears here regardless of total session age; only genuine silence
    does. now is injectable for deterministic tests (Milestone 9B.10,
    matching attention_scheduler.py's clock-injection pattern). Plain
    ISO-8601 UTC string comparison, same style as
    get_due_attention_requests() — these timestamps are always UTC and
    zero-padded, so lexicographic order matches chronological order
    without needing SQL date arithmetic."""
    from datetime import timedelta

    now_dt = datetime.fromisoformat((now or utcnow()).replace("Z", "+00:00"))
    cutoff = (now_dt - timedelta(seconds=max_idle_seconds)).isoformat().replace("+00:00", "Z")
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM voice_sessions WHERE state NOT IN ('closed', 'failed') AND updated_at <= ?",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def try_claim_voice_session_lease(attention_request_id: str, voice_session_id: str) -> bool:
    """Atomic set-if-null ownership claim (Milestone 9B.4, TD-002/ADR-007):
    only succeeds if no other VoiceSession currently holds the lease for
    this AttentionRequest. Same conditional-UPDATE-with-WHERE-guard pattern
    as transition_attention_status() — whichever UPDATE's WHERE clause
    matches first wins under SQLite's single-writer semantics, so this is
    the real concurrency guard, not just a check-then-set race."""
    conn = get_conn()
    now = utcnow()
    cur = conn.execute(
        "UPDATE attention_requests SET active_voice_session_id=?, updated_at=? "
        "WHERE attention_request_id=? AND active_voice_session_id IS NULL",
        (voice_session_id, now, attention_request_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount == 1


def release_voice_session_lease(attention_request_id: str, voice_session_id: str) -> None:
    """Only releases if this exact session still holds the lease — a lease
    held by a *different* (later) voice_session_id must never be cleared by
    a stale release call."""
    conn = get_conn()
    now = utcnow()
    conn.execute(
        "UPDATE attention_requests SET active_voice_session_id=NULL, updated_at=? "
        "WHERE attention_request_id=? AND active_voice_session_id=?",
        (now, attention_request_id, voice_session_id),
    )
    conn.commit()
    conn.close()
