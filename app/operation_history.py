"""ADR-023 Phase 6: the Operation model -- a first-class identifier and
durable record for every operator-triggered action, owned by the
Operations console, never Jarvis's own DB. Reason: a "Stop Jarvis"
operation's own outcome must be recorded even though the only process
that could otherwise write it (Jarvis) is, by definition, going away
mid-operation. This table is therefore the single source of operation
history regardless of target (jarvis, opencode, connectivity_policy) or
whether Jarvis was reachable throughout.

operation_id is its own namespace -- never trace_id (ADR-020's canonical
definition: a trace is one logical unit of autonomous work initiated or
coordinated by the Supervisor; an operator click is neither).

Status vocabulary: QUEUED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED.
QUEUED collapses to effectively instantaneous under this milestone's
mutual-exclusion design -- a second concurrent request for the same
target is rejected with 409 before any Operation row is created at all,
rather than queued behind the first. QUEUED is retained in the schema
for forward compatibility, not because anything observably stays in it
today. CANCELLED is reserved and unreachable this milestone: every
claimed action runs to completion; interrupting a kill/spawn mid-flight
is unsafe and was not requested. Both are documented here deliberately,
not silently omitted, so an unreachable enum value reads as a scoped
decision rather than unfinished work.
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone


def _default_db_path() -> str:
    """Deliberately resolved fresh on every call, not baked in as a
    function default parameter (evaluated once, at import time) -- a
    real bug found via full-suite testing: whichever test file happened
    to import this module first froze DEFAULT_DB_PATH to whatever
    JARVIS_OPERATIONS_DB was (or wasn't) set to at that moment, silently
    ignoring the env var for every later caller in the same process."""
    return os.environ.get(
        "JARVIS_OPERATIONS_DB",
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisOperationsConsole", "operations.db"),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect(db_path: str) -> sqlite3.Connection:
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> None:
    conn = _connect(db_path or _default_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            action TEXT NOT NULL,
            operator TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            duration_ms INTEGER,
            error TEXT,
            detail TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_created_at ON operations(created_at)")
    conn.commit()
    conn.close()


def create_operation(target: str, action: str, operator: str = "local",
                      detail: dict | None = None, db_path: str | None = None) -> str:
    """Single-operator system: 'operator' is an honest constant, not
    invented RBAC (ADR-022's Authorization section)."""
    operation_id = str(uuid.uuid4())
    conn = _connect(db_path or _default_db_path())
    conn.execute(
        "INSERT INTO operations (operation_id, target, action, operator, status, created_at, detail) "
        "VALUES (?, ?, ?, ?, 'QUEUED', ?, ?)",
        (operation_id, target, action, operator, _now_iso(), json.dumps(detail) if detail else None),
    )
    conn.commit()
    conn.close()
    return operation_id


def mark_running(operation_id: str, db_path: str | None = None) -> None:
    conn = _connect(db_path or _default_db_path())
    conn.execute(
        "UPDATE operations SET status='RUNNING', started_at=? WHERE operation_id=?",
        (_now_iso(), operation_id),
    )
    conn.commit()
    conn.close()


def mark_finished(operation_id: str, status: str, error: str | None = None,
                   db_path: str | None = None) -> None:
    db_path = db_path or _default_db_path()
    conn = _connect(db_path)
    row = conn.execute("SELECT started_at FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    duration_ms = None
    if row and row["started_at"]:
        started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    conn.execute(
        "UPDATE operations SET status=?, finished_at=?, duration_ms=?, error=? WHERE operation_id=?",
        (status, _now_iso(), duration_ms, error, operation_id),
    )
    conn.commit()
    conn.close()


def get_recent_operations(limit: int = 50, target: str | None = None,
                           action: str | None = None, status: str | None = None,
                           db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path or _default_db_path())
    query = "SELECT * FROM operations WHERE 1=1"
    params: list = []
    if target:
        query += " AND target=?"
        params.append(target)
    if action:
        query += " AND action=?"
        params.append(action)
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
