# ADR-020: Trace ID — Execution Correlation Model

**Addendum, 2026-07-22 (M-OX.1 acceptance):** the Canonical Definition and
Governing Invariant sections below were added at acceptance, at the
user's explicit direction, to give every subsequent Owner Experience
milestone (starting with M-OX.2, Structured Logging) an unambiguous
definition to build against. The rest of this document is unchanged from
what M-OX.1 implemented and is not being retroactively rewritten.

## Status

Accepted

## Date

2026-07-22 (Owner Experience Milestone 1)

## Canonical Definition

**A trace represents one logical unit of autonomous work initiated or
coordinated by the Supervisor.**

This is the project's canonical definition of a trace, referenced by
name (not restated) in every later ADR that touches trace_id. It is
deliberately narrower than "anything that happens in Jarvis": a trace
exists only where the Supervisor is coordinating — which is why a
locally-started task issued directly via a slash command, with no
Supervisor turn behind it, correctly has no trace_id (see Tradeoffs), and
why `voice_sessions` correctly has none of its own (a session is a
container that can hold many traces, one per turn, not a unit of work
itself).

## Governing Invariant for Future Persisted Objects

**Every persisted object created as part of a traced execution must be
able to answer: "Which trace created me?"**

This applies to every future subsystem that persists execution-related
state — artifacts, execution history, notifications, log records, and
any additional persisted execution metadata not yet designed. The
mechanism is always `trace_id` as defined by this ADR: read it from
`app.trace.current_trace_id()` while a Supervisor turn is in progress, or
recover it from an already-tagged row (the pattern
`OpenCodeSupervisor.start_session()` establishes for the async-completion
case) when the work outlives the turn that started it. Future subsystems
must not invent an alternative correlation identifier, a parallel
"session" concept, or a bespoke join key to solve the same problem —
doing so would fragment exactly what this ADR exists to unify. If a
future subsystem finds a case `trace_id` genuinely cannot express, that
is a reason to extend this ADR (or write a superseding one), not a
license to work around it silently.

## Context

The Owner Experience (OX) initiative's stated mission is that the owner
should be able to operate, observe, debug, and trust Jarvis without
relying on Claude — "every autonomous action must leave a permanent,
queryable trace... future debugging should begin from evidence rather
than memory." Five of the six OX projects (Unified Logging, Execution
Trace, Execution History, Artifact Management, Debug Bundle) all depend
on being able to answer one question first: given a single user request,
which rows — across `conversations`, `tasks`, `opencode_tasks`, and
`events` — belong to it?

Today, nothing answers that question. `conversation_id` groups many turns
of one ongoing conversation together, not one turn. `task_id`/`session_id`
identify one OpenCode execution, but not the turn that started it or the
other records (the user's message, the tool call, the events) produced
alongside it. There is no way to take one user request and reconstruct
everything Jarvis did because of it.

A closely related primitive already exists: `client_request_id`
(ADR-017), minted by an Android client to correlate a wake-word detection
with its `voice_session_open` response. ADR-017 states its own intended
scope explicitly: "deliberately generic, not a wake-word-specific field,
because any future asynchronous client operation... has the identical
correlation problem and should reuse this same primitive rather than
inventing its own feature-specific identifier." It correlates *session
opening*, not the turns that happen inside a session once it's open, and
it doesn't exist server-side as a database concept at all today — it is
purely an echoed WebSocket field.

## Problem

How does Jarvis assign one identifier to "everything that happened
because of this one request" — spanning a synchronous reply path (the
Supervisor's tool-calling loop) and an asynchronous one (an OpenCode task,
whose completion can arrive minutes after the turn that started it
already returned) — without inventing a parallel correlation mechanism
alongside `conversation_id`/`voice_session_id`/`task_id`, and without
threading a new parameter through every intervening function signature
between `Supervisor.process_message()` and `OpenCodeSupervisor.start_session()`?

## Decision

**`trace_id` identifies one turn — one call to
`Supervisor.process_message()`** (the single entry point every voice
transcript and every typed message already passes through). It is
server-minted (`trace_<12 hex chars>`, `app/trace.py:new_trace_id()`),
generated fresh on every call, and is not itself a new *kind* of
correlation concept — it is `client_request_id`'s existing, ADR-017-stated
purpose (a reusable async-operation correlation primitive), applied to
every turn rather than only to session-opening. A future client-supplied
seed is deliberately left as a revisit condition rather than implemented
now (see below) — this ADR fixes the server-side model first.

- **Propagation is via a `contextvars.ContextVar`
  (`app/trace.py:current_trace_id()`), not a threaded parameter.**
  `Supervisor.process_message()` binds it once, at the top, for the
  lifetime of that call. Every asyncio Task gets its own independent copy
  of the current context, so this is safe under the exact concurrency
  `_active_turns`' own comment already calls out — two turns (e.g. phone
  + PWA) can be in flight at once without their trace_ids crossing.
  `ToolRegistry.call()` → `OpenCodeSupervisor.start_session()` reads the
  ContextVar directly; no signature in that call chain changed.
- **The async leg is solved by persisting `trace_id` on the
  `opencode_tasks` row at creation, not by threading it through a later
  callback.** `start_session()` reads `trace.current_trace_id()` while
  still inside the turn that's starting the task, and writes it onto both
  the `tasks` and `opencode_tasks` rows immediately. The SSE-driven
  handlers (`_handle_activity`, `_handle_session_idle`,
  `_handle_session_failed`) run in a *different* asyncio Task
  (`_sse_loop`'s background task) with no ContextVar binding of their
  own — they can look `trace_id` up from the row exactly because it was
  already written there, long before their event arrives.
- **`trace_id` is added to four existing tables** — `conversations`,
  `tasks`, `opencode_tasks`, `events` — as an additive, nullable column
  (`_ensure_column` migration, matching every other schema change in
  `app/database.py`). No new table. Existing rows read back as `NULL`,
  same convention as `termination_reason`, `last_evidence_type`, etc.
  `voice_sessions` deliberately does **not** get a `trace_id` column: a
  session spans many turns, so it has no single trace_id to hold — the
  *turns* that happen inside it each get their own, recorded on
  `conversations`/`events`/`opencode_tasks`, not on the session row.
- **Round-tripped, not required.** `voice_session_response` and
  `supervisor_message` (the two WebSocket replies that terminate a turn)
  now include `trace_id` — `null` when the reply never reached a full
  Supervisor turn (e.g. a stale-bound-attention early reply). No client
  reads it yet; this is additive groundwork for a client that wants to
  correlate its own local state with the server's record of that turn,
  not a requirement placed on any existing client today.
- **Retention: `events` gets one concrete, enforceable policy now.**
  `db.purge_events_older_than(days)` deletes `events` rows older than a
  given age and returns the count removed. `events` is the
  fastest-growing trace-carrying table (one row per broadcastable turn,
  tool call, and lifecycle step) and the one this milestone puts a
  ceiling on. TD-019 (unbounded growth across nine tables) is updated to
  reflect this as a partial resolution, not a closure — `conversations`,
  `tasks`, and `opencode_tasks` remain unaddressed, and
  `purge_events_older_than` is deliberately **not** wired to a scheduler
  here; invoking it on a cadence is an operational/plumbing concern for a
  later OX milestone, not a data-model decision.

## Alternatives Considered

**A fourth, independent trace table (`traces`), written to explicitly by
every call site.** Rejected — this is exactly the "invent a parallel
mechanism" the Problem section rules out, and it would require every
existing row-creation call site to remember to also write to a second
table, a coordination burden with no enforcement.

**Thread `trace_id` as an explicit parameter through
`ToolRegistry.call()` → each tool handler → `OpenCodeSupervisor`.**
Rejected — `ToolRegistry.call()` and every one of its dozen-plus
registered handlers would need a new parameter purely to pass a value
none of them use directly, for a concept (per-turn correlation) that is
naturally task-scoped, which is precisely what `contextvars.ContextVar`
exists for.

**Reuse `conversation_id` as the correlation key instead of minting
something new.** Rejected — `conversation_id` deliberately spans many
turns (that's its whole purpose, conversation continuity). Grouping "one
request's full execution" by `conversation_id` would return every turn of
an entire ongoing conversation, not the one being debugged.

**Have the client mint `trace_id`/extend `client_request_id` to every
message now, immediately.** Rejected for *this* milestone specifically —
this ADR is scoped to the server-side model per the approved M-OX.1
boundary ("do not begin any implementation beyond the trace model,
schema, retention policy, and ADR"); extending the wire protocol's
inbound side touches Android and PWA client code, which is out of scope
here. Recorded as a Future Revisit Condition below, not silently dropped.

## Consequences

- Every turn through `Supervisor.process_message()` — voice or text,
  fast-path or full LLM tool-calling loop — now produces a `trace_id`
  that ties together its conversation messages, any task/OpenCode task it
  started, and every dashboard-observable event it emitted, including
  ones whose completion arrives after the turn itself returned.
- `_broadcast()` and `_broadcast_lifecycle()` now tag every event they
  write with the current trace_id (or `None` outside any turn) with no
  change to their call sites' own logic — the ContextVar read happens
  once, inside each function.
- Four tables gained one nullable column each; every existing call site
  of `save_event`/`create_task_record`/`create_opencode_task_record`/
  `save_conversation_message` continues to work unchanged (the new
  parameter is keyword-only with a `None` default).
- `events` now has an enforceable, if not yet scheduled, retention
  mechanism. The other eight tables TD-019 names do not yet.

## Positive Outcomes

- The hardest case (an OpenCode task whose real completion arrives
  asynchronously, well after the turn that started it returned) is
  solved correctly at the schema level, not papered over — this is the
  exact scenario every later OX project (especially Execution Trace and
  Artifact Management) needs to already work before they can build on it.
- No existing call site, test, or client had to change its own signature
  or behavior to get this — the ContextVar approach and keyword-only
  defaults make this a genuinely additive milestone.
- `client_request_id`'s own stated generalization intent (ADR-017) is
  honored rather than duplicated: this milestone reuses that primitive's
  *purpose*, it does not introduce a competing one.

## Tradeoffs

- A `ContextVar` is an implicit channel — a future engineer reading
  `OpenCodeSupervisor.start_session()` in isolation will not see where
  `trace_id` comes from without knowing this ADR exists. The module
  docstring in `app/trace.py` and inline comments at each read site are
  the mitigation, not a substitute for this document.
- `trace_id` is currently server-minted only; a client cannot yet
  correlate its own pre-request state (e.g. "the button I just pressed")
  with the server's trace_id until a future milestone extends the wire
  protocol's inbound side. Until then, `trace_id` is useful for
  server-side debugging and for later OX projects, but not yet for a
  client-side "show me what happened when I did X" feature.
- Only `events` has a retention policy; `conversations`, `tasks`, and
  `opencode_tasks` still grow unbounded (TD-019, updated not closed).
- `voice_session_manager._broadcast_lifecycle("transcript_received", ...)`
  fires *before* `process_message()` mints a trace_id (the turn hasn't
  started yet at that point), so that one lifecycle event always carries
  `trace_id: null` and cannot be joined to the turn it precedes by
  trace_id alone. Acceptable now; a real thing for Execution Trace
  (a later OX milestone, which needs a turn's *complete* timeline) to
  resolve, not a defect in this one.
- Local tasks created via `task_manager`'s slash-command path (not routed
  through a Supervisor turn at all) get no `trace_id` — consistent with
  the per-turn model (there is no turn to correlate them to), but worth
  stating explicitly so a `NULL` there is never later mistaken for a bug.

## Future Revisit Conditions

Extend the WebSocket protocol so a client can supply a `client_request_id`
on `voice_session_transcript` and `user_message` (today only
`voice_session_open` carries one), and have `process_message()` accept it
as the seed for `trace_id` instead of always minting server-side — this
is the natural point at which `client_request_id` and `trace_id` become
the same field end-to-end, closing the gap this ADR's rejected-alternative
section left open deliberately. Extend the SSE handlers
(`_handle_activity`/`_handle_session_idle`/`_handle_session_failed`) to
read `trace_id` off the `opencode_tasks` row and tag their own
`_notify_broadcast()`/`notifications.notify()` events with it, once a
later OX milestone's Execution Trace surface actually needs that
cross-referencing. Add retention to `conversations`, `tasks`, and
`opencode_tasks` once their growth actually threatens performance (TD-019).
Revisit if a future client integration (a hypothetical ESP companion,
per ADR-017's own revisit note) needs trace correlation before the
WebSocket extension above happens.

## References

- ADR-017 (Production Wake-Word Foundation) — `client_request_id`'s
  origin and its own stated generalization intent, which this ADR
  extends rather than replaces.
- ADR-018 (Jarvis Control Center — Observability Architecture), ADR-019
  (Separation of Observability and Operations) — the observer/broadcast
  infrastructure this milestone tags with `trace_id`, and the
  Observe/Act boundary the OX initiative's six projects were mapped
  against before this ADR was written.
- `docs/protocols/websocket-protocol-v1.md` §2.2.1 — the wire-level
  `trace_id` addition.
- `docs/TECHNICAL_DEBT.md`, TD-019 (unbounded table growth — updated to
  reflect `events`' new partial retention mechanism).
- `SESSION.md`, Owner Experience Milestone 1.

## Related Milestones

Owner Experience Milestone 1 (M-OX.1) — this ADR, the schema migration,
and the retention function are its complete scope; M-OX.2 through M-OX.6
(structured logging, Execution History, Artifacts, Debug Bundle,
Launcher) are gated on this decision and not started.

## Related Source Files

- `app/trace.py` (new — `new_trace_id()`, `current_trace_id()`,
  `bind_trace_id()`, `reset_trace_id()`)
- `app/database.py` (`trace_id` columns on `events`/`tasks`/
  `opencode_tasks`/`conversations`; `save_event`, `create_task_record`,
  `create_opencode_task_record`, `save_conversation_message` gain an
  optional `trace_id` parameter; new `get_events_by_trace_id`,
  `purge_events_older_than`)
- `app/supervisor/supervisor.py` (`process_message()` mints and binds
  `trace_id`; `_broadcast()`, `_persist_conversation()`,
  `_persist_tool_call()` read it)
- `app/integrations/opencode_supervisor.py` (`start_session()` persists
  `trace_id` onto the task rows at creation)
- `app/voice_session_manager.py` (`_broadcast_lifecycle()` accepts an
  explicit `trace_id`; `handle_transcript()`'s two Supervisor-routed
  return paths propagate it)
- `app/main.py` (`voice_session_response`, `supervisor_message` echo
  `trace_id`)
- `tests/test_trace_id.py` (new)
