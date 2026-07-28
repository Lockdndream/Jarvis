# ADR-022: Jarvis Operations Subsystem

## Status

Accepted

## Date

2026-07-23 (accepted 2026-07-24)

## Context

ADR-019 named, but deliberately did not build, a future write-capable
subsystem ("Jarvis Operations") as the correct home for Start/Stop/
Restart controls and other actions that change what Jarvis does, kept
permanently separate from the Control Center's read-only observability
contract (ADR-018). A subsequent request ("CCOU.1") asked for exactly
that capability — an Operations Panel with Start/Stop/Restart buttons
for Jarvis and OpenCode, plus Refresh Status — implemented inside the
Control Center. That request was not implemented as specified: it
re-opens the boundary ADR-019 closed, for the same structural reason
ADR-019 describes ("a dashboard that starts read-only naturally drifts
toward becoming an admin console, one convenient button at a time").
The request was redirected here, into the subsystem ADR-019 already
reserved for it, per explicit instruction: proceed with a new
subsystem, do not modify ADR-018 or ADR-019.

Investigation of the existing codebase found:

- OpenCode already has a real, safe, reusable lifecycle: `OpenCodeSupervisor.start()`/`stop()` (`app/integrations/opencode_supervisor.py`), which itself owns `OpenCodeServerManager.start()`/`stop()` (`app/integrations/opencode_server.py`) — process discovery, health verification, and graceful-then-forced shutdown (`process_utils.kill_process_tree(force=False)` escalating to `force=True` on timeout) all already exist and are exercised in production today.
- Jarvis has no equivalent for itself. Jarvis's own process cannot safely start, stop, or restart *itself* — the process handling the "restart" request is the process being restarted. This project's own history already identified this ("no watchdog architecture exists, a process should not supervise itself") and deferred it rather than fake it. This ADR must resolve it, not defer it again, because "Restart Jarvis" was explicitly requested.
- The only existing access-control primitive, `_require_api_token()` (`app/main.py`), is an optional shared-secret gate that is a **no-op when `JARVIS_API_TOKEN` is unset — today's default deployment**. It currently gates only read-only or write-light endpoints (push subscriptions, ws-token issuance, the dashboard snapshot). No destructive action exists behind it today, and it is not, by itself, a sufficient authorization model for one.
- Jarvis's main process (`app/main.py`) binds `0.0.0.0:8443` — reachable from the whole LAN by design (dashboard/phone access). Any write-capable endpoint added to that same process inherits that exposure unless deliberately narrowed.
- A real defect was found during this investigation, load-bearing for this ADR's Restart design: `OpenCodeSupervisor.stop()` sets `self._stopped = True`, but `OpenCodeSupervisor.start()` never resets it back to `False`. After a stop→start cycle, the newly created `_sse_loop()`/`_poll_loop()` tasks see `self._stopped` already `True` and exit immediately — the server reports healthy, but event processing is silently dead. This must be fixed as a prerequisite for "Restart OpenCode" to be honest, not treated as a new Operations feature.

## Problem

How can Jarvis provide real Start/Stop/Restart/Refresh-Status controls
for both itself and OpenCode, reusing existing service-management logic
without duplicating it, with a security and audit posture proportionate
to the fact that these are now destructive, consequential actions —
while leaving ADR-018 and ADR-019's read-only Control Center boundary
completely untouched?

## Decision

**Jarvis Operations is a new, standalone local subsystem — its own
long-running process, not a feature of `app/main.py` or the Control
Center.** It is the one and only home for actions that change what
Jarvis or OpenCode are doing. The Control Center remains exactly as
read-only as ADR-018/ADR-019 require; this ADR does not modify either.

### Responsibilities

Exactly the seven controls requested, nothing more:
Start Jarvis, Stop Jarvis, Restart Jarvis, Start OpenCode, Stop OpenCode,
Restart OpenCode, Refresh Status. No new monitoring surface is built —
status comes from Jarvis's existing observability primitives (see
Integration with the Control Center, below). No auto-recovery, no
crash-watchdog, no scheduled restarts: every action is manually
triggered by the operator. Building an always-on supervisory watchdog
is a materially different, higher-risk system than "let a human press a
button," and is explicitly out of scope.

### Architecture

Two different actions require two different mechanisms, because only
one of them can be done safely from inside Jarvis's own process:

**OpenCode controls (start/stop/restart OpenCode)** are implemented as
new endpoints on Jarvis's existing FastAPI app (`app/main.py`):
`POST /api/operations/opencode/start`, `POST /api/operations/opencode/stop`,
`POST /api/operations/opencode/restart`. OpenCode is already owned
in-process by a single `OpenCodeSupervisor` instance; a second, external
owner acting on it directly would create two uncoordinated managers
racing for the same process. These endpoints call
`opencode_supervisor.start()`/`stop()` directly — reusing, not
duplicating, the existing lifecycle code — and `restart` is `stop()`
followed by `start()` on that same instance.

**Jarvis controls (start/stop/restart Jarvis itself)** are implemented
in the Jarvis Operations process directly, using
`app/integrations/process_utils.py`'s existing Windows-safe helpers
(`find_listening_pid(8443)`, `get_process_identity()`,
`kill_process_tree()`, `wait_port_released()`) — the same primitives
OpenCode's own manager already uses, reused rather than reinvented.
Stop: locate the PID listening on 8443, request graceful shutdown,
escalate to forced termination on timeout, verify the port is released
— the same three-stage escalation `OpenCodeServerManager.stop()` already
performs. Start: spawn a new `uvicorn app.main:app --host 0.0.0.0
--port 8443 --ssl-keyfile ... --ssl-certfile ...` subprocess, mirroring
the existing launch invocation in `open_control_center.bat`, and poll
until the port is listening and `/api/dashboard/snapshot` responds.
Restart is Stop then Start, sequenced — not a single atomic operation
(see Failure Handling).

This is why Jarvis Operations must be its own process, running
independently of whether Jarvis itself is currently up: only a process
other than Jarvis can restart Jarvis. The operator starts Jarvis
Operations manually (a new launcher script, analogous to
`open_control_center.bat`); it is not started by Jarvis and does not
start automatically with Windows. If Jarvis Operations itself is not
running, none of the Jarvis-process controls are reachable — a real,
stated limitation, not a hidden gap.

### Security model

Jarvis Operations binds to **`127.0.0.1` only, never `0.0.0.0`.** This
is the primary security boundary: it is a local-operator console by
design, reachable only by someone with access to the machine itself,
never over the LAN — deliberately not inheriting the LAN-facing trust
model (`JARVIS_API_TOKEN` unset by default) the rest of the deployment
currently accepts (Known Limitation #1).

The new `/api/operations/opencode/*` endpoints, however, live on
`app/main.py`, which does bind `0.0.0.0`. Binding *Jarvis Operations*
to localhost does not, by itself, protect endpoints living on *Jarvis's
own* app. Those endpoints are therefore additionally gated by a new
dependency that only accepts requests from `127.0.0.1`
(`request.client.host == "127.0.0.1"`), independent of and in addition
to `_require_api_token()` (which remains a no-op unless
`JARVIS_API_TOKEN` is set). Without this, an unauthenticated LAN client
could otherwise reach a destructive action through a port that is
already, by default, open to the whole LAN.

### Authorization

No new user or role model is introduced. This is a single-operator,
single-machine system; the authorization boundary is physical/local
access to the machine, enforced by the localhost-only binding above.
Inventing RBAC for a system with one operator would be unneeded
complexity, not increased safety — stated honestly rather than
implemented speculatively.

### Confirmation flow

Start and Refresh Status are non-destructive and require no
confirmation. Stop and Restart (for both Jarvis and OpenCode) require
explicit confirmation at two layers: the UI requires an explicit
confirm step (a second click / confirm dialog) before sending the
request, and the underlying endpoint itself requires an explicit
`confirm=true` parameter — omitting it is a `400 Bad Request`, not a
silent no-op — so no script, retry, or accidental request can trigger a
destructive action without deliberately opting in each time.

### Audit logging

Every attempted action — not only successes — is recorded through the
existing structured-logging infrastructure (ADR-021): a new component,
`operations`, is added to the taxonomy. Destructive actions (Stop,
Restart) log at `WARNING`; Start and Refresh Status log at `INFO`. Each
record carries the action, target (`jarvis` or `opencode`), outcome
(`attempted` / `succeeded` / `failed`), and failure reason when
applicable. This is the durable audit trail; no new database table is
introduced, since nothing here currently needs to be queried beyond
what the structured log already supports.

These records are **deliberately not traces.** Per ADR-020's canonical
definition, a trace is one logical unit of autonomous work initiated or
coordinated by the Supervisor — an operator clicking "Stop OpenCode" is
neither autonomous nor Supervisor-coordinated. Per ADR-020's governing
invariant, this ADR does not invent an alternative correlation
mechanism to compensate: Operations audit records stand on their own
via timestamp and component, exactly as the invariant requires of any
subsystem that isn't itself producing traced work.

### Failure handling

Stop uses the same graceful-then-forced escalation
`OpenCodeServerManager.stop()` already performs for OpenCode, applied
via `process_utils` for Jarvis's own process: graceful signal, wait,
verify port released, escalate to forced termination on timeout,
verify again. Restart is Stop then Start as two sequenced steps, not
one atomic operation — if Stop succeeds but Start then fails, the
system is left **down**, and Jarvis Operations must report exactly
that ("stopped; restart failed to bring it back up") rather than
reporting "restarted." The UI's failure state must visually distinguish
this case from a clean restart, since it is a materially worse outcome
the operator needs to notice immediately.

As a direct prerequisite for "Restart OpenCode" to behave honestly
(found during this ADR's investigation, not a new feature): fix
`OpenCodeSupervisor.start()` to reset `self._stopped = False`, matching
the pattern `OpenCodeServerManager.start()` already correctly follows.
Without this fix, a restarted OpenCode server reports healthy while its
event-processing loops are silently dead.

### Integration with the Control Center

Unchanged from what ADR-019's own Future Evolution section already
specifies, and not modified by this ADR: Jarvis Operations reuses the
Control Center's existing observability primitives —
`ConnectionManager.has_observers()`/`broadcast_observers()`,
`Supervisor.get_supervisor_state()`, `OpenCodeServerManager.check_health()`
— as its own status-checking foundation for "Refresh Status," rather
than re-deriving them. No Control Center source file is modified by
this milestone. The Control Center does not gain any link to or
awareness of Jarvis Operations in this milestone; ADR-019's own Future
Evolution section already describes that possible future UX
(a Control Center panel surfacing the *option* to act, never performing
the action) as a later, separate step, not required here.

## Alternatives Considered

**Give Jarvis Operations its own database table for audit records.**
Rejected for now: the structured-logging record already answers every
question this milestone needs answered (what happened, when, outcome).
A dedicated table is deferred until a real query need appears, per
YAGNI — matching this project's established discipline of not building
speculative infrastructure ahead of demonstrated need.

**Have Jarvis Operations control OpenCode directly via `process_utils`,
the same way it controls Jarvis.** Rejected: OpenCode already has a
single in-process owner (`OpenCodeSupervisor`) with health-loop and
completion-event state tied to its own lifecycle. A second external
owner acting on the same process without that supervisor's knowledge
would desynchronize its internal state (e.g., its `_stopped` flag,
active SSE/poll tasks) from reality. Proxying through Jarvis's own
supervisor, which already owns that state, avoids creating a second,
uncoordinated owner.

**Gate the new `/api/operations/opencode/*` endpoints with
`_require_api_token()` instead of a localhost check.** Rejected as
insufficient on its own: that gate is a no-op by default (today's
deployment has no `JARVIS_API_TOKEN` set), which would leave a
destructive action reachable by anything on the LAN out of the box.
The localhost check fails closed regardless of configuration; the
token gate remains available as an *additional* layer for operators
who do set one.

**Auto-start Jarvis Operations with Windows / run it as a background
watchdog.** Rejected: not requested, and it would quietly reintroduce
the self-supervision problem this ADR otherwise avoids by requiring a
human to deliberately start Jarvis Operations. Kept manual and
explicit.

## Consequences

### Positive

- Start/Stop/Restart controls for both Jarvis and OpenCode become real
  and safe to use, without touching ADR-018 or ADR-019's read-only
  boundary at all.
- Existing service-management logic (`process_utils.py`,
  `OpenCodeSupervisor`, `OpenCodeServerManager`) is reused directly,
  with zero duplicated process-management code.
- A real latent defect (`OpenCodeSupervisor.start()` not resetting
  `_stopped`) is fixed as a byproduct of designing Restart honestly,
  rather than being discovered later by a confused operator watching a
  "healthy" OpenCode server silently stop processing events.
- The security model is proportionate and simple: local-machine-only
  access, no speculative RBAC, fail-closed by default regardless of
  the rest of the deployment's LAN-facing trust posture.

### Trade-offs

- The operator must remember to start Jarvis Operations separately
  before "Start Jarvis" is available at all — an accepted, explicit
  limitation, not hidden behind a false promise of always-on control.
- Two processes to run (Jarvis, and now Jarvis Operations) instead of
  one, when the operator wants full control available. This is the
  direct, necessary cost of not building self-supervision.
- Jarvis Operations is a second codebase surface with its own (small)
  security review burden, exactly as ADR-019 anticipated when it said
  a write-capable subsystem "deserves its own dedicated design pass."

## Future Evolution

If a genuine need for queryable operational history emerges (e.g., "how
often has this been restarted this month"), promote the structured-log
audit trail into a small dedicated table at that point — not before.
If remote (off-machine) operational control is ever genuinely required,
that is a materially larger security problem (real authentication,
real authorization, real transport security) deserving its own ADR;
this ADR's localhost-only model should not be quietly loosened to
accommodate it.

## References

- `docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md` (the read-only contract this ADR does not modify)
- `docs/decisions/ADR-019-separation-of-observability-and-operations.md` (names Jarvis Operations as this subsystem's founding mandate; its Future Evolution section is the basis for this ADR's Control Center integration section)
- `docs/decisions/ADR-020-trace-id-execution-correlation-model.md` (canonical trace definition and governing invariant, both referenced by this ADR's Audit Logging section)
- `docs/decisions/ADR-021-structured-logging-architecture.md` (the logging infrastructure Operations audit records reuse via the new `operations` component)
- `app/integrations/process_utils.py` (reused Windows-safe process-management primitives)
- `app/integrations/opencode_supervisor.py`, `app/integrations/opencode_server.py` (reused OpenCode lifecycle, and the location of the `_stopped` defect this ADR requires fixing)
- `open_control_center.bat` (existing precedent for the Jarvis launch invocation Start Jarvis reuses)

## Related Milestones

This ADR's implementation is the first Jarvis Operations milestone
(the redirected scope of the original "CCOU.1" request, rebuilt as its
own subsystem per explicit instruction rather than inside the Control
Center).

## Related Source Files

None yet — this ADR defines the subsystem prior to implementation. The
implementation will add a new standalone Operations process/UI, new
`/api/operations/opencode/*` endpoints on `app/main.py`, the
`OpenCodeSupervisor.start()` fix, and a new `operations` structured-log
component.
