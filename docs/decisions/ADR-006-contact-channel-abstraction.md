# ADR-006: Contact Channel Abstraction

## Status

Accepted (implemented channels: push, voice; reserved but unimplemented:
native Android, phone call, SMS)

## Date

2026-07-10 (Milestone 8)

## Context

Milestone 7 gave Jarvis one way to reach a user: Web Push (plus in-app
delivery while a WebSocket is open). Milestone 8's attention lifecycle
work needed to reach users through more than one mechanism — a bound
voice session, and eventually, channels that don't exist yet (a native
Android notification, a phone call, SMS). Each of these has a completely
different delivery-certainty profile: push can only report "accepted by
the push service," never confirmed delivery; a phone call, if it
connects, is about as certain as contact gets. The design needed to
accommodate channels with fundamentally different guarantees without
letting the differences leak into the rest of the attention system.

## Problem

How should Jarvis represent "attempt to reach the user" in a way that
works uniformly across channels with very different delivery guarantees,
and that can accept new channels later without changing the attention
state machine itself?

## Decision

`ContactChannel` (`app/contact_channels.py`) is an abstraction with one
method, `attempt_contact()`, returning a result carrying the channel's
own honest assessment of what happened — never more delivery certainty
than the platform can actually provide. `AttentionManager` calls a
channel and persists the result into `contact_attempts`
(`create_contact_attempt`/`update_contact_attempt_status`) — the channel
implementation itself never writes to the database. Two channel names
are reserved but deliberately unimplemented — `NATIVE_ANDROID`,
`PHONE_CALL`, `SMS` — `get_channel()` raises `ValueError` for all three by
design. This is the explicit Milestone 8/9 boundary: a reserved name
signals "this channel is expected to exist eventually and the attention
system already knows its name," without pretending it currently works.

Channels are interchangeable from `AttentionManager`'s point of view —
`InterruptionPolicy` (ADR-003) decides *which* channel to try, and the
channel abstraction guarantees the rest of the pipeline (attempt
recording, notification creation, resolution) behaves identically
regardless of which one was chosen.

## Alternatives Considered

**Special-case each contact mechanism directly in `AttentionManager`.**
Rejected: this was the trajectory Milestone 7's single-channel (push-only)
design was already on, and it does not scale to N channels with
different guarantees without turning `AttentionManager` itself into a
large conditional on channel type — exactly the kind of ownership
ambiguity ADR-001/ADR-005 exist to prevent elsewhere in the system.

**Stub out `NATIVE_ANDROID`/`PHONE_CALL`/`SMS` with fake, always-succeeding
implementations "to unblock" other work.** Rejected, explicitly, in
`SESSION.md`'s own Architecture Decisions list (#26): an unimplemented
channel that clearly raises is strictly more honest than a stub that
silently reports fake success — the latter would violate ADR-010
(Evidence-Based Engineering) at the exact point where delivery-certainty
honesty matters most.

**Let each channel own its own delivery-result persistence.** Rejected:
would duplicate the `contact_attempts` write logic per channel and create
exactly the kind of ownership ambiguity `ARCHITECTURE.md` §5 explicitly
calls out as a real, disclosed gap when it *does* happen elsewhere
(`push_subscriptions`, written directly from a route handler rather than
through its logically-owning module) — this ADR's design deliberately
avoids repeating that mistake for `contact_attempts`.

## Consequences

Adding a real native-Android or phone-call channel later is expected to
mean implementing `ContactChannel`'s interface and registering the name
— not touching `AttentionManager`, `InterruptionPolicy`, or the
`contact_attempts` schema. This is the direct extensibility seam
`ARCHITECTURE.md` §15 names for "new contact channel."

## Positive Outcomes

- `PushChannel`'s honest delivery-certainty reporting
  (`"PUSH_ACCEPTED_BY_PUSH_SERVICE (delivery to device not confirmed)"`,
  never `"delivered"`) is a direct, disclosed consequence of this
  abstraction's own contract, not a special case bolted onto push
  specifically.
- A future voice-based contact attempt (a bound `VoiceSession`) already
  fits the same shape — `contact_attempts.voice_session_id` exists in the
  schema precisely because the abstraction was designed to accommodate a
  channel whose "attempt" is itself a stateful session, not a fire-and-
  forget send.
- The reserved-name pattern means the attention system's own code already
  anticipates future channels without needing schema or interface changes
  when they arrive.

## Tradeoffs

- The abstraction currently has real coverage of only one asynchronous,
  fire-and-forget channel (push) plus a session-based one (voice) — its
  suitability for a genuinely different shape of channel (e.g. SMS, which
  is asynchronous but has its own delivery-receipt semantics) is
  unverified until one is actually implemented.
- A caller cannot currently query "which channels are even available on
  this deployment" beyond attempting one and catching the `ValueError` for
  reserved names — a minor ergonomic gap, not a correctness one.

## Future Revisit Conditions

Revisit when the first of `NATIVE_ANDROID`, `PHONE_CALL`, or `SMS` is
actually implemented — at that point, confirm the existing
`attempt_contact()` interface genuinely accommodates the new channel's
real delivery-certainty semantics rather than forcing them into the
push/voice-shaped mold uncritically.

## References

- `ARCHITECTURE.md` §4 (Contact channels), §8 (Attention Architecture),
  §15 (Extensibility)
- `SESSION.md`, Milestone 8, Architecture Decisions #26–27, Known
  Limitation #38

## Related Milestones

Milestone 7 (push-only precursor), Milestone 8 (this abstraction)

## Related Source Files

- `app/contact_channels.py`
- `app/attention_manager.py`
- `app/push.py`
