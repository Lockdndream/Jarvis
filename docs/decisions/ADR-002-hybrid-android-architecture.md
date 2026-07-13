# ADR-002: Hybrid Android Architecture

## Status

Accepted (architecture only — production implementation, Milestone 9B,
has not started as of this writing)

## Date

2026-07-10 (Milestone 9A decision gate)

## Context

By Milestone 8.1, the browser PWA was a mature, real-device-validated
client: voice input/output, a call-style attention UI, deep links,
notifications, and reconnect-safe conversation continuity all worked on
a real Samsung Galaxy S20 FE. The question Milestone 9A was chartered to
answer: is the PWA *sufficient* as Jarvis's only phone surface, or is a
native Android companion actually justified — and if so, for what,
specifically?

## Problem

Should Jarvis replace the PWA with a native app, extend the PWA only, add
a native companion alongside the PWA, or conclude no native work is
justified at all? The decision needed to be evidence-based, not a default
assumption that "native is better."

## Decision

**Hybrid: keep the PWA as the rich conversational client, add a thin
native Android companion justified only for capabilities categorically
unavailable to a browser on Android** — not capabilities that are merely
less reliable in a browser. Milestone 9A identified exactly four such
capabilities:

1. A home-screen widget (no PWA API provides this at all, independent of
   reliability).
2. Real audio-focus/ducking control (`AudioFocusRequest` is
   Android-native-only; no PWA equivalent exists).
3. A wake word that survives backgrounding (Android suspends backgrounded
   JS; the Web Speech API is cloud-backed even in the foreground, so even
   an always-on browser tab could not do this locally).
4. Presence that does not depend on a browser tab's execution context
   staying alive (real-device-tested: backgrounding the PWA triggers a
   full page reload on Android, discarding all client-side state).

The native companion's scope is bounded to exactly these four
capabilities plus the transport needed to support them — see
`ARCHITECTURE.md` §9 for the proposed module boundary. It is not a
general-purpose native rewrite of the PWA's functionality.

## Alternatives Considered

**Full native rewrite, retire the PWA.** Rejected. The PWA already does
almost everything well (voice, TTS, notifications, deep links,
reconnect), and native app distribution (build/sign/install friction,
no-app-store-in-scope) is a real cost with no matching benefit for
functionality the PWA already provides correctly. This was explicitly
discussed and rejected during Milestone 7.1 as disproportionate to the
actual gap (background push reliability) that prompted the question.

**PWA-only, invest further in browser capabilities (e.g. a Trusted Web
Activity wrapper) instead of native.** Considered as a targeted spike
candidate for the background-push gap specifically (`SESSION.md`, "Next
Planned Work," item 1) but rejected as the *general* answer, because a
TWA wrapper still cannot provide a real home-screen widget or real
`AudioFocusRequest` access — the four capabilities above are not browser
engineering gaps, they are platform-level exclusions.

**Native companion that fully replaces the PWA's conversational role.**
Rejected. This would mean re-implementing already-proven, real-device-
validated functionality (voice, TTS, notifications, timeline UI) for no
architectural gain, and would multiply the surface area that must stay
in sync with the laptop's protocol (`ARCHITECTURE.md` §6).

**Do nothing — accept the PWA's limitations as final.** Rejected: the
four capabilities above are not minor UX gaps. A wake word and a
home-screen widget are core to the "always-reachable assistant"
experience Jarvis's vision describes (`ARCHITECTURE.md` §1); concluding
"not worth it" would have meant abandoning that part of the vision
outright, which was not the milestone's finding once the evidence was in.

## Consequences

The native companion is scoped narrowly by design — this ADR is the
reason a future engineer should push back on any companion feature
proposal that isn't one of the four justifying capabilities (or the
transport/pairing/attention-relay work needed to support them). See
ADR-001 for the reasoning-boundary constraint that applies equally to
this companion.

## Positive Outcomes

- The decision is falsifiable and was actually tested, not assumed: real
  Test A (PWA backgrounding causes a full page reload — platform
  behavior, not a Jarvis bug) and Test B (screen-off survives, with real
  OS-level mic cycling) produced the evidence base, not intuition.
- Scope discipline: Milestone 9B.0's own disposable spike (`spikes/android-presence/`)
  stayed small (528 lines) specifically because this ADR bounds what the
  companion is *for*.
- The backend requires zero reasoning changes to support this (Milestone
  9A's D2 code review, independently verified) — the hybrid model was
  chosen partly *because* it required no architecture change beyond
  scoped additions.

## Tradeoffs

- Two client codebases (PWA + native) must both speak the same WebSocket
  protocol (`ARCHITECTURE.md` §6) and stay consistent with it — a real,
  ongoing maintenance cost accepted in exchange for capability coverage
  neither one alone provides.
- A native Android build/signing/distribution pipeline is now a real
  future requirement (out of scope for the M9B.0 spike, which sideloads a
  debug APK via `adb`) — not free, deferred to Milestone 9B.
- Users on iOS get none of the four native-only capabilities; this ADR
  does not address iOS at all (see `SESSION.md` Known Limitation #30 —
  iOS Safari behavior remains unverified).

## Future Revisit Conditions

Revisit if: (a) Android platform changes remove one of the four
categorical exclusions (unlikely in the near term — these are deep
platform/browser-sandbox distinctions, not temporary API gaps), (b) the
native companion's real-world maintenance cost turns out to exceed its
value once built, or (c) a cross-platform framework emerges that could
provide all four capabilities without a fully separate native codebase
(not evaluated as of this ADR).

## References

- `ARCHITECTURE.md` §9 (Android Companion), §10 (Browser PWA)
- `SESSION.md`, Milestone 9A ("Decision gate" section, capability matrix
  — full detail in the published M9A artifact), Milestone 7.1 (native
  rewrite considered and rejected for a narrower reason)

## Related Milestones

Milestone 7 (PWA capabilities established), Milestone 7.1 (real-device
PWA validation, native-rewrite question first raised and rejected),
Milestone 9A (this decision), Milestone 9B.0 (first real evidence from
the disposable spike)

## Related Source Files

- `spikes/android-presence/` (disposable spike only — not production)
- `app/static/app.js`, `app/static/manifest.json` (PWA client)
- `app/main.py` (`/ws` endpoint both clients will share)
