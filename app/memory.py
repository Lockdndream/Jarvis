"""SQLite-backed persistence layer for the v1 memory system.

Three tiers (core_fact / episodic / explicit) plus decision and preference
are stored in the `memories` table and retrieved via an FTS5 full-text index.
This module is intentionally read/write data access only — no write-path
wiring into supervisors, workers, or tools lives here.
"""

import re
import uuid

from app import config
from app.database import get_conn, utcnow
from app.supervisor.projects import get_projects

CONTENT_MAX = 4000
_TRUNCATION_MARKER = "... (truncated)"


def _truncate(content: str) -> str:
    """Bound memory content at write time to prevent runaway context growth."""
    if len(content) <= CONTENT_MAX:
        return content
    max_body = CONTENT_MAX - len(_TRUNCATION_MARKER)
    return content[:max_body] + _TRUNCATION_MARKER


def _sanitize_fts_query(query: str) -> str | None:
    """Convert arbitrary user text into a safe FTS5 MATCH expression.

    FTS5 treats hyphens, quotes, colons, and bare AND/OR/NOT as query
    operators, so raw text produces syntax errors. We tokenize on
    alphanumeric runs, drop tokens shorter than 3 characters, and quote
    every surviving token. Tokens are joined with OR so any matched word
    returns the memory.
    """
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", query) if len(t) >= 3]
    if not tokens:
        return None
    return " OR ".join(f'"{token}"' for token in tokens)


def _row_to_dict(row) -> dict:
    return dict(row)


def store_memory(
    category: str,
    content: str,
    project: str | None = None,
    source: str = "conversation",
    source_id: str | None = None,
    metadata: str | None = None,
    expires_at: str | None = None,
) -> str:
    """Persist a new memory and its FTS5 entry. Returns the generated id."""
    memory_id = f"mem_{uuid.uuid4().hex[:12]}"
    now = utcnow()
    truncated = _truncate(content)

    conn = get_conn()
    conn.execute(
        "INSERT INTO memories "
        "(id, category, project, content, source, source_id, created_at, updated_at, expires_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (memory_id, category, project, truncated, source, source_id, now, now, expires_at, metadata),
    )
    conn.execute(
        "INSERT INTO memories_fts (id, content) VALUES (?, ?)",
        (memory_id, truncated),
    )
    conn.commit()
    conn.close()
    return memory_id


def retrieve_memories(
    query: str,
    project: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Search memories by FTS5 query, optionally scoped to a project.

    Expired memories (expires_at in the past) are excluded. Results are
    returned in FTS relevance order.
    """
    match_expr = _sanitize_fts_query(query)
    if match_expr is None:
        return []

    now = utcnow()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
        (match_expr, limit * 4),
    ).fetchall()

    if not rows:
        conn.close()
        return []

    ids = [r["id"] for r in rows]
    order_case = " ".join(f"WHEN ? THEN {i}" for i, _ in enumerate(ids))
    params: list = list(ids)
    params.append(now)
    if project is not None:
        params.append(project)
    params.extend(ids)

    where = "id IN (" + ",".join("?" for _ in ids) + ") AND (expires_at IS NULL OR expires_at >= ?)"
    if project is not None:
        where += " AND project = ?"

    sql = f"SELECT * FROM memories WHERE {where} ORDER BY CASE id {order_case} END"

    memory_rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in memory_rows[:limit]]


def get_core_facts() -> list[dict]:
    """Return all non-expired core facts, oldest first."""
    now = utcnow()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM memories WHERE category='core_fact' "
        "AND (expires_at IS NULL OR expires_at >= ?) "
        "ORDER BY created_at ASC",
        (now,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_memory(id: str, content: str) -> None:
    """Update a memory's content and refreshed updated_at, keeping FTS5 in sync.

    No-op cleanly if the id does not exist.
    """
    now = utcnow()
    truncated = _truncate(content)

    conn = get_conn()
    cur = conn.execute(
        "UPDATE memories SET content=?, updated_at=? WHERE id=?",
        (truncated, now, id),
    )
    if cur.rowcount == 0:
        conn.close()
        return

    conn.execute("DELETE FROM memories_fts WHERE id=?", (id,))
    conn.execute(
        "INSERT INTO memories_fts (id, content) VALUES (?, ?)",
        (id, truncated),
    )
    conn.commit()
    conn.close()


def delete_memory(id: str) -> None:
    """Delete a memory and its FTS5 entry. No-op cleanly if it does not exist."""
    conn = get_conn()
    conn.execute("DELETE FROM memories_fts WHERE id=?", (id,))
    conn.execute("DELETE FROM memories WHERE id=?", (id,))
    conn.commit()
    conn.close()


def get_memories_by_source(source: str, source_id: str | None) -> list[dict]:
    """Return all memories with the exact source/source_id pair."""
    conn = get_conn()
    if source_id is None:
        rows = conn.execute(
            "SELECT * FROM memories WHERE source=? AND source_id IS NULL ORDER BY created_at DESC",
            (source,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memories WHERE source=? AND source_id=? ORDER BY created_at DESC",
            (source, source_id),
        ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_recent_memories(project: str | None = None, limit: int = 10) -> list[dict]:
    """Return most recently created non-expired memories."""
    now = utcnow()
    conn = get_conn()
    if project is None:
        rows = conn.execute(
            "SELECT * FROM memories WHERE (expires_at IS NULL OR expires_at >= ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (now, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memories WHERE project=? AND (expires_at IS NULL OR expires_at >= ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (project, now, limit),
        ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def seed_core_facts() -> int:
    """Seed immutable doctrine and configured identity into core facts.

    Idempotent: only inserts facts whose exact content is not already
    stored with source="startup_seed". Safe to call on every startup.
    Returns the number of newly inserted facts.
    """
    existing = get_memories_by_source("startup_seed", None)
    existing_contents = {row["content"] for row in existing}

    candidates: list[str] = []

    name = config.user_name()
    if name:
        candidates.append(f"The user's name is {name}.")

    projects = get_projects()
    if projects:
        display_names = [info.get("display_name", alias) for alias, info in projects.items()]
        candidates.append(f"Known projects: {', '.join(display_names)}.")

    candidates.extend(
        [
            "Local-first: Jarvis avoids cloud dependencies for core operation; the laptop is the brain and the phone is the interface.",
            "Evidence over inference: work is not marked complete based on silence or assumption — verifiable evidence is required.",
            "The supervisor routes tasks to workers; it never directly opens files or runs shell commands itself.",
            "No over-engineering: prefer additive, minimal changes over new infrastructure (no Redis/Celery/Docker/frontend frameworks).",
        ]
    )

    inserted = 0
    for content in candidates:
        if content not in existing_contents:
            store_memory(category="core_fact", source="startup_seed", content=content)
            inserted += 1
    return inserted
