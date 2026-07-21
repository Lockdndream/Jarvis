# ADR-018: Jarvis Control Center — Observability Architecture

## Status

Accepted

## Date

2026-07-22

## Context

Jarvis had grown, by Milestone 9B.10, into a system with real autonomy:
a Supervisor that makes tool-calling decisions on its own, an OpenCode
integration that executes real, unsupervised, potentially long-running
work on the user's laptop, and a background-service layer (wake word,
presence, voice-session lifecycle, idle-session reaping, attention
scheduling) that runs continuously whether or not anyone is watching.
None of this had a live view. The only way to know what Jarvis was
doing was to read `SESSION.md`, grep a log file, or query `jarvis.db`
directly — all after the fact, all requiring the person asking to
already know approximately what to look for.

This stopped being a tolerable gap during Milestone 9B.10's own
release-candidate rehearsal, not hypothetically:

- A production table (`tasks`) accumulated 131 leftover test-fixture
  rows and a dependent 126 fake `questions` rows over eleven days,
  undetected, because nothing surfaced "recent activity" in a form a
  human would glance at and notice looked wrong. It was found only
  because the Supervisor's own tool-calling started answering
  questions about the wrong task, and answering *that* required a
  live SQL investigation.
- A `_handle_activity()` code path that had been silently updating the
  database on every real OpenCode execution step, the entire time,
  with no observable trace at all — discovered only when a rehearsal
  needed to visually prove OpenCode was doing real work on camera, and
  there was nothing to show.
- An LLM API connectivity failure crashing an entire WebSocket
  connection (not just the one conversational turn) went undetected
  in code review until a real device rehearsal happened to hit it —
  because nothing made a mid-flight Supervisor failure observable
  before it took the whole connection down with it.

Each of these was eventually found, but only through ad hoc, one-off
investigation — SQL queries written by hand, log files grepped after
the fact, real-device rehearsals used as the only practical test
harness for questions like "is OpenCode actually still running or did
it hang?" None of that investigative effort produced anything reusable
for the next time. The underlying problem is structural, not a series
of unrelated bugs: **Jarvis had autonomy without observability**, and
every incident above was a symptom of that same gap, not a separate
root cause.

## Problem

How does Jarvis expose what it is doing — in real time, to a human,
without requiring them to already know what to look for or where to
look — without turning every subsystem's authors into observability
plumbers, without becoming a second, competing real-time protocol that
Android and the PWA have to reason about, and without becoming a
performance or security liability on the one deployment model
(phone + laptop, LAN, single user) the whole system exists to serve?

## Decision

**The Control Center is a first-class, permanent subsystem of Jarvis
(Core Infrastructure, not an optional add-on) — a desktop-only,
read-only, operator console that observes the system through the same
primitives every other client uses, on a strictly opt-in side channel
that cannot affect and is invisible to the phone or PWA.**

This section states the decision; `docs/protocols/control-center-observer-protocol-v1.md`
is the durable, wire-level contract this decision commits Jarvis to.

1. **Observer model, not a second protocol.** A dashboard client
   connects to the exact same `/ws` endpoint every client uses, with
   the exact same JWT auth (`docs/protocols/websocket-protocol-v1.md`
   §1.1) — there is no separate real-time channel, no separate
   authentication mechanism, no separate connection-lifecycle model to
   maintain. It distinguishes itself with exactly one additional
   message, `register_observer`, sent once after connecting.

2. **Observer broadcasts are structurally separate from the general
   broadcast path.** `ConnectionManager.broadcast_observers()` is a
   distinct fan-out from `ConnectionManager.broadcast()`, reaching only
   connections that have called `mark_observer()`. Every event type
   introduced by this subsystem (`supervisor_turn_started`,
   `supervisor_tool_call`, `supervisor_turn`, `voice_session_lifecycle`,
   `device_status_update`) is routed exclusively through
   `broadcast_observers()`. This is the single load-bearing invariant
   of the whole design — see Contract 1 in the protocol document — and
   it exists because the alternative (folding new event types into the
   general `broadcast()`) was tried first, during implementation, and
   broke six existing protocol tests through cross-talk: a dashboard
   event landing on a phone or PWA connection that had no way to
   recognize or ignore it. That failure is the concrete evidence this
   separation is necessary, not a hypothetical concern.

3. **A snapshot endpoint for bootstrap, not for polling.**
   `GET /api/dashboard/snapshot` exists because a freshly opened
   dashboard has no history — it needs a one-shot answer to "what is
   the state of the world right now" before the WebSocket's live
   stream can start extending it. It is not a general-purpose query
   API and is not intended to grow additional fields on demand; see
   Contract 5 (explicit allow-lists) for why.

4. **Authentication is not optional and not different from anything
   else.** The snapshot endpoint requires the same `_require_api_token`
   dependency every other authenticated endpoint uses. The dashboard's
   own WS-token fetch and snapshot fetch send whatever auth the
   deployment requires, exactly like the Android companion and the PWA
   already do. A dashboard is a client like any other client; it does
   not get a private exemption from the security model because it is
   "just for me."

5. **Zero observers connected means zero extra cost.**
   `ConnectionManager.has_observers()` gates both the broadcast *and*
   the `db.save_event()` write that would otherwise happen
   unconditionally on every Supervisor turn and every voice-session
   lifecycle transition. Observability is additive to the phone-facing
   hot path only when something is actually watching — never a
   standing tax on ordinary use.

6. **Explicit field allow-lists, not `SELECT *`.** Every field the
   snapshot endpoint returns is deliberately chosen to match what
   `dashboard.js` actually renders, following the exact convention
   `/api/task/{id}` already established (deliberately withholding raw
   command/instruction text). A new column added to any underlying
   table in the future does not silently become part of this API's
   contract; someone has to choose to add it.

7. **Observable execution, never chain-of-thought.** The "Decision
   Stream" panel and the `supervisor_tool_call`/`supervisor_turn`
   events it renders describe *what the Supervisor did* (which tool,
   with what arguments, what result) — never the LLM's internal
   reasoning text. There is no "reason" field anywhere in this
   codebase's tool-calling loop to leak in the first place; this is a
   design commitment to keep it that way, not a filter bolted onto
   something that currently exposes more.

## Alternatives Considered

**A dedicated observability service/process, separate from the main
FastAPI app.** Rejected: for a single-user, single-laptop deployment,
a second long-running process to keep alive, restart, and reconcile
state with the primary backend is pure operational overhead with no
corresponding benefit. The primary backend already has direct,
synchronous access to everything worth observing.

**Polling instead of a WebSocket-based observer channel.** Considered
seriously, since it would have avoided touching `ConnectionManager`
entirely. Rejected because it cannot deliver the actual product
requirement — "instant" awareness of what Jarvis is doing right now —
without either polling so aggressively it becomes its own load
problem, or accepting a visible lag that defeats the purpose of a
mission-control view. Polling is retained only for the snapshot
endpoint's narrow bootstrap/reconciliation role (a handful of numeric
fields, every 5 seconds), not as the primary data path.

**Folding dashboard events into the existing general `broadcast()`.**
Tried literally, during implementation, not merely considered on
paper. Rejected on direct evidence: it broke six existing protocol
tests via cross-talk between unrelated connection types. This is the
strongest evidence in this ADR for why the observer/general split is
load-bearing rather than a stylistic preference.

**A separate authentication model for the dashboard** (e.g., a
localhost-only exemption, or a separate simpler token). Rejected
because it would create exactly the kind of "quiet exception" this
project's own security posture (ADR-011, ADR-014, TD-018) has
repeatedly found to be where real gaps hide — verified concretely
during hardening, when the dashboard's own WS-token fetch was found
sending no auth header at all, which would have made the entire
subsystem silently non-functional the moment `JARVIS_API_TOKEN` is
ever set. Bringing it under the identical auth model closed that gap
by construction rather than by a special case.

**Returning full ORM/row objects from the snapshot endpoint for
developer convenience.** Rejected — verified during hardening that the
initial implementation's `SELECT *`-backed task list would have
returned raw task command/instruction text that `/api/task/{id}`
already deliberately withholds. Convenience for whoever extends this
next is not worth an accidental, silent data-exposure contract.

## Consequences

### Positive

- **Observability**: the three real incidents in Context (stale test
  data, invisible OpenCode execution, a connection-crashing failure)
  each become directly visible going forward — a stale/anomalous task
  list, a silent subsystem, and an unhandled failure are exactly the
  categories of thing the Live Activity Feed, Event Timeline, and
  Recent Alerts panels exist to surface without requiring anyone to
  already suspect something is wrong.
- **Maintainability**: the observer/general broadcast split, the
  explicit allow-list convention, and the has-observers cost gate are
  all patterns any future event source can adopt directly rather than
  re-deriving.
- **Operational awareness**: "is Jarvis healthy, what is it doing,
  what needs my attention" becomes answerable by looking, not by
  querying `jarvis.db` by hand — which is the exact investigative
  workflow every incident in Context required until now.
- **Extensibility**: `set_broadcast_hook()` reuses `attention_manager.py`'s
  existing None-safe, set-once-at-startup pattern rather than
  inventing a new one — any future subsystem wanting to become
  observable has one established convention to follow, not several
  competing ones.

### Trade-offs

- **Additional infrastructure**: a new REST endpoint, a new WS message
  type, and two new broadcast hooks are now permanent surface area
  with the durability commitments `docs/protocols/control-center-observer-protocol-v1.md`
  describes — see that document's contracts for exactly what breaks
  if they are violated.
- **Event management complexity**: the Supervisor and VoiceSessionManager
  now each carry an extra, structurally separate broadcast/persistence
  path alongside their existing control flow. Verified additive and
  non-invasive (both files were diffed line-by-line against their
  pre-existing behavior during review — the LLM-call failure handling
  and all existing return values are untouched), but it is still one
  more thing a future change to either file must not silently break.
- **Observer lifecycle management**: multiple dashboard tabs, a
  dashboard client that reconnects without re-registering, and a
  dashboard client that never registers at all are all real states
  `ConnectionManager` now has to handle correctly forever, not just at
  ship time.

## Future Revisit Conditions

Revisit this decision if a second observing client type emerges with
materially different needs than a desktop browser dashboard (e.g., a
CLI diagnostics tool, a remote/non-LAN observer per TD-003) — the
current design assumes "observer" means "desktop dashboard on the same
`/ws` connection," and a sufficiently different client might justify a
distinct channel rather than stretching this one. Revisit if the event
volume ever makes `has_observers()`-gated persistence insufficient on
its own — TD-019 (unbounded `events` table growth) already exists
independently of this subsystem and this ADR does not resolve it, only
avoids making it worse when no observer is connected.

## References

- `SESSION.md`, Milestone 9B.10 (the RC-validation rehearsal night this
  ADR's Context is drawn from — server restart/network-loss/idle-timeout
  failure injection, the stale Mock Agent data discovery, the invisible
  OpenCode execution finding)
- `docs/TECHNICAL_DEBT.md`, TD-019 (unbounded event/task table growth —
  pre-existing, not resolved by this ADR), TD-018 (no auth by default —
  this subsystem is held to the same standard, not exempted from it)
- `docs/protocols/websocket-protocol-v1.md` (the base protocol this
  subsystem's observer channel extends, not replaces)
- `docs/protocols/control-center-observer-protocol-v1.md` (this
  decision's wire-level contract — read together with this ADR)
- ADR-014 (Unified WebSocket Authentication — the auth model this
  subsystem is brought under, not given an exception from)
- ADR-010 (Evidence-Based Engineering — the standard this ADR's Context
  and Alternatives sections were held to: every claim above traces to
  a real incident, a real test failure, or a real code diff, not a
  hypothetical)

## Related Milestones

Milestone 9B.10 (release-candidate validation, where the observability
gap became concrete and costly enough to justify this subsystem);
initial implementation, independent architectural/security/performance
review, and hardening pass all completed the same milestone.

## Related Source Files

- `app/connection_manager.py` (`broadcast_observers()`, `mark_observer()`,
  `has_observers()`, `record_heartbeat()`, `get_stats()`)
- `app/main.py` (`GET /api/dashboard/snapshot`, `register_observer`/
  `heartbeat`/`ping` message handling, `/dashboard` static serving)
- `app/supervisor/supervisor.py` (`set_broadcast_hook()`, `_broadcast()`)
- `app/voice_session_manager.py` (`set_broadcast_hook()`,
  `_broadcast_lifecycle()`, `_schedule_lifecycle()`)
- `app/static/dashboard/` (`index.html`, `dashboard.css`, `dashboard.js`)
- `scripts/dashboard_demo_seed.py` (development/manual-verification tool,
  not a production code path)
- `tests/conftest.py` (autouse fixture resetting the module-global
  broadcast hooks between tests)
- `tests/test_dashboard_observer_isolation.py`,
  `tests/test_connection_manager.py`, `tests/test_supervisor.py`,
  `tests/test_voice_session_manager.py` (observer-isolation and
  cost-gating test coverage)
