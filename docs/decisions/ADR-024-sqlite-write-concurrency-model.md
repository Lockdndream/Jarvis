# ADR-024: SQLite Write-Concurrency Model

## Status

Accepted

## Date

2026-07-28 (F1.16 — Foundation Sprint)

## Context

Jarvis persists state in local SQLite databases. The main Jarvis DB
(`jarvis.db`) is accessed through `app/database.py:get_conn()`, and a
separate Operations console DB (`operations.db`) is accessed through
`app/operation_history.py:_connect()`. Both currently use WAL mode
(`PRAGMA journal_mode=WAL`) but leave `busy_timeout` at its default of
**0**, meaning a writer that finds the database locked fails immediately
with `database is locked` rather than waiting.

Until now, SQLite writes have been blocking calls on a single event loop,
so they were effectively serialized by Python execution. Task F1.15 will
route DB calls through `asyncio.to_thread()`, introducing **real
concurrent writers** for the first time. Without a bounded wait, ordinary
lock overlap between two writers will surface as immediate failures.

## Problem

What concurrency model should Jarvis use for SQLite writes once multiple
threads can genuinely attempt them at the same time?

## Decision

**WAL mode + `busy_timeout=5000` (a 5-second bounded wait) is the
complete write-concurrency model.** Every SQLite connection created by
`get_conn()` and `app/operation_history.py:_connect()` sets
`PRAGMA busy_timeout=5000` immediately after opening. A writer that
encounters lock contention waits up to 5 seconds; if the lock is still
held, SQLite raises `database is locked`.

No writer queue, connection pool, retry decorator, or other machinery is
introduced. WAL already allows one writer concurrent with many readers,
and Jarvis is a single-process, single-user, local-first application
(ADR-001), so a dedicated writer queue would be complexity without
benefit (CLAUDE.md: no over-engineering).

### Per-connection pragmas

`busy_timeout`, like `foreign_keys`, is a **per-connection** pragma. It
must be set on every connection as it is created, which is why both
`get_conn()` and `_connect()` set it at connection-open time. It is not
sufficient to set it once per process or per database.

## Alternatives Considered

**A single-writer queue or dedicated writer thread.** Rejected. SQLite
in WAL mode already serializes writers at the database level; adding a
process-level queue would not increase real concurrency, would introduce
blocking/serialization of its own, and would require new machinery for a
single-user local application.

**A connection pool.** Rejected. The dominant cost is not connection
creation; the dominant concern is lock contention between writers. A pool
would not solve that and would add lifecycle and thread-safety complexity
(`sqlite3.Connection` objects must not be shared across threads).

**Retry decorators around individual writes.** Rejected as unnecessary
machinery. `busy_timeout` is SQLite's built-in, correct mechanism for
bounded lock waiting. Wrapping every call site in application-level retry
logic would duplicate the same concern, scatter it across the codebase,
and risk hiding real failures.

**A longer or shorter timeout.** 5000 ms is chosen as a generous but
bounded wait: long enough to absorb normal event-loop/thread scheduling
jitter, short enough that a genuinely stuck writer surfaces as an error
rather than hanging indefinitely. Revisit if real usage shows either
routine timeouts or no contention at all.

## Consequences

### Positive

- Lock contention between concurrent writers is handled automatically by
  SQLite within a bounded, explicit time budget.
- The change is a single pragma set at the single place each database's
  connections are created — no new modules, no new call-site logic.
- WAL mode's reader/writer concurrency is preserved; read-heavy paths
  continue to operate while a write is in progress.

### Trade-offs

- `busy_timeout` handles *lock contention*, not *logical atomicity*.
  Multi-step operations still need explicit transactions and correct
  application-level guards.
- A `sqlite3.Connection` cannot be shared across threads, so a
  transaction must execute entirely within one thread/connection. F1.14
  owns transaction boundaries; this ADR does not change them.
- If contention ever exceeds 5 seconds, the caller receives
  `database is locked`. That is treated as a real failure, not silently
  retried.

## Future Revisit Conditions

- Real metrics or incidents show routine `database is locked` under
  normal load — justify a longer timeout or a real concurrency review.
- A future milestone introduces multiple long-running writers that hold
  locks for seconds at a time — may require transaction redesign before
  any queue/pool machinery.
- The project ceases to be single-process/single-user — at that point a
  more elaborate concurrency model may become warranted.

## References

- `docs/decisions/ADR-001-laptop-remains-the-brain.md` (local-first, no
  cloud dependency).
- `app/database.py` (`get_conn()` sets `journal_mode=WAL`,
  `foreign_keys=ON`, and now `busy_timeout=5000`).
- `app/operation_history.py` (`_connect()` sets `busy_timeout=5000` on
  the separate operations DB).
- `docs/MILESTONE_F1_FOUNDATION_SPRINT.md` (F1.15 — async DB call
  conversion; F1.14 — transaction boundaries).
- `CLAUDE.md` (no over-engineering).

## Related Milestones

Foundation Sprint F1.16. Load-bearing prerequisite for F1.15
(`asyncio.to_thread` DB calls), which creates the first genuine
concurrent writers.

## Related Source Files

- `app/database.py`
- `app/operation_history.py`
