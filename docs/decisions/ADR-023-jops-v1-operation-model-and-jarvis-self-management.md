# ADR-023: JOPS v1.0 — Operation Model, Jarvis Self-Management, and Connectivity Policy Split

## Status

Accepted

## Date

2026-07-24

## Context

ADR-022 established Jarvis Operations as a standalone, localhost-only
subsystem and implemented its first slice (OpenCode start/stop/restart),
explicitly deferring Jarvis's own lifecycle. JOPS v1.0 ("Do NOT revisit
the architecture... maintain the architectural boundaries established by
ADR-018, ADR-019, ADR-020, ADR-021, and ADR-022") extends that same
subsystem to cover Jarvis's own lifecycle, connectivity policy, an
operation history, and a fuller dashboard. This ADR does not reopen any
prior decision; it names the small number of genuinely new primitives
this extension requires, per the same discipline ADR-020 used when it
added the trace_id invariant before letting new subsystems build on it.

Three new primitives need a home before implementation:

1. **An operation identifier distinct from trace_id.** ADR-020's
   canonical definition: a trace is one logical unit of autonomous work
   initiated or coordinated by the Supervisor. An operator clicking
   "Restart Jarvis" is neither. JOPS v1.0 needs its own identifier
   (`operation_id`) for its own concern (operator-triggered actions,
   possibly spanning multiple service-state transitions), never trace_id,
   per the explicit standing invariant against inventing alternative
   correlation mechanisms elsewhere while also never overloading trace_id
   with meaning it was never designed to carry.

2. **A way to start/stop Jarvis's own process without Jarvis supervising
   itself.** Jarvis's own process cannot safely act on its own lifecycle
   — the process handling a "restart" request is the process being
   restarted. Unlike OpenCode (already owned in-process by
   `OpenCodeSupervisor`, reused via a proxy per ADR-022), there is no
   in-process object to proxy to for Jarvis itself: when Jarvis is down,
   there is nothing on the other end of a proxy call. This has to be
   handled directly by the Operations console process, using
   `process_utils.py`'s existing primitives, mirroring (not duplicating)
   `OpenCodeServerManager`'s classify-then-spawn-or-attach pattern.

3. **Where connectivity policy is defined vs. where it takes effect.**
   Per explicit direction: JOPS defines and exposes the connectivity
   policy (Always Connected / Wi-Fi Only / Manual); the Android Companion
   enforces it inside `PresenceService`. Investigation found no existing
   Android-side connectivity-mode concept and no existing settings-fetch
   call from the app at all — Android enforcement is real, unbuilt,
   unvalidatable-by-Playwright work, not a small addition to something
   already there. Per explicit direction, this milestone implements
   policy definition and persistence in JOPS; Android enforcement is
   deferred, not attempted partially and unvalidated.

## Decision

### Operation model

A new `operations` table (`app/database.py`), separate from `events` and
from anything trace_id-bearing:

```
operation_id   TEXT PRIMARY KEY   -- uuid4, minted once per operator action
target         TEXT               -- 'jarvis' | 'opencode' | 'connectivity_policy'
action         TEXT               -- 'start' | 'stop' | 'restart' | 'set_policy'
operator       TEXT               -- 'local' -- single-operator system; no invented RBAC
status         TEXT               -- QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
created_at, started_at, finished_at, duration_ms, error, detail (JSON)
```

This table is owned and written by the **Operations console**, not by
Jarvis's own app, and lives in the console's own local SQLite file, not
`jarvis.db`. Reason: a "Stop Jarvis" operation's own outcome must be
recorded even though the only process that could otherwise write it
(Jarvis) is, by the operation's own definition, going away mid-operation.
Making the console the single owner of operation history means every
operation type (Jarvis lifecycle, OpenCode lifecycle, policy changes) is
recorded the same way regardless of whether Jarvis is reachable at the
time, and the dashboard's "Operation History" always has something to
show even when Jarvis is down. OpenCode/connectivity operations remain
*additionally* visible in Jarvis's own structured log (ADR-021,
component `operations`) exactly as ADR-022 already built — this table
does not replace that, it is the durable, filterable record the log was
never meant to be queried as (ADR-022's Future Evolution named exactly
this as the trigger for promoting to a table).

An operation may span multiple service-state transitions (e.g. a future
compound "restart everything" would be one operation_id covering two
service transitions) — the schema does not assume a 1:1 relationship,
even though every JOPS v1.0 operation happens to be single-target.

### Jarvis self-management

A `JarvisProcessManager`, living in the Operations console process
(never in `app/main.py`), reusing the exact same
`claim_*()`/`run_claimed_*()` race-safe state-machine shape already
proven by `OpenCodeSupervisor` (ADR-022 Phase 1/3/4), and reusing
`process_utils.py`'s `find_listening_pid`/`get_process_identity`/
`kill_process_tree`/`wait_port_released` directly — the same primitives
`OpenCodeServerManager` already uses, extracted where genuinely shared
(owner-marker read/write/clear) rather than duplicated.

Startup classifies whatever is listening on Jarvis's port before acting,
mirroring `OpenCodeServerManager.classify_existing_server`: nothing
listening → spawn; something listening that answers like a real Jarvis →
attach, don't duplicate; something listening that doesn't → fail closed
with a clear diagnostic (port conflict), never silently kill an unknown
process. A stale owner marker from a prior console run is handled the
same way OpenCode's is: reconciled against what's actually listening, not
trusted blindly.

The target port, spawn working directory, and spawn environment are all
config-driven (derived from `JARVIS_OPERATIONS_TARGET_URL` plus explicit
overrides), never hard-coded to the production port — required so
validation can run against a throwaway instance without any risk of the
console's first real "Stop Jarvis" call landing on the user's actual
running backend.

### Connectivity policy: write vs. read

- **Write** (define the policy): a new localhost-gated Operations
  endpoint on Jarvis's own app (reachable only when Jarvis is up, exactly
  like OpenCode control), persisted via the existing
  `get_setting`/`set_setting` (`app/database.py`) — no new storage
  mechanism. This is configuration, not a destructive action: no
  `confirm=true` gate, unlike Stop/Restart.
- **Read** (the phone learns the policy): the existing, already-LAN-open
  `GET /api/settings` endpoint gains a `connectivity_mode` field. No new
  command channel to the phone is introduced — the phone will read this
  the same way it would read any other setting, once it has a reason to
  fetch settings at all (it currently does not; see below).
- **Observation** (the phone's current connection state): unchanged,
  reused from `ConnectionManager.get_latest_device_status()`/
  `get_stats()` — read-only, exactly as ADR-019's Future Evolution
  already sanctioned.

**Explicitly deferred:** Android enforcement (`PresenceService` reading
`connectivity_mode`, trusted-Wi-Fi detection, auto-disconnect/reconnect,
a Settings UI toggle). Not present today; real Kotlin/Android work;
not something Playwright can validate; exactly the class of code
(network-state-driven background service behavior) this project's own
history shows only fails in ways a real device under real OEM
battery/doze conditions reveals. Building it now, unvalidated, would
repeat a mistake this engagement has explicitly avoided elsewhere.
Building the JOPS-side half now and stopping there is the direction
given, not a shortfall against it.

## Consequences

### Positive

- `operation_id` gives every operator action a first-class identity
  without stretching trace_id past its canonical definition.
- Operation history survives the exact case that would otherwise break
  it (Jarvis's own process disappearing mid-operation).
- Jarvis's own lifecycle becomes controllable through the same
  console-owned, localhost-only, audited model already proven for
  OpenCode — no new security posture to design from scratch.
- The phone-facing half of connectivity policy is fully specified
  (what field, what endpoint, what storage) even though unbuilt —
  Android enforcement has an exact contract to implement against later.

### Trade-offs

- Two places now record "what happened to OpenCode" (Jarvis's structured
  log and the console's operations table) — an accepted, deliberate
  redundancy, not drift: one is the real-time diagnostic trace, the
  other is the durable filterable record.
- Connectivity policy can be set from the console today with no visible
  effect on the phone until Android enforcement ships — an honest,
  documented gap, not a silent one.

## References

- `docs/decisions/ADR-022-jarvis-operations-subsystem.md`
- `docs/decisions/ADR-020-trace-id-execution-correlation-model.md` (canonical trace definition this ADR deliberately does not stretch)
- `docs/decisions/ADR-019-separation-of-observability-and-operations.md` (sanctions Operations reusing Control Center's read primitives for status)
- `app/integrations/process_utils.py`, `app/integrations/opencode_server.py` (reused patterns)
- `android/app/src/main/java/com/jarvis/companion/service/PresenceService.kt` (where enforcement lands, later)

## Related Milestones

JOPS v1.0.

## Related Source Files

`app/operations_console.py`, `app/jarvis_process_manager.py` (new),
`app/operations_connectivity.py` (new), `app/database.py` (new
`operations` table + console-local history store), `app/main.py`
(`GET /api/settings` gains `connectivity_mode`).
