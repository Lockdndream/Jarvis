# ADR-010: Evidence-Based Engineering

## Status

Accepted

## Date

2026-07-09 (Milestone 6, verified-evidence task states — the first
explicit statement of this philosophy), practiced continuously through
Milestone 9B.0

## Context

Milestone 6 discovered that OpenCode task completion had been silently
unverifiable since Milestone 4 — Jarvis had been inferring success from
elapsed time and absence of error, never from anything OpenCode itself
actually confirmed. This was not a one-off bug; the same pattern recurred
across the project's history in different forms: a delegated code
review's confidently-stated findings turning out to be factually wrong on
independent verification (Milestone 9B.0's D4); a native foreground
service's "survived screen-off" claim requiring cross-checked client-and-
server timestamps to actually mean anything (Milestone 9B.0's Phase 3);
an HTTP 200 response that told Jarvis nothing about whether OpenCode had
actually done any work at all (Milestone 6). By Milestone 9B.0 this had
become a named, deliberately-practiced discipline rather than a lesson
re-learned each time.

## Problem

What should count as proof that something happened — a task completed, a
device survived a condition, a review's finding is correct — in a system
where the cheap, tempting signals (HTTP 200, elapsed silence, confident-
sounding LLM prose) are demonstrably not reliable evidence?

## Decision

**Evidence over inference, applied uniformly, regardless of how
inconvenient that is.** Concretely, as practiced across this project:

- **Verify on real hardware, not just in a test harness.** Milestone
  7.1 through 9B.0's real-device validation on the Samsung Galaxy S20 FE
  is not incidental — a browser-simulated or emulator-only result is
  explicitly distrusted for exactly the claims that matter (voice
  accuracy, push delivery, background survival), because platform
  behavior has repeatedly diverged from what a simulated environment
  predicted (e.g. the emulator-only `10.0.2.2` placeholder correctly and
  instructively failing on real hardware, Milestone 9B.0).
- **Never fake a PASS.** A task is `completed` only because a specific,
  named piece of native evidence said so (`session.idle`,
  `session.error`, a filesystem artifact with exact content match) —
  never because time passed without an error. A restart-interrupted task
  becomes `degraded` (unknown), not `failed` and not `completed`, because
  a restart proves nothing about what was actually happening.
- **Distinguish assumption from fact.** Milestone 9B.0's D1 wake-word
  research explicitly categorized every claim as FACT, REPOSITORY
  EVIDENCE, ASSUMPTION, or REQUIRES EXPERIMENT — a pretrained model file
  existing in a public repository is a fact; whether it runs correctly on
  a real device via a hand-built interpreter wrapper is, honestly, an
  assumption until an experiment says otherwise.
- **Separate repository/documentation evidence from platform evidence.**
  A GitHub repository confirming a library exists and has a stated
  license is real evidence of one specific thing; it is not evidence that
  the library behaves as documented on a specific real device — these are
  treated as different tiers of confidence, not conflated.
- **Cross-verify independent sources when more than one is available.**
  Milestone 9B.0's Phase 3 battery-optimization finding was accepted only
  once the phone's own telemetry and the server's independent connection
  log were checked against each other and found consistent — either
  source alone would have been a weaker, single-witness claim.
- **Independently spot-check delegated work rather than accepting it
  automatically.** A delegated OpenCode code review's two most
  consequential findings were both confidently stated and both
  independently verified to be factually wrong (against AOSP source and
  documented Android behavior, not just re-reading the same code) before
  being rejected — Milestone 9B.0's D4. No code changes were made based
  on either false claim.
- **Document uncertainty honestly rather than resolving it prematurely.**
  The exact mechanism of the Milestone 6.1 Phase 1 incident (why a
  partial-environment-variable command mutated the shared OpenCode
  database) remains formally unconfirmed and is recorded as such, not
  papered over with a plausible-sounding but unverified explanation.

## Alternatives Considered

**Trust structured API responses (HTTP status codes, tool-call logs)
as sufficient proof of completion.** Rejected, directly, by the Milestone
6 investigation that motivated this ADR — HTTP 200/204, a tool-call log
alone, LLM prose, or elapsed silence are explicitly enumerated as *not*
acceptable proof in that milestone's own specification.

**Accept a single source of telemetry as authoritative when cross-
verification is available but costs more effort.** Rejected in practice:
Milestone 9B.0 repeatedly chose to pull server-side logs alongside
device-side telemetry specifically because a single source's absence of
a signal (e.g. no `WS_DISCONNECTED` logged) turned out, more than once,
to mean the *detection* was delayed, not that the underlying event never
happened.

**Accept a delegated agent's own report of its work as sufficient.**
Rejected, with a concrete, real counterexample: D4's independent
spot-check found real, confidently-stated errors that a "trust the
report" policy would have let stand uncorrected.

## Consequences

Any future claim of success — a new feature working, a device surviving
a condition, a delegated agent's finding — is expected to be
accompanied by the specific evidence that would let another engineer
independently check it, not just a description of what was attempted.
This is now the default standard for `SESSION.md` entries and for how
this project evaluates its own work, including work done by AI agents
(delegated or otherwise) on the codebase itself.

## Positive Outcomes

- Real, load-bearing bugs were found specifically because this discipline
  refused to accept a plausible-looking signal at face value: the M9A
  `get_messages()` wrong-endpoint bug, the multi-turn voice session bug,
  the OpenCode credential-leak incident, and both of D4's false findings
  were all caught this way, not by luck.
- The project's own historical record (`SESSION.md`) is trustworthy
  specifically because failures and unresolved uncertainties are recorded
  alongside successes, not filtered out — a future engineer can rely on
  "not listed as a known limitation" actually meaning "checked," per
  `ARCHITECTURE.md` §14.

## Tradeoffs

- This discipline is genuinely slower — real-device testing, independent
  cross-verification, and spot-checking delegated work all cost real time
  that a "trust the first plausible signal" approach would not spend.
- It sometimes produces an honest "inconclusive" result (Milestone 9B.0's
  first screen-off survival test) rather than a clean pass/fail, which is
  a less satisfying outcome to report but a more honest one.

## Future Revisit Conditions

This is not expected to be revisited — it is the project's most
consistently reinforced principle, re-derived independently across
Milestones 6, 9A, and 9B.0 from different real incidents each time. A
revisit would require a demonstrated case where this discipline's cost
genuinely outweighed the value of the bugs and false claims it has
caught, which has not occurred.

## References

- `ARCHITECTURE.md` §2 (Design Principles — "Evidence over inference"),
  §14 (Failure Philosophy)
- `SESSION.md`, Milestone 6 (verified-evidence task states, the
  foundational statement), Milestone 9A (D1's FACT/ASSUMPTION/REQUIRES
  EXPERIMENT categorization), Milestone 9B.0 (cost-boundary incident,
  Phase 3 cross-verification, D4 independent spot-check)

## Related Milestones

Milestone 6, Milestone 9A, Milestone 9B.0

## Related Source Files

- `app/integrations/opencode_supervisor.py` (verified-evidence state
  transitions)
- `tests/m6_execution_probe.py` (the original evidence-based verification
  script)
- `spikes/android-presence/docs/survival-test-protocol.md` (explicit
  PASS/FAIL criteria defined before testing, per this same discipline)
