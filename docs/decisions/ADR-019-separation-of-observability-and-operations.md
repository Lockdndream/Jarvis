# ADR-019: Separation of Observability and Operations

## Status

Accepted

## Date

2026-07-22

## Context

While implementing a small set of requested operational improvements to
the Control Center (Task 2 of the post-hackathon Product Mode pass:
"expose common operational actions as buttons"), a request surfaced to
add Start/Stop/Restart controls for both Jarvis's backend process and
the OpenCode server directly into the Control Center. Investigation
found that OpenCode's lifecycle methods (`OpenCodeServerManager.start()`/
`stop()`) already exist and are safe to call, and that nothing prevents
wiring them to buttons in the same UI that ADR-018 established as
read-only.

That is exactly the danger this ADR exists to name: **a dashboard that
starts read-only naturally drifts toward becoming an admin console**,
one convenient button at a time, because each individual addition looks
small and each one reuses code that "already exists and is safe." The
Control Center could have grown a Start/Stop/Restart panel, then a
"resolve this attention item" button, then a "cancel this task" button
— each locally reasonable, none individually alarming, and the
cumulative result is a system that no longer has a boundary at all: a
dashboard someone can accidentally (or, eventually, maliciously, once
this is a daily-use system with more than one operator) use to change
what Jarvis does, not just see what it's doing. The pressure is
structural, not a one-off lapse — every future "just one more button"
request will face the identical temptation, because the reasoning
("it's just reusing existing safe code") is genuinely true every single
time, right up until the aggregate crosses from observation into
control.

## Problem

How does Jarvis prevent this drift permanently, rather than relying on
catching each individual request as it comes up?

## Decision

**The Control Center is permanently read-only. It observes, explains,
diagnoses, and reports — it never modifies system state, full stop.**
A future write-capable subsystem, **Jarvis Operations**, is named now
(not built now) as the deliberate, separate home for anything that
changes what Jarvis does.

Responsibilities are split by a single test, restated from ADR-018 and
made permanent here: **"Is this observing the system, or changing the
system?"** If it changes the system, it belongs in Jarvis Operations,
never in the Control Center, regardless of how small the change or how
safe the underlying code already is.

| Control Center | Jarvis Operations (future) |
|---|---|
| Observe | Restart |
| Explain | Stop |
| Diagnose | Start |
| Report | Recover |
| Surface attention | Maintain |
| — | Execute administrative actions |

This is not a new invention — it formalizes and names what ADR-018
already established (Contract 2: "Dashboard clients never affect
phone/PWA behavior... read-only") and what the operational-improvements
pass just independently re-derived under real pressure to violate it
(see `SESSION.md`, the "Post-hackathon" Task 2 resolution). ADR-018
protected the boundary implicitly, for one specific case (the phone).
This ADR makes the boundary explicit, general, and permanent, for every
future case.

## Security rationale

A read-only system and a write-capable system have fundamentally
different threat models. A read-only Control Center's worst failure
mode is showing wrong or stale information — bad, but self-contained
and recoverable by definition (nothing it does can be undone because it
never does anything). A write-capable Operations subsystem's worst
failure mode is taking a real, consequential action incorrectly,
without authorization, or without a way to know it happened — an
entirely different category of risk requiring its own authentication
model, authorization model, audit log, and failure-recovery strategy
(all explicitly out of scope for this ADR — see the Jarvis Operations
proposal this ADR's acceptance unblocks). Merging the two into one
subsystem means either the read-only parts inherit write-capable
security requirements they don't need (friction for no benefit), or —
far more likely in practice — the write-capable parts inherit the
read-only parts' lighter security posture (a real vulnerability). Kept
separate, each subsystem's security model is exactly as strict as its
actual risk requires, and neither can accidentally borrow the other's
posture.

## Operational rationale

Separation also improves ordinary maintainability, independent of
security. A contributor (human or AI) reading Control Center code never
has to ask "could this button have side effects elsewhere?" — the
answer is categorically no, which makes the whole subsystem easier to
reason about, easier to test (no destructive-action test fixtures,
no "did this actually restart something" verification burden), and
safer to leave running unattended, exactly as a dashboard meant to be
open all the time should be. Jarvis Operations, when it exists, can
adopt a stricter review bar (every change is reviewed as "could this
take an unauthorized or irreversible action") without that bar slowing
down ordinary Control Center improvements, which don't need it.

## Alternatives Considered

**Add a permissions/role layer inside the Control Center** (e.g., an
"advanced mode" toggle that unlocks write actions in the same UI).
Rejected: this keeps the write capability living inside a subsystem
whose entire external contract (ADR-018, this codebase's tests, every
contributor's mental model) says "read-only" — a toggle is a much
weaker guarantee than a subsystem boundary, and exactly the kind of
thing that erodes further under the next "just for convenience"
request.

**Build Jarvis Operations now, alongside this ADR.** Rejected per
explicit direction (Product Mode: recommend, don't implement) and on
the merits — a write-capable subsystem's authentication/authorization/
audit/recovery model deserves its own dedicated design pass, not one
folded into an ADR whose job is to draw the boundary, not build across
it.

**Do nothing — trust future judgment to catch drift case-by-case.**
Rejected: this ADR's own Context section is direct evidence that
"trust future judgment" already nearly failed once, on the very first
real request to test the boundary. A documented, explicit, permanent
rule is strictly better than relying on catching every future instance
of the same reasonable-sounding argument.

## Consequences

### Positive

- The Control Center's read-only guarantee is now a named, documented,
  permanent architectural rule, not an implicit property of ADR-018
  that has to be re-derived from first principles every time it's
  tested.
- Every future "should this go in the Control Center" question has a
  one-line test, stated once, applicable to any future proposal without
  needing a new ADR each time.
- Jarvis Operations has a clear founding mandate before a single line
  of it is written, reducing the risk of it either duplicating Control
  Center functionality or inheriting an unclear security posture by
  default.

### Trade-offs

- Genuinely convenient, low-risk operational conveniences (this ADR's
  own motivating example: OpenCode start/stop, which already has safe,
  existing code behind it) are deliberately not available until Jarvis
  Operations exists — a real, accepted cost, not an oversight.
- Two subsystems instead of one means two things to maintain, two
  places to look, and (once Operations exists) a real integration
  question of how a human moves between "I see something wrong" (Control
  Center) and "I want to fix it" (Operations) without friction. Future
  Evolution addresses this directly.

## Future Evolution

**Jarvis Operations builds on the Control Center; it does not replace
or duplicate it.** Concretely: Operations reuses the Control Center's
existing observability primitives (`ConnectionManager.has_observers()`/
`broadcast_observers()`, the `set_broadcast_hook()` pattern,
`OpenCodeServerManager.check_health()`, `Supervisor.get_supervisor_state()`)
as its own status-checking foundation rather than re-deriving them —
Operations needs to *know* system state before *acting* on it, and the
Control Center already answers that question correctly. The intended
UX evolution once Operations exists: a Control Center panel showing
something wrong (e.g., "OpenCode unavailable") links to or surfaces the
*option* to take the corresponding Operations action, without the
Control Center itself performing that action — the link between
"observe" and "act" is a UI convenience at the boundary, never a
blurring of which subsystem owns which responsibility.

## References

- `docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`
  (the read-only decision this ADR formalizes and makes permanent)
- `docs/protocols/control-center-observer-protocol-v1.md` (Contract 2:
  "Dashboard clients never affect phone/PWA behavior")
- `docs/observability-strategy.md` (the governing "how does the Control
  Center know this happened" question this ADR complements with "and
  who is allowed to act on it")
- `docs/control-center-roadmap.md` §Version 2.0 ("Remote controls...
  explicitly the largest departure from this subsystem's founding
  decision... would require its own ADR" — this is that ADR, written
  proactively rather than at the point someone tries to build it)
- `SESSION.md`, Milestone 9B.10, "Post-hackathon Product Mode" (the real
  Task 2/3/4 requests this ADR's Context is drawn from)

## Related Milestones

Milestone 9B.10 (Control Center implementation, review, hardening, and
merge; the operational-improvements pass that motivated this ADR).

## Related Source Files

None — this ADR is a governance document constraining future source
files, not describing an existing implementation. The Jarvis Operations
proposal (separate document) will name its own source files once
scoped.
