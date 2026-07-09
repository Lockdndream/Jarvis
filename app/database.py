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


def mark_running_tasks_interrupted():
    conn = get_conn()
    conn.execute(
        "UPDATE tasks SET status='failed', completed_at=?, exit_code=-1 WHERE status IN ('running', 'waiting_for_user')",
        (utcnow(),),
    )
    conn.execute(
        "UPDATE questions SET status='cancelled', answered_at=? WHERE status='pending'",
        (utcnow(),),
    )
    conn.commit()
    conn.close()


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
