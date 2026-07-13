# ADR-003: Deterministic Attention Architecture

## Status

Accepted

## Date

2026-07-09 (Milestone 7, `AttentionPolicy`), reaffirmed 2026-07-10
(Milestone 8, `InterruptionPolicy` and `AttentionScheduler`)

## Context

Milestone 7 introduced the first version of "should Jarvis notify the
user about this" (`AttentionPolicy`). Milestone 8 extended this
substantially with a full attention lifecycle: `AttentionRequest`,
deferred re-contact, and `InterruptionPolicy` (should Jarvis interrupt
the user *right now*, and through which channel). At each point, using an
LLM to make this judgment call was a real, available option — Jarvis
already has an LLM in the loop for conversation (`Supervisor`). The
question was whether to extend that LLM's authority into deciding when
and how urgently to interrupt a human.

## Problem

Should the decision to notify or interrupt a user be made by the LLM
(flexible, can reason about nuance) or by deterministic code (rigid, but
predictable and auditable)? Interruption is a uniquely sensitive category
of decision — wrong in one direction (annoying, notifying too often) and
wrong in the other (missing something urgent) both have real, felt costs
to the user.

## Decision

`AttentionPolicy`, `InterruptionPolicy`, and `AttentionScheduler` are all
**pure, deterministic functions of known state** — attention type,
urgency, current status, connection state, prior contact count, and
quiet-hours configuration. None of them call an LLM. `AttentionScheduler`
additionally is entirely database-driven (polls `attention_requests` on
a fixed tick), never a client-side timer, so a deferred request survives
browser closure, phone reboot, and a full Jarvis restart identically.

## Alternatives Considered

**LLM-scored interruption urgency.** The Supervisor could, in principle,
be asked "how urgent is this, and should I interrupt the user now?" for
every candidate event. Rejected: an LLM's occasional creative
reinterpretation of "should I bother this person right now" was judged an
unacceptable failure mode for a safety-adjacent decision — false
negatives (missing something urgent) and false positives (interrupting
someone during genuinely quiet hours) are both real-world-visible
failures with no natural correction mechanism once they happen. A
deterministic policy's mistakes are at least *consistent and
debuggable* — the same input always produces the same decision, which is
not true of an LLM call.

**Hybrid: deterministic policy with an LLM override for edge cases.**
Considered implicitly and rejected — introducing even a narrow LLM
escape hatch here would reintroduce the exact unpredictability this
decision exists to avoid, and it is unclear what "edge case" would
justify it that the deterministic policy's own quiet-hours/urgency/retry-
count inputs couldn't already express as an explicit rule.

**Client-side scheduling (browser `setTimeout`/similar) for deferred
re-contact.** Considered and rejected during Milestone 8's design phase.
A client-side timer cannot survive the browser tab closing, the phone
rebooting, or the specific real-device backgrounding behavior already
documented (Milestone 9A Test A: Android discards a backgrounded PWA
tab's execution context entirely) — a deferred reminder set this way
could simply never fire. `AttentionScheduler`'s DB-driven tick was chosen
specifically because it does not depend on any client remaining alive.

## Consequences

Every future attention-related feature (a new contact channel, a new
device, a new urgency signal) must express its logic as additional
deterministic state and rules feeding into these existing policies,
not as a new LLM call layered on top. This has already held through
Milestone 9A/9B.0's Android companion planning — the native companion's
`attention/` module is expected to relay to the same laptop-side policies
(ADR-001), not implement its own.

## Positive Outcomes

- Fully testable without an LLM in the loop — `InterruptionPolicy`'s
  decision table has direct unit test coverage exercising every branch,
  something an LLM-based decision could not offer the same guarantee for.
- Predictable behavior a user can build a mental model of ("Jarvis
  doesn't interrupt me during configured quiet hours") — a property that
  matters specifically because interruption is felt immediately and
  personally, unlike most other Jarvis behavior.
- No LLM cost or latency on the hot path of every worker event —
  relevant given the project's own free-tier cost discipline (ADR-009).

## Tradeoffs

- Genuinely nuanced situations (e.g. "this failure is worded urgently but
  is actually routine") are not handled with any more sophistication than
  the deterministic rule table provides — no judgment call beyond what
  was explicitly coded.
- Every new signal that should affect interruption urgency requires a
  deliberate code change to the policy, rather than emerging naturally
  from a general-purpose LLM's reasoning.
- The policy's rules must be maintained and extended by an engineer, not
  tuned via prompt engineering — a different (not necessarily worse) kind
  of maintenance burden.

## Future Revisit Conditions

`ARCHITECTURE.md`'s own extensibility section already names this
directly: LLM-based interruption/importance scoring remains a possible
future direction *if the deterministic `InterruptionPolicy` proves
insufficient in real use* — but this has not happened as of Milestone
9B.0, and reversing this decision would require concrete evidence of a
real, recurring shortfall the deterministic rules cannot express, not
just a hypothetical nuance.

## References

- `ARCHITECTURE.md` §2 (Design Principles — "Deterministic policies where
  possible"), §4 (InterruptionPolicy, AttentionScheduler), §8 (Attention
  Architecture)
- `SESSION.md`, Milestone 7 (`AttentionPolicy` introduced), Milestone 8
  (`InterruptionPolicy`/`AttentionScheduler` introduced), Architecture
  Decisions #23–24

## Related Milestones

Milestone 7, Milestone 8

## Related Source Files

- `app/attention_policy.py`
- `app/interruption_policy.py`
- `app/attention_scheduler.py`
- `app/attention_manager.py`
