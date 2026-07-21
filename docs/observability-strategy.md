# Jarvis Observability Strategy

This document states Jarvis's long-term observability philosophy. It
is broader than the Control Center — the Control Center is the current
*implementation* of this philosophy, not the philosophy itself. A
future subsystem, a future client, or a future rewrite of the
dashboard should all still be judged against what's written here.

## The governing question

Every subsystem, from here forward, should be designed to answer one
question before it ships, not after an incident forces the question:

**"How does the Control Center know this happened?"**

Not "should we add a dashboard widget for this." The dashboard is a
rendering surface, not the point. The point is that the event existed,
observably, the moment it happened — whether or not anyone was looking
at a screen at the time. If a subsystem's author cannot answer the
governing question for a given piece of behavior, that behavior is not
finished, in the same sense that untested code is not finished.

This reframes observability from "a feature we might add" to "a
property every subsystem either has or is incomplete without" — the
same category of requirement as error handling or input validation,
not an optional layer bolted on afterward.

## Why this, and why now

Milestone 9B.10's rehearsal produced three concrete incidents (detailed
in `docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`'s
Context) where real problems existed for days, or for an entire task's
execution, with zero observable trace — not because anyone chose to
hide them, but because nothing in those subsystems' original design
asked the governing question. Jarvis's growing autonomy (a Supervisor
that acts on its own judgment, an OpenCode integration that executes
real unsupervised work, a growing set of always-on background services)
makes this gap more costly with every milestone, not less — autonomy
without observability is not a stable place to build from.

## Event model

An **event** is anything that happened that a human might reasonably
want to know about without having to already suspect it. Not every
internal state change qualifies — a variable being reassigned is not
an event; a `VoiceSession` transitioning state, a tool being called, a
task reaching a terminal status, a connection dropping, are events.

Two kinds of events already exist in this codebase and should stay
distinct rather than merge into one undifferentiated stream:

- **General events** (`ConnectionManager.broadcast()`, `db.save_event()`
  from `app/main.py`'s existing paths) — things the *product* itself
  needs to know about, delivered to whichever client the event concerns
  (a task update to the client that started it, an attention item to
  whoever should be contacted).
- **Observer events** (`ConnectionManager.broadcast_observers()`,
  introduced by ADR-018) — things a *human operator* wants to know
  about for situational awareness, delivered only to clients that
  opted in, and structurally incapable of affecting the product's
  general behavior (Contract 1/2, `control-center-observer-protocol-v1.md`).

Keeping these distinct is deliberate, not accidental duplication: a
future subsystem should ask *whose* event this is before deciding which
path it belongs on, not assume one stream serves both purposes.

## Event ownership

Each event is emitted by exactly the subsystem that caused it, using
that subsystem's own broadcast hook — never inferred, aggregated, or
reconstructed by a downstream consumer. `Supervisor` emits
`supervisor_*` events about its own turns; `VoiceSessionManager` emits
`voice_session_lifecycle` about its own transitions. Nothing external
to a subsystem should ever need to guess what it did from side effects
alone (this is the same discipline ADR-010, Evidence-Based Engineering,
already applies to task-completion state: evidence, not inference).

## Event lifecycle

An event is emitted once, at the moment it becomes true, and is never
retroactively edited. History is append-only (`db.save_event()`,
subject to TD-019's disclosed, not-yet-resolved growth-management gap).
A dashboard or any future consumer reconstructs "current state" by
replaying or snapshotting this history — it does not maintain a
parallel, independently-mutable copy of truth that could drift from
what actually happened.

## Health reporting

Every background service, every external dependency (the LLM API, the
OpenCode server, the database), and every connection should be able to
answer "healthy, degraded, or down" without requiring a human to
correlate multiple unrelated signals by hand. The Control Center's
System Health panel is the current UI for this; the underlying
requirement — that health is a first-class, queryable property of each
subsystem, not an inference from the absence of errors — outlives that
specific panel.

## Task visibility

Any unit of work that takes long enough for a human to wonder "is this
still running?" (today: OpenCode tasks; potentially, in the future,
any other long-running delegated work) must expose: that it started,
that it is still active, incremental evidence that it is making
progress (not merely "no error yet"), and a definitive terminal state
backed by real evidence — never inferred from silence or a timeout
alone. This is not a new requirement; it restates and generalizes the
principle Milestone 9B.10's `opencode activity` logging fix (every
real execution event, not just the first) already established for one
specific subsystem.

## Failure visibility

A failure must be observable at the moment it happens, with enough
context to know what failed and what — if anything — the system did in
response. The specific failure mode ADR-018's Context describes (an
LLM API failure silently taking down an entire WebSocket connection)
is the canonical example of what this requirement exists to prevent:
not the failure itself, which can't always be avoided, but a failure
that produces no trace of what happened or why the user experienced
what they experienced.

## User attention model

"What does Jarvis need my attention for" is already a first-class,
deterministic concept in this codebase (`app/attention_manager.py`,
ADR-003). The Control Center's Attention Center panel is a viewer onto
that existing model, not a parallel one — any future extension to what
counts as "needs attention" belongs in `attention_manager.py` first;
the dashboard should only ever be reflecting that system, never
defining its own separate notion of urgency.

## Metrics strategy

Jarvis does not currently have, and this document does not propose
adding, a general-purpose metrics/timeseries system (a Prometheus-style
counter/gauge/histogram layer). The Connectivity Monitor's latency and
reconnect-count figures, and any future "task analytics" (see the
roadmap), are computed from the same event history described above,
not from a separate metrics pipeline. Introducing a dedicated metrics
system is deliberately not part of this strategy today — it would be
new infrastructure solving a problem (dashboards, aggregation,
retention policy) this single-user, single-laptop deployment does not
yet have, and should only be reconsidered if that changes (see the
roadmap's Version 2.0 discussion of multi-device/distributed scenarios).

## How future systems should integrate

1. Before writing a new subsystem (or a significant new capability in
   an existing one), answer the governing question: what event(s) does
   this produce, and who is the intended observer — the product itself
   (general event) or a human operator (observer event)?
2. If it's an observer event: call `set_broadcast_hook()` in the
   owning module (mirroring `supervisor.py`/`voice_session_manager.py`),
   gate any persistence behind `ConnectionManager.has_observers()`
   (Contract 6), and add the new event type to
   `control-center-observer-protocol-v1.md` §2 before considering the
   work done.
3. If it's a general event: use the existing `ConnectionManager.broadcast()`
   / `db.save_event()` paths already established, and never route it
   through the observer channel (Contract 1) even if a dashboard panel
   would also find it interesting — extend the snapshot endpoint or a
   dedicated general event's own delivery instead.
4. Never expose reasoning/chain-of-thought text through either path
   (Contract 7) — describe what happened, not why the LLM decided it.
5. Update this document's event model or the protocol document if a
   genuinely new *kind* of event emerges that doesn't fit the
   general/observer split as currently defined — don't force a new
   concept into an ill-fitting existing category.

## References

- `docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`
- `docs/protocols/control-center-observer-protocol-v1.md`
- `app/attention_manager.py`, ADR-003 (the pre-existing attention model
  this strategy explicitly does not duplicate)
- ADR-010 (Evidence-Based Engineering)
- `docs/TECHNICAL_DEBT.md`, TD-019 (unbounded event/task table growth —
  this strategy's append-only event model inherits, not resolves, this
  existing risk)
