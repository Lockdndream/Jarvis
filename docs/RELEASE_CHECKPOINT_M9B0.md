# Release Checkpoint: Milestone 9B.0

**Milestone**: 9B.0 — Native Companion Technical Risk Spike
**Status**: Closed
**Date**: 2026-07-12
**Checkpoint type**: Architecture freeze validation, pre-M9B.1

---

## Architecture Frozen

Jarvis's architecture is now considered frozen at Version 1, documented
across four authoritative sources:

- `SESSION.md` — chronological record of what happened, milestone by
  milestone, since Milestone 1.
- `ARCHITECTURE.md` — synthesized specification of what Jarvis is and why
  it is built this way, kept stable across implementation churn.
- `docs/decisions/` — 10 Architecture Decision Records capturing the
  reasoning behind the load-bearing decisions, with genuine alternatives
  and honest tradeoffs.
- `README.md` — how to run it.

This checkpoint's own Architecture Freeze Validation (see below) found
the four documents substantively consistent with each other and with the
actual repository, with a small number of disclosed gaps — none of them
contradictions of substance, all now tracked in `docs/TECHNICAL_DEBT.md`.

## ADR System Complete

10 ADRs, `docs/decisions/README.md` establishing the system's lifecycle,
numbering, and review process:

| ADR | Title |
|---|---|
| 001 | Laptop Remains the Brain |
| 002 | Hybrid Android Architecture |
| 003 | Deterministic Attention Architecture |
| 004 | OpenCode Runtime Isolation |
| 005 | Worker / Supervisor Architecture |
| 006 | Contact Channel Abstraction |
| 007 | VoiceSession Ownership |
| 008 | Hybrid Presence Model |
| 009 | Cost Policy |
| 010 | Evidence-Based Engineering |

All 10 are `Accepted` status. Each was independently spot-checked against
the actual code (module names, function names, ownership claims) during
both its own writing and this checkpoint's review pass.

## Test Suite

**389/389 passing** (`pytest tests/`, 843.25s), 0 failed, 0 skipped, 2
known warnings (pre-existing Windows asyncio teardown noise, unrelated to
this milestone). One environmental concurrency-test flake was observed
under heavy sustained system load late in the session
(`test_attention_concurrency.py`, real-thread-race tests) and confirmed,
by isolated re-run, to be load-induced rather than a regression — none of
this milestone's changes touch the module under test
(`attention_manager.py`).

Android side (gated, not part of `pytest`): `ConnectionGenerationTrackerTest`
4/4 passing; `./gradlew assembleDebug` succeeds.

Desktop OpenCode shared storage confirmed byte-identical at every
checkpoint throughout the milestone (hash-verified before/after every
real OpenCode-touching test run).

## Real Phone Validation

Device: Samsung Galaxy **SM-G781B** (Galaxy S20 FE 5G), **Android 13**,
**API 33**, security patch 2025-10-01 — confirmed directly via `adb`,
matching the Milestone 9A device-name correction.

Real, physical-device evidence gathered this milestone:
- The disposable Android presence spike (`spikes/android-presence/`)
  built, installed, and ran on this device.
- A real WSS connection to the real Jarvis server, over the real,
  existing `/ws` protocol (no protocol fork), independently confirmed on
  both client and server logs.
- Foreground-service survival: **96 continuous minutes, zero
  disconnects**, spanning 20+ screen on/off cycles, under Unrestricted
  battery status.
- Root-caused connection-reliability failure mode under **Default**
  battery status: 5.5–11 minute silent gaps and real, repeated
  connection drops — reproduced in a short, targeted confirmation run.
- A real Wi-Fi→cellular handover leaving the device unable to reach the
  laptop's private LAN address for ~13 minutes — expected network
  behavior, tracked separately as Transport Reachability (`docs/TECHNICAL_DEBT.md`
  TD-003), not a battery or lifecycle issue.

## Hybrid Decision

Reaffirmed, not reopened, this milestone: the PWA remains the rich
conversational client; a native Android companion is justified for
exactly four capabilities categorically unavailable to a browser on
Android (home-screen widget, real audio-focus/ducking, a
backgrounding-resistant wake word, presence independent of a browser
tab's execution context) — see ADR-002. Milestone 9B.0's real-device
evidence is consistent with, and adds real data underneath, this
decision; it did not change it.

## Known Limitations (Summary)

Full itemized list: `SESSION.md` "Known Bugs, Limitations, and Technical
Debt" (56 numbered items as of this milestone); curated architectural
subset: `docs/TECHNICAL_DEBT.md` (20 items, TD-001 through TD-020). The
highest-severity open items, conditional on future work:

- **TD-002** (VoiceSession multi-client ownership guard) — must land
  before or during Milestone 9B; a hard precondition for a second
  simultaneous voice-capable client.
- **TD-018** (no authentication/TLS by default) — critical the moment
  remote connectivity is considered; currently an accepted risk under the
  strictly-LAN deployment model.
- **TD-003** (Transport Reachability) — high-severity for any deployment
  beyond same-LAN; deliberately deferred to a future M10A.
- **TD-004** (background push reliability) — platform-imposed, no full
  fix expected for the PWA path; partially mitigated by the Android
  companion's persistent foreground-service model once built.

## Open Questions

- **Wake-word candidate**: microWakeWord is the evidence-based
  recommendation (D1, accepted), but the actual build spike (D5) —
  proving a pretrained model runs via a custom TFLite-interpreter wrapper
  on real hardware — has not been performed. This remains a **REQUIRES
  EXPERIMENT** item, not yet resolved to fact.
- **Multi-hour/overnight survival procedures** (protocol B/C/E) — real,
  informal 96-minute evidence exists; the formal, isolated versions of
  these procedures have not been run (TD-013), deliberately deferred to
  production-confidence validation near the end of Milestone 9B.
- **`JARVIS_OPENCODE_ALLOW_PAID`** — currently reverted to `false`,
  verified. Whether the free-tier rate-limiting condition that
  necessitated it was a one-off or will recur is unknown; see ADR-009's
  Future Revisit Conditions.

## Production Readiness

**Not production-ready, and not claimed to be.** The Android spike is
formally classified **DISPOSABLE** (`ARCHITECTURE.md` §17,
`spikes/android-presence/` self-assessment) — it proves technical
feasibility for its two target risks, and is explicitly not a foundation
to build production code on top of without a rewrite. No production
Android companion code exists. Milestone 9B (production implementation)
has not started.

The core laptop-resident system (Supervisor, task/attention/voice/
notification pipeline, OpenCode integration) remains at the maturity
level established through Milestone 8.1 — real-device-validated,
regression-tested, and considered production-ready *for its existing,
LAN-only, single-user deployment model* specifically (not for remote
access, not for multi-user access — see TD-018).

## Risk Assessment

| Risk | Status | Mitigation |
|---|---|---|
| Second simultaneous client races on VoiceSession | Real, will trigger the moment a second client connects | TD-002, designed fix not yet built — must precede Milestone 9B's companion going live |
| Remote/non-LAN connectivity | Unaddressed | TD-003/TD-018, explicitly deferred to M10A, not attempted prematurely |
| Wake-word engine actually buildable on real hardware | Unproven | D5 build spike is the next concrete step to resolve this, gated on approval |
| OpenCode delegated-agent cost leakage | Closed, with one small residual gap (title-generation calls, TD-007) | Env allowlist + explicit model pinning (ADR-004, ADR-009), live-verified |
| Samsung OEM battery policy defeating companion reliability | Understood, with a working mitigation (Unrestricted battery) | Root-caused this milestone (Phase 3); production companion should request Unrestricted status during setup |
| Documentation drift from implementation | Actively checked this milestone; found small, disclosed gaps, no contradictions of substance | This checkpoint + `docs/TECHNICAL_DEBT.md` TD-015–017 |

## What Must Happen Before M9B.1

1. **Fix or explicitly accept** TD-015/TD-016 (stale milestone-status
   language, conversation-ownership clarity gap) in `SESSION.md`/
   `ARCHITECTURE.md` — small, low-risk documentation corrections
   identified but not applied during this review (by explicit
   instruction to review, not edit).
2. **Decide** whether the D5 wake-word build spike proceeds next, or
   whether M9B.1 addresses TD-002 (VoiceSession ownership guard) first —
   both are legitimate next steps; this checkpoint does not choose
   between them.
3. **No new architectural decisions should be made without a
   corresponding ADR** — the system established in `docs/decisions/` is
   now the expected process for any future decision of that weight.
4. Continue treating `spikes/android-presence/` as disposable — any
   decision to promote part of it to production status should be
   explicit and documented (a new ADR would be the correct vehicle), not
   assumed.

---

*This checkpoint does not authorize or begin Milestone 9B.1. It is a
review artifact only.*
