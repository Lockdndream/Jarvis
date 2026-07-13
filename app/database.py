import re
import sqlite3
import uuid
from datetime import datetime, timezone

DB_PATH = "jarvis.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def save_event(event_type: str, content: str | None = None):
    conn = get_conn()
    timestamp = utcnow()
    conn.execute(
        "INSERT INTO events (type, timestamp, content) VALUES (?, ?, ?)",
        (event_type, timestamp, content),
    )
    conn.commit()
    conn.close()


def get_recent_events(limit: int = 100):
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


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_task_record(task_id: str, name: str, command: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO tasks (task_id, name, command, started_at) VALUES (?, ?, ?, ?)",
        (task_id, name, command, utcnow()),
    )
    conn.commit()
    conn.close()


def update_task_status(task_id: str, status: str, exit_code: int | None = None):
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


def create_question_record(question_id, task_id, question_text, context, options_json):
    conn = get_conn()
    conn.execute(
        "INSERT INTO questions (question_id, task_id, question, context, options_json, asked_at) VALUES (?, ?, ?, ?, ?, ?)",
        (question_id, task_id, question_text, context, options_json, utcnow()),
    )
    conn.commit()
    conn.close()


def answer_question_record(question_id, answer):
    conn = get_conn()
    conn.execute(
        "UPDATE questions SET status='answered', answered_at=?, answer=? WHERE question_id=?",
        (utcnow(), answer, question_id),
    )
    conn.commit()
    conn.close()


def cancel_question_record(question_id):
    conn = get_conn()
    conn.execute(
        "UPDATE questions SET status='cancelled', answered_at=? WHERE question_id=? AND status='pending'",
        (utcnow(), question_id),
    )
    conn.commit()
    conn.close()


def get_question_record(question_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM questions WHERE question_id=?", (question_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_questions():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM questions WHERE status='pending' ORDER BY asked_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── OpenCode tasks ────────────────────────────────────────────────


def create_opencode_task_record(task_id: str, session_id: str, project_dir: str, instruction: str | None = None):
    now = utcnow()
    conn = get_conn()
    conn.execute(
        "INSERT INTO opencode_tasks (task_id, session_id, project_dir, instruction, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, session_id, project_dir, instruction, now, now),
    )
    conn.commit()
    conn.close()


def update_opencode_task_status(task_id: str, status: str):
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


def save_conversation_message(conversation_id: str, role: str, content: str, metadata_json: str | None = None):
    now = utcnow()
    conn = get_conn()
    conn.execute(
        "INSERT INTO conversations (conversation_id, role, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (conversation_id, role, content, metadata_json, now),
    )
    conn.commit()
    conn.close()


def get_conversation_messages(conversation_id: str, limit: int = 100) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, conversation_id, role, content, metadata_json, created_at FROM conversations WHERE conversation_id=? ORDER BY id ASC LIMIT ?",
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
        }
        for r in rows
    ]


CONVERSATION_ID_PATTERN = re.compile(r"^conv_[0-9a-f]{12}$")


def new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex[:12]}"


def is_valid_conversation_id(conversation_id) -> bool:
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


def _ensure_column(conn, table: str, column: str, coltype: str) -> None:
    """Add a column to an existing table if it's missing (safe migration for
    DBs created before this column existed). SQLite has no
    'ADD COLUMN IF NOT EXISTS', so check PRAGMA table_info first."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            content TEXT
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
            exit_code INTEGER
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
            last_evidence_at TEXT
        )
    """)
    _ensure_column(conn, "opencode_tasks", "last_evidence_type", "TEXT")
    _ensure_column(conn, "opencode_tasks", "last_evidence_at", "TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL
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
            dedup_key TEXT UNIQUE NOT NULL
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
            closed_at TEXT
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contact_attempts_attention_id ON contact_attempts(attention_request_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_sessions_conversation_id ON voice_sessions(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_sessions_attention_id ON voice_sessions(attention_request_id)")
    conn.commit()
    conn.close()


def mark_running_opencode_tasks_interrupted():
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
    **extra_fields,
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


def update_voice_session_state(voice_session_id: str, state: str) -> None:
    conn = get_conn()
    now = utcnow()
    closed_at = now if state == "closed" else None
    conn.execute(
        "UPDATE voice_sessions SET state=?, updated_at=?, closed_at=COALESCE(?, closed_at) "
        "WHERE voice_session_id=?",
        (state, now, closed_at, voice_session_id),
    )
    conn.commit()
    conn.close()
