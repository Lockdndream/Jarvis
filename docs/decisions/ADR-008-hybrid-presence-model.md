# ADR-008: Hybrid Presence Model

## Status

Accepted for Browser + Android (Android at spike/Experimental maturity);
Experimental / vision-level for ESP — see Status notes per surface below

## Date

2026-07-10 (Milestone 9A, browser+Android), vision extended in
`ARCHITECTURE.md` §11 (ESP, undated — no implementation exists)

## Context

ADR-001 establishes that the laptop is the only brain. ADR-002 explains
why Android earns a native companion. This ADR names the broader pattern
those two decisions are both instances of, because a third surface — a
future ESP32 presence device (`ARCHITECTURE.md` §11) — is already
anticipated, and the pattern needs to be stated once, generally, rather
than re-derived for every new surface.

## Problem

As Jarvis gains more physical/software surfaces a user can reach it
through, what is the *general* rule for what a new surface is allowed to
be, independent of its specific capabilities?

## Decision

**Browser, Android, and (eventually) ESP are all clients. None of them
owns reasoning.** This is ADR-001 restated as a standing rule for *any*
future surface, not just the two that exist or are spiked today. A
surface earns the right to exist by providing capabilities the existing
surfaces categorically cannot (ADR-002's reasoning, generalized) — never
by offering to "also handle conversation" or "also decide things," which
remains exclusively the laptop's job regardless of how capable the
surface's underlying hardware or platform is.

Each surface's specific scope, as currently understood:
- **Browser PWA**: rich conversational client — voice, TTS, timeline,
  notifications, deep links. Real, implemented, real-device-validated.
- **Android companion**: presence, wake word, native audio focus/ducking,
  a home-screen widget — the four capabilities ADR-002 names. Spiked
  (Milestone 9B.0, disposable), not production.
- **ESP presence device**: wake word, button, LED, speaker, microphone,
  presence — an even narrower surface than Android, explicitly excluding
  any UI beyond state feedback. Vision-level only, no implementation, no
  hardware selection made (`ARCHITECTURE.md` §11, marked `FUTURE DESIGN`).

## Alternatives Considered

**Treat each surface as a fully independent design problem, with no
shared rule.** Rejected: this is what would allow surface-specific
"just this once" reasoning exceptions to accumulate — exactly the
gradual-drift failure mode ADR-001 was written to prevent for Android
specifically. Naming the general pattern here is intended to make that
drift visible immediately for any *future* surface, including ones not
yet conceived of.

**Rank surfaces by capability and let the most capable one take on more
responsibility (e.g. "Android is powerful enough to also do some
reasoning").** Rejected: capability is precisely not the criterion — the
laptop is the brain regardless of how capable a client's own hardware is.
An ESP32 with almost no compute and a flagship Android phone are treated
identically under this rule: both are clients, neither reasons.

**Design ESP integration now, in detail, since the pattern is already
anticipated.** Rejected as premature: `ARCHITECTURE.md` §11 explicitly
marks ESP's open questions (audio routing priority against the phone,
Bluetooth interaction model, hardware/wake-word selection) as
`FUTURE DESIGN` — inventing detail now, before any real hardware
evidence, would risk the same "assumed default is unsafe until proven
otherwise" mistake ADR-004's credential-isolation lesson warns against
applied to a different domain.

## Consequences

Any future presence-surface proposal (ESP or otherwise) should be
evaluated first against ADR-002's justification test ("what does this
provide that no existing surface categorically can") before any
implementation detail is discussed — this ADR is the standing reference
for that test, so ADR-002 does not need to be re-litigated per surface.

## Positive Outcomes

- A consistent mental model across the whole system: "is this a client
  or the brain" has exactly one correct answer for every surface, present
  or future.
- The Android companion's own module boundary (ADR-002) can be reused
  nearly verbatim as a template for a future ESP device's module
  boundary, because both are instances of the same underlying pattern.

## Tradeoffs

- This ADR is, by its own nature, more abstract than the others — it
  states a rule rather than resolving a specific technical question, and
  its practical value depends entirely on it actually being applied
  consistently when the next surface is proposed.
- Naming ESP explicitly here, before any implementation, risks reading as
  a commitment to build it — it is not; `ARCHITECTURE.md` §18's roadmap
  places it after the Android companion proves out the presence/
  transport/attention model on a second real device, with no scheduled
  date.

## Future Revisit Conditions

Revisit when ESP moves from vision to an actual proposed spike (the same
gate Android went through in Milestone 9A) — at that point, this ADR
should be checked against whatever real hardware/platform evidence
emerges, the same way ADR-002 was checked against Milestone 9A's real
capability matrix rather than assumed in advance.

## References

- `ARCHITECTURE.md` §1 (Vision), §3 (High-Level Architecture diagram),
  §9 (Android Companion), §11 (ESP Presence Device)
- ADR-001, ADR-002

## Related Milestones

Milestone 9A (Android instance of this pattern established), no ESP
milestone yet exists

## Related Source Files

No ESP-related source exists. Android: `spikes/android-presence/`
(disposable spike only).
