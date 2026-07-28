# ADR-021: Structured Logging Architecture

## Status

Accepted

## Date

2026-07-22 (Owner Experience Milestone 2)

**Acceptance note (2026-07-23):** implemented via delegated Builder tasks
(real DeepSeek V4 Flash, `scripts/delegate_opencode_task.py`, ADR-013),
one for the Python backend and one for Android, each followed by an
independent review pass that found and fixed real defects before
acceptance — see `SESSION.md`'s M-OX.2 entry for the complete list
(a leaked `ContextVar` binding that polluted unrelated tests, a missing
Python 3.12+ `LogRecord` attribute, two broken assertions in the
Builder's own new test file, an existing Android test file that failed
to compile until `VoiceSessionResponse.traceId` was given a default
value, and a quadratic-growth bug in a rotation test that made its own
assertion unreachable). Final state: 530/530 Python tests passing
(511 pre-milestone baseline + 19 new), 230/230 Android unit tests
passing, `./gradlew assembleDebug` clean.

## Context

M-OX.1 (ADR-020) established `trace_id` — the canonical identifier for
"one logical unit of autonomous work initiated or coordinated by the
Supervisor" — and the governing invariant that every future persisted
object from a traced execution must be able to answer "which trace
created me?" Structured Logging is the second Owner Experience milestone
and the first to actually consume that invariant: today, log output
across the backend and Android exists, but is not correlated,
not uniformly structured, and not built with export in mind.

Two genuinely different starting points exist, and this ADR is wrong if
it treats them the same:

- **Python backend**: `app/main.py:53` calls `logging.basicConfig(level=INFO,
  format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")` once, at
  import time. Every module does the standard `logger =
  logging.getLogger(__name__)` then `logger.info(...)`/`logger.warning(...)`
  at dozens of call sites. Output is plain text, console-only, with no
  `trace_id`, no machine-readable field boundaries, and no persistence —
  restart the process and history is gone.
- **Android companion**: `telemetry/TelemetryRecorder.kt` is **not** a
  greenfield problem — it already exists, is already production code, and
  is already called from roughly two dozen sites across
  `WakeWordManager`, `PresenceService`, `CompanionWebSocketClient`,
  pairing, and more. It already writes to both `logcat` and a persistent,
  app-internal file (`telemetry.log`), already has a closed, deliberate
  event-name vocabulary ("do not invent new event names ad hoc"), and
  already has crude size-based rotation (truncate the single file to its
  last 2,000 lines past 1MB). It is plain text
  (`timestamp EVENT_NAME detail`), has no `trace_id`, no severity levels,
  and rotates in place rather than into separate files. This milestone
  evolves that class; it does not replace it.

Neither side should be rebuilt from scratch. The problem this ADR solves
is making both sides **structured, `trace_id`-correlated, and
export-ready** without breaking any existing call site or duplicating
work that already exists and already functions.

## Problem

1. What is the one canonical log record schema every component in every
   language writes to?
2. How is a log line tagged with `trace_id` (per ADR-020's invariant)
   without requiring every one of the ~dozens of existing `logger.info(...)`
   call sites (Python) and ~two dozen `telemetry.record(...)` call sites
   (Android) to be individually rewritten?
3. What does "component" mean consistently across two languages and three
   runtimes (Python backend, Android, OpenCode-as-observed-by-Jarvis)?
4. Where exactly is the line between "logging Jarvis's interaction with
   OpenCode" (in scope) and "logging OpenCode's internals" (impossible —
   Jarvis doesn't own that process's own logs)?
5. What rotates, what doesn't, and does this create a second TD-019
   (unbounded growth) instead of closing anything?

## Decision

### Canonical schema (JSON Lines, one object per line)

```json
{"timestamp":"2026-07-22T18:04:12.345678Z","severity":"INFO","component":"backend.supervisor","message":"tool call completed","trace_id":"trace_abcdef123456","conversation_id":"conv_abcdef123456","task_id":"oc_abcdef123456","runtime_task":"Task-142","fields":{"tool":"start_opencode_task","sequence":1}}
```

| Field | Type | Always present? | Source |
|---|---|---|---|
| `timestamp` | string, ISO-8601 UTC, matching `db.utcnow()`'s exact format | Yes | Record creation time |
| `severity` | string: `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` (Python `levelname`); Android maps `Log` levels to the same five strings | Yes | The log call's own level |
| `component` | string, dot-namespaced (see taxonomy below) | Yes | Derived automatically from logger name / event-name prefix — **never a per-call-site parameter** |
| `message` | string, human-readable | Yes | The log call's own message |
| `trace_id` | string or `null` | Yes (key always present, value nullable) | `app.trace.current_trace_id()` (Python) / last-known-trace field (Android, see below) — the **only** ambient, automatically-injected correlation field, per ADR-020's invariant |
| `conversation_id` | string or `null` | Yes (key always present, value nullable) | Explicit `extra=`/parameter at specific call sites that already have it in scope — **not** ambient, see Alternatives Considered |
| `task_id` | string or `null` | Yes (key always present, value nullable) | Same as `conversation_id` — explicit, not ambient |
| `runtime_task` | string or `null` | Yes | Python: `asyncio.current_task().get_name()` if inside a task, else the thread name. Android: the calling thread's name. This is the *execution unit* identifier — deliberately named differently from `task_id` (a Jarvis domain concept: a local or OpenCode task) so the two are never confused. |
| `fields` | object (possibly empty `{}`) | Yes | Anything else passed via `extra=`/parameters that isn't one of the above — the explicit "avoid embedding structured data inside free-form strings" mechanism |

Every key is always present (nulls, not omissions) so a consumer never
has to branch on a missing field — this is what "consistent schema"
means concretely.

### Component taxonomy

Dot-namespaced, `<runtime>.<subsystem>`, derived automatically — never
passed explicitly at a call site:

**Python** (`app/structured_logging.py`, a prefix-matched lookup table
keyed by logger name, i.e. `__name__`):

| Logger name | Component |
|---|---|
| `app.main` | `backend.server` |
| `app.connection_manager` | `backend.websocket` |
| `app.supervisor.*` | `backend.supervisor` |
| `app.voice_session_manager` | `backend.voice_session` |
| `app.integrations.opencode_*` | `backend.opencode` |
| `app.attention_manager`, `app.attention_scheduler` | `backend.attention` |
| `app.notifications` | `backend.notifications` |
| *(anything unmapped)* | the raw logger name, unmodified — **never dropped**, so a future module that forgets to register itself here still produces a usable, if unclassified, record |

**Android** (`TelemetryRecorder.kt`, a prefix-matched lookup table keyed
by the existing event-name vocabulary):

| Event prefix | Component |
|---|---|
| `WS_`, `DEVICE_STATUS_` | `android.websocket` |
| `WAKEWORD_` | `android.wakeword` |
| `ATTENTION_` | `android.attention` |
| `PAIRING_` | `android.pairing` |
| `APP_`, `SERVICE_`, `FOREGROUND_`, `NOTIFICATION_`, `TASK_REMOVED`, `NETWORK_`, `SCREEN_` | `android.presence` |
| *(anything unmapped)* | `android.other` — never dropped |

**OpenCode-as-observed-by-Jarvis**: `backend.opencode` (same component as
the rest of `OpenCodeSupervisor`) — there is no separate "OpenCode"
component, because Jarvis is never logging OpenCode's own internals, only
its own record of interacting with it (see the dedicated section below).

### Propagation: `trace_id` is the only ambient field

Per ADR-020's invariant ("do not invent alternative correlation
mechanisms"), this milestone reuses `app.trace.current_trace_id()`
exactly as-is — no new ContextVar, no parallel primitive.

- **Python**: a new `logging.Filter` (`JarvisContextFilter` in
  `app/structured_logging.py`) reads `trace.current_trace_id()` and
  stamps it onto `record.trace_id` for *every* LogRecord that passes
  through it, with zero change to any existing `logger.info(...)` call
  site. `conversation_id`/`task_id` are **not** given the same ambient
  treatment — deliberately (see Alternatives Considered) — they are
  attached only at specific call sites, via the standard `extra=` kwarg,
  where they are already in local scope and genuinely add value: the
  Supervisor's existing tool-call log line, and the OpenCode-interaction
  log lines enumerated below. This is real, but bounded and incremental
  work, not a blanket retrofit.
- **Android**: there is no per-turn ambient scope the way Python's
  ContextVar provides, because Android does not mint traces — it only
  *learns* one when a `voice_session_response`/`supervisor_message` frame
  arrives (per M-OX.1's additive `trace_id` field, §2.2.1 of the
  WebSocket protocol doc). A single mutable "last known trace_id" field
  (owned by `CompanionWebSocketClient`, updated whenever such a frame
  arrives) is read by `TelemetryRecorder.record()` as its default when no
  explicit `traceId` argument is given. This is intentionally the
  simplest thing that could work — a call site that wants to say "this
  event belongs to that specific trace" can still pass one explicitly.

### Python integration: additive, not a replacement

`app/structured_logging.py` (new) provides `configure_structured_logging()`,
called once from `app/main.py` in place of the current bare
`logging.basicConfig(...)` call. It attaches **two** handlers to the root
logger, not one:

1. A console `StreamHandler` with the *exact* existing format string —
   anyone watching the console (a developer, a `docker logs`-style
   consumer) sees identical output to today, unchanged.
2. A new `TimedRotatingFileHandler` (daily rotation, `backupCount` from
   `JARVIS_LOG_RETENTION_DAYS`) writing JSONL via `JsonlFormatter`, with
   `JarvisContextFilter` attached so every record carries `trace_id`
   automatically.

No existing `logger.info(...)`/`logger.warning(...)` call anywhere in the
codebase needs to change for this to work. The **only** code changes to
existing call sites are the small number of deliberate `extra=` additions
listed below, for records where `conversation_id`/`task_id`/structured
`fields` genuinely add debugging value beyond what's already ambient.

### OpenCode: log Jarvis's interaction, not OpenCode's internals

Jarvis does not own OpenCode's process, and this milestone makes no
change to OpenCode's own logging. What Jarvis *can* honestly log, all at
`component=backend.opencode`, are events already observed in
`app/integrations/opencode_supervisor.py`:

| Event | Existing call site | New `extra=` fields |
|---|---|---|
| Request received | `start_session()` | `task_id`, `session_id` (once minted) |
| Session created | `start_session()` (existing `logger.info`) | `task_id`, `session_id`, `trace_id` (ambient) |
| Tool invocation / activity evidence | `_handle_activity()` (existing `logger.info`) | `task_id`, `evidence_type` |
| Completion | `_handle_session_idle()` (existing `logger.info`) | `task_id`, `status="completed"`, `duration_seconds` (computed from the `opencode_tasks.created_at` already on the row) |
| Error | `_handle_session_failed()` (existing `logger.info`) | `task_id`, `status="failed"`, `duration_seconds` |
| Follow-up instruction | `send_instruction()` | `task_id` |

No field promises visibility into what OpenCode's own reasoning or tool
execution actually did internally — only what Jarvis's own supervision
loop observed (an SSE event arrived, a status changed, a duration
elapsed). This is the same honesty discipline ADR-018/019 already
established for the Control Center's `owned`/`last_health_check_at`
fields: state what Jarvis can actually verify, never more.

### Control Center: observability for logging only

One additive field on the existing `/api/dashboard/snapshot` endpoint
(already an explicit allow-list, never a raw passthrough — same pattern
`tasks`/`opencode` already follow):

```json
"logging": {
    "enabled": true,
    "level": "INFO",
    "file_path": "logs/jarvis-2026-07-22.jsonl",
    "file_size_bytes": 48213,
    "retention_days": 14
}
```

Read-only, no new WebSocket message type, no log viewer, no search — a
dashboard operator can confirm structured logging is actually running and
roughly how large the current file is. Execution History, Artifact
Management, and Debug Bundle (later OX milestones) are where an actual
log-browsing UI belongs; this milestone deliberately stops at "is it on."

### Rotation and retention

- **Python**: `TimedRotatingFileHandler`, daily rotation at midnight
  local time, `backupCount` from `JARVIS_LOG_RETENTION_DAYS` (default
  14) — old files are deleted by the handler itself, not a separate job.
  Files live under `JARVIS_LOG_DIR` (default `logs/`), named
  `jarvis-YYYY-MM-DD.jsonl`. This is a **file-based** retention surface,
  deliberately separate from `purge_events_older_than()` (ADR-020's
  retention mechanism for the `events` *table*) — the two are not the
  same store and should not be conflated in future work.
- **Android**: `TelemetryRecorder` moves from "truncate the one file to
  its last 2,000 lines past 1MB" to real numbered-file rotation
  (`telemetry.log`, `telemetry.log.1` … `telemetry.log.4`, oldest
  deleted), same 1MB-per-file threshold, capped at 5 files (5MB ceiling
  per install, unchanged order of magnitude from today's cap). Local
  disk only — see Tradeoffs for what this does *not* yet do.

### Configuration model

- **Python**: `JARVIS_LOG_DIR` (default `logs/`), `JARVIS_LOG_LEVEL`
  (default `INFO`), `JARVIS_LOG_RETENTION_DAYS` (default `14`) — same
  `.env`-driven convention as every other Jarvis server setting.
- **Android**: a single Settings toggle (`SettingsActivity`, reusing the
  existing wake-word-toggle pattern and `SecureConfigStore`/settings
  storage) for verbose/debug-level telemetry. Rotation cap is fixed in
  v1, not user-configurable — a knob nobody has asked for yet.

## Alternatives Considered

**Give `conversation_id` its own ContextVar, mirroring `trace_id`.**
Considered because the mechanism is identical and `Supervisor.process_message()`
already knows `conversation_id` at the exact point it binds `trace_id`.
Rejected: ADR-020's invariant is specifically about not multiplying
correlation mechanisms, and `conversation_id` is already fully
reconstructable by anyone holding a `trace_id` (via `tasks`/`conversations`
row lookups) — a second ambient channel would be redundant machinery
solving a problem `trace_id` alone doesn't leave open. Explicit `extra=`
at specific call sites is simpler and just as effective.

**Replace `TelemetryRecorder` with a new Android logging class.**
Rejected outright — it already works, is already called from two dozen
production sites, and already has real rotation and dual-sink behavior.
Rebuilding it would violate this project's own "avoid replacing working
logging code wholesale" instruction for exactly the reason that
instruction exists.

**Ship Android logs to the server now, achieving true centralization.**
Rejected per this milestone's explicit non-goal (no remote upload, no
streaming) — a real capability with real battery/bandwidth/privacy cost
that deserves its own deliberate decision, not a default.

**Require every existing log call site to pass `component=` explicitly.**
Rejected — this is precisely the "replace wholesale" trap. Deriving
`component` from the existing logger name / event-name vocabulary is
free, requires no call-site changes, and degrades gracefully (unmapped →
raw name, never dropped) if a future module is added without updating
the table.

## Consequences

- Every Python log record gains `trace_id` automatically the moment this
  ships, with no changes to existing call sites; a handful of
  `opencode_supervisor.py`/`supervisor.py` call sites gain
  `conversation_id`/`task_id`/`duration_seconds` deliberately.
- `TelemetryRecorder.record()` gains new optional parameters
  (`severity`, `component` override, `traceId`) with defaults that make
  every one of its ~two dozen existing call sites behave identically to
  today unless a caller opts in.
- `DiagnosticsActivity`'s telemetry tail now displays raw JSONL instead
  of the current plain-text line — see Tradeoffs.
- The dashboard snapshot endpoint gains one additive, read-only field.

## Positive Outcomes

- Neither runtime's existing, working logging infrastructure is
  discarded — this genuinely is incremental migration, not a rewrite
  wearing an incremental label.
- `trace_id` propagation required zero new correlation machinery beyond
  what ADR-020 already built — the invariant did its job.
- The OpenCode-interaction boundary is stated precisely enough that a
  future reviewer can check any new log line against it mechanically:
  does this describe something Jarvis's own supervision loop observed,
  or something only OpenCode itself could know? Only the former is ever
  logged.

## Tradeoffs

- `DiagnosticsActivity` displaying raw JSONL instead of a formatted line
  is a real, disclosed UX regression for that one screen until a later
  milestone (Debug Bundle or a dedicated log viewer) gives it a proper
  renderer. Still fully inspectable — just less pretty than today.
- Android's `tailLines()` continues to read only the active log file;
  reading across rotated files is not implemented in this milestone —
  acceptable for "recent activity," not yet sufficient for "everything
  since yesterday."
- Python's console output stays deliberately unstructured (the existing
  plain-text format, untouched) — structured JSONL exists only in the
  file handler. Two different views of the same events, by design, not
  an oversight.
- `conversation_id`/`task_id` are only present on log lines at the
  specific call sites listed above — most log lines will show `null` for
  both, which is correct (most log lines aren't about a specific
  conversation or task) but means "grep the logs for this conversation"
  only reliably works via `trace_id`, not via searching for a
  `conversation_id` value directly on most lines.

## Future Revisit Conditions

Log shipping/co-location (Android → server) once a real cross-device
debugging need demonstrates the battery/bandwidth/privacy tradeoff is
worth it. A real log-viewer/search UI, once Execution History or Debug
Bundle need one. Multi-file tail reading on Android, once "recent
activity" stops being sufficient. A registry-driven component taxonomy
(replacing the two manual lookup tables here) if either list grows large
enough that manual maintenance becomes error-prone.

## References

- ADR-020 (Trace ID — Execution Correlation Model), especially its
  Canonical Definition and Governing Invariant sections, which this ADR
  is the first to build against.
- ADR-017 (Production Wake-Word Foundation), ADR-018/019 (Control
  Center / Observability-Operations separation) — the honesty discipline
  this ADR's OpenCode-interaction boundary follows.
- `docs/TECHNICAL_DEBT.md`, TD-019 — this ADR's file-based retention is a
  separate surface from TD-019's table-growth problem; not to be
  conflated in future accounting.
- `android/app/src/main/java/com/jarvis/companion/telemetry/TelemetryRecorder.kt`
  — the existing production class this milestone evolves, not replaces.

## Related Milestones

Owner Experience Milestone 2 (M-OX.2) — this ADR is its complete
architectural scope; implementation (Python formatter/filter, Android
rotation/schema upgrade, targeted call-site `extra=` additions, the
Control Center snapshot field, tests) follows once this ADR is reviewed
and accepted, per the same discipline used for the Control Center and
M-OX.1.

## Related Source Files

- `app/structured_logging.py` (new — `JsonlFormatter`, `JarvisContextFilter`,
  `configure_structured_logging()`, `_component_for()`, `get_logging_status()`)
- `app/main.py` (`configure_structured_logging()` replaces the bare
  `logging.basicConfig()` call; `logging` block added to
  `/api/dashboard/snapshot`)
- `app/supervisor/supervisor.py` (the existing tool-call `logger.info`
  gains `extra=`)
- `app/integrations/opencode_supervisor.py` (the six existing call sites
  in the table above gain `extra=`; `_handle_session_idle`/
  `_handle_session_failed` gain `duration_seconds`)
- `android/app/src/main/java/com/jarvis/companion/telemetry/TelemetryRecorder.kt`
  (new optional parameters, real multi-file rotation, JSONL rendering,
  component taxonomy)
- `android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt`
  (last-known-`trace_id` field, updated on `voice_session_response`/
  `supervisor_message`)
- `android/app/src/test/java/com/jarvis/companion/telemetry/TelemetryRecorderTest.kt`
  (new)
- `tests/test_structured_logging.py` (new)
