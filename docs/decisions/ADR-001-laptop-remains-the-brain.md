# ADR-001: Laptop Remains the Brain

## Status

Accepted

## Date

2026-07-10 (formalized retroactively; the principle has held since
Milestone 1 and was made explicit during Milestone 9A's investigation)

## Context

Jarvis started as a single laptop-resident process reachable from a
phone browser (Milestone 1). As the project grew — voice (Milestone 7),
attention/interruption handling (Milestone 8), and finally a real
investigation into a native Android companion (Milestone 9A) — the
question of *where reasoning lives* became unavoidable. A native Android
app has real capabilities a browser tab does not: it can run code while
backgrounded, hold a foreground service, detect a wake word locally, and
control audio focus. Each of those capabilities is also a place where it
would be technically possible to put decision-making logic — matching a
spoken answer to a pending question, deciding whether to re-contact the
user, deciding what a deferred reminder means. Milestone 9A's own D2
review confirmed the backend *could* support a second client attaching
without any reasoning change — which made it necessary to decide, in
writing, whether it *should* be allowed to reason at all.

## Problem

As Jarvis gains more capable client surfaces (native Android, and
eventually an ESP32 presence device), what stops those clients from
gradually accumulating their own decision-making logic — and why does
that matter?

## Decision

All reasoning, all state machines, and all persistence live on the
laptop, in one process family, backed by one SQLite database
(`app/database.py`). Every other device Jarvis can be reached through —
the browser PWA, a future native Android companion, a future ESP32
presence device — is architecturally a *client*: it sends input (text,
voice transcript, a button press) and receives state (timeline events,
attention prompts, TTS audio). No client is permitted to interpret what
an answer means, decide when to re-contact a user, or hold its own copy
of conversational or task state beyond what it needs to render the
current turn.

Concretely, this means:
- **Android is not another supervisor.** A native companion's proposed
  module boundary (`presence/`, `transport/`, `audio/`, `wakeword/`,
  `attention/`, `widget/`, `pairing/`) explicitly excludes any module
  that would contain Supervisor logic (Milestone 9A).
- **ESP is not another supervisor.** The long-term presence-device vision
  (see `ARCHITECTURE.md` §11) is scoped even more narrowly than the
  Android companion — wake word, button, LED, and audio relay only.
- **The browser PWA is not exempt from this rule either** — it happens to
  be the richest client today, but `VoiceSessionManager`'s thin
  pass-through design (ADR-007) applies to it exactly as much as it would
  to any future client.

## Alternatives Considered

**Let each client own its own local reasoning for responsiveness.** A
native Android app could, for example, locally interpret "yes"/"no"
answers to avoid a round-trip to the laptop. Rejected: this is exactly
the failure mode this ADR exists to prevent — a second, uncoordinated
copy of "what does this answer mean" logic that will drift from the
laptop's own `Supervisor`/`deferral.py` logic over time, with no way to
guarantee the two stay consistent.

**Move reasoning to a cloud service reachable from any client equally.**
Rejected outright, not seriously considered: violates the project's
local-first vision (`ARCHITECTURE.md` §1) and introduces exactly the kind
of always-on third-party dependency the project has consistently avoided
(no public relay has ever been used or proposed for production, per every
milestone's stop-condition checks).

**Split reasoning between laptop and phone by responsibility (e.g. laptop
handles tasks, phone handles conversation).** Rejected: this was
considered implicitly during Milestone 9A's capability-matrix work and
found to have no natural seam — conversational state, deferral parsing,
and task state are too interdependent to split without duplicating
context on both sides.

## Consequences

Every new client surface is required to conform to the same constraint,
regardless of what capabilities the underlying platform offers. This has
already shaped the Android companion's proposed module boundary (ADR-002)
and `VoiceSessionManager`'s design (ADR-007), and will shape the future
ESP32 device's design (ADR-008) the same way.

## Positive Outcomes

- Exactly one place to fix a reasoning bug, not N places that could each
  have drifted differently.
- A new client can be added without re-deriving or re-testing
  conversational logic — Milestone 9A's D2 review confirmed the existing
  backend supports a second client attaching with zero reasoning changes.
- State survives any single client's disconnection, crash, or
  reinstall, because no client is the source of truth for anything beyond
  its own current UI.

## Tradeoffs

- Every client interaction requires a round-trip to the laptop, even for
  things a client could technically answer locally (e.g. "is this a
  yes/no answer") — a deliberate latency cost accepted in exchange for
  consistency.
- A client cannot function at all if it cannot reach the laptop — there
  is no offline degraded mode for conversational behavior. (A future
  native companion's foreground-service presence work is about
  *maintaining the connection*, not about functioning without it.)
- This constrains how "smart" a wake-word or presence device can appear
  to be on its own — by design, it cannot appear smart at all outside of
  relaying to the laptop.

## Future Revisit Conditions

This decision should be revisited only if a client scenario emerges
where the laptop is reliably unreachable for extended periods but a
client must still make user-facing decisions (e.g. true offline voice
control) — and even then, the correct response is likely a narrowly
scoped exception (e.g. "cache the last N minutes of context locally for
graceful degradation") rather than reversing the principle. No such
scenario has arisen as of Milestone 9B.0.

## References

- `ARCHITECTURE.md` §1 (Vision), §2 (Design Principles), §9 (Android
  Companion)
- `SESSION.md`, Milestone 9A ("hybrid recommended" decision gate, D2
  review)

## Related Milestones

Milestone 1 (implicit from the start), Milestone 8 (VoiceSessionManager
introduced), Milestone 9A (made explicit and stress-tested against a real
native-client proposal)

## Related Source Files

- `app/supervisor/supervisor.py`
- `app/voice_session_manager.py`
- `app/database.py`
