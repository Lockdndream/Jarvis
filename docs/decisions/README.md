# Architecture Decision Records

This directory is Jarvis's permanent record of *why* significant
architectural decisions were made — as distinct from `ARCHITECTURE.md`
(what the system is, kept current) and `SESSION.md` (what happened,
kept chronological). An ADR is written once, at the point a decision is
made or formalized, and after that changes rarely — typically only when
the decision itself is later superseded, deprecated, or rejected.

## What an ADR is

A short, durable document capturing one significant, hard-to-reverse
architectural decision: the problem it addresses, the decision made, the
real alternatives that were considered and rejected, and the honest
tradeoffs accepted. An ADR is not a design spec, not a changelog entry,
and not a tutorial. It should still make sense years later even if the
surrounding code has moved on, because it explains *reasoning*, not
current implementation detail.

## When a new ADR should be written

Write a new ADR when a decision meets most of these:

- It is expensive or disruptive to reverse (a different decision would
  require touching many modules, not one).
- It establishes an invariant other code is expected to honor going
  forward (e.g. "the Supervisor never executes work directly").
- It was chosen over genuine, considered alternatives — not just "the
  first approach that worked."
- Getting it wrong would be a real, not hypothetical, risk (a security
  boundary, a cost boundary, a data-ownership boundary).
- A future engineer would reasonably ask "why is it built this way?" and
  the answer is not obvious from reading the code alone.

Do **not** write an ADR for: a routine bug fix, a UI tweak, a dependency
version bump, or an implementation detail that doesn't constrain future
design (put those in `SESSION.md` instead, where they already belong).

## Numbering

ADRs are numbered sequentially, zero-padded to three digits, in the order
they were written: `ADR-001`, `ADR-002`, and so on. Numbers are **never**
reused, even if an ADR is later rejected or superseded — a rejected
ADR-004 stays ADR-004 forever; the next new ADR is still the next
unused number, not a replacement for it. File naming:
`ADR-NNN-short-kebab-case-title.md`.

## Lifecycle and status meanings

| Status | Meaning |
|---|---|
| **Proposed** | Under active consideration, not yet acted on. May not reflect the current implementation at all. |
| **Accepted** | The decision is in effect and the codebase should conform to it. This is the default status for a decision that has actually been implemented. |
| **Experimental** | Accepted for a bounded trial (e.g. a technical-risk spike) but not yet committed to as permanent; expect a follow-up ADR or a status change once the trial concludes. |
| **Superseded** | A later ADR has replaced this decision. The superseding ADR is named in this document's front matter; this document is kept, not deleted, as a historical record. |
| **Deprecated** | The decision is no longer recommended but nothing has formally replaced it yet — a signal to be cautious, not a claim that a replacement already exists. |
| **Rejected** | Considered and explicitly not adopted. Kept on record specifically so the same alternative is not re-litigated from scratch without first reading why it was rejected. |

A status change is itself worth a one-line addendum at the top of the
file (date + new status + one-sentence reason + link to the superseding
ADR if any) — the original body of the document is otherwise left
intact, not rewritten, so the historical reasoning stays visible.

## Review process

An ADR is proposed the same way any other significant architectural
change would be: written up, and reviewed by whoever has authority over
the architecture before its status moves from `Proposed` to `Accepted`.
For Jarvis specifically, that means: the ADR should cite the real
evidence behind the decision (a milestone's findings, a real incident, a
concrete alternative that was actually tried) — not merely assert a
preference. An ADR that turns out, on review, to just restate
`ARCHITECTURE.md` without adding the "why" has not done its job and
should be revised before being accepted.

## Format

Every ADR uses the same section structure, in this order:

1. **Title**
2. **Status**
3. **Date**
4. **Context** — the situation that made a decision necessary
5. **Problem** — the specific question being answered
6. **Decision** — what was decided, stated plainly
7. **Alternatives Considered** — genuine options that were rejected, and
   why
8. **Consequences** — what follows from this decision
9. **Positive Outcomes**
10. **Tradeoffs**
11. **Future Revisit Conditions** — what would justify reopening this
    decision
12. **References** — links to `SESSION.md`, `ARCHITECTURE.md`, and any
    external sources
13. **Related Milestones**
14. **Related Source Files**

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-laptop-remains-the-brain.md) | Laptop Remains the Brain | Accepted |
| [ADR-002](ADR-002-hybrid-android-architecture.md) | Hybrid Android Architecture | Accepted |
| [ADR-003](ADR-003-deterministic-attention-architecture.md) | Deterministic Attention Architecture | Accepted |
| [ADR-004](ADR-004-opencode-runtime-isolation.md) | OpenCode Runtime Isolation | Accepted |
| [ADR-005](ADR-005-worker-supervisor-architecture.md) | Worker / Supervisor Architecture | Accepted |
| [ADR-006](ADR-006-contact-channel-abstraction.md) | Contact Channel Abstraction | Accepted |
| [ADR-007](ADR-007-voicesession-ownership.md) | VoiceSession Ownership | Accepted |
| [ADR-008](ADR-008-hybrid-presence-model.md) | Hybrid Presence Model | Accepted |
| [ADR-009](ADR-009-cost-policy.md) | Cost Policy | Accepted |
| [ADR-010](ADR-010-evidence-based-engineering.md) | Evidence-Based Engineering | Accepted |
| [ADR-011](ADR-011-android-pairing-security-model.md) | Android Companion Pairing Security Model | Accepted |
| [ADR-012](ADR-012-device-state-sync-protocol.md) | Device State-Sync and Capability-Advertisement Protocol | Accepted |
| [ADR-013](ADR-013-standing-delegation-workforce-cost-policy.md) | Standing Delegation-Workforce Cost Policy | Accepted |
| [ADR-014](ADR-014-unified-websocket-authentication.md) | Unified WebSocket Authentication (Short-Lived Signed Tokens) | Accepted |
| [ADR-015](ADR-015-android-attention-widget.md) | Android Attention Widget | Accepted |
| [ADR-016](ADR-016-android-voice-infrastructure.md) | Android Voice Infrastructure | Accepted |
| [ADR-017](ADR-017-production-wakeword-foundation.md) | Production Wake-Word Foundation | Accepted |
| [ADR-018](ADR-018-jarvis-control-center-observability-architecture.md) | Jarvis Control Center — Observability Architecture | Accepted |
| [ADR-019](ADR-019-separation-of-observability-and-operations.md) | Separation of Observability and Operations | Accepted |
| [ADR-020](ADR-020-trace-id-execution-correlation-model.md) | Trace ID — Execution Correlation Model | Accepted |
| [ADR-021](ADR-021-structured-logging-architecture.md) | Structured Logging Architecture | Accepted |
| [ADR-022](ADR-022-jarvis-operations-subsystem.md) | Jarvis Operations Subsystem | Accepted |
| [ADR-023](ADR-023-jops-v1-operation-model-and-jarvis-self-management.md) | JOPS v1.0 — Operation Model, Jarvis Self-Management, Connectivity Policy Split | Accepted |
| [ADR-024](ADR-024-sqlite-write-concurrency-model.md) | SQLite Write-Concurrency Model | Accepted |
| [ADR-025](ADR-025-groq-whisper-stt.md) | Groq Whisper for Speech-to-Text | Accepted |
