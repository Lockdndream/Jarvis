# ADR-009: Cost Policy

**2026-07-13 addendum**: The OpenCode-delegation override's "temporary,
session-scoped, requiring re-approval each time" behavior (see Decision,
below) is superseded by **ADR-013** for delegated implementation work
specifically — `JARVIS_OPENCODE_ALLOW_PAID` is now a standing
configuration for that one scope, gated by a credit-threshold guardrail
instead of per-use re-approval. Everything else in this ADR — the
Supervisor's own free-only guard (`JARVIS_LLM_FREE_ONLY`,
`validate_free_only_model()`), the exactly-one-named-paid-model pattern,
and the general "permissive default is unsafe until proven otherwise"
reasoning — is unaffected and remains in full effect. See ADR-013.

## Status

Accepted

## Date

2026-07-09 (Milestone 5, Supervisor-side free-only guard), extended
2026-07-11/12 (Milestone 9B.0 — OpenCode delegation cost boundary, and
the temporary paid-model override)

## Context

Jarvis's own conversational LLM (`Supervisor`) has used a free-only
OpenRouter model since Milestone 5, gated by `JARVIS_LLM_FREE_ONLY`.
Milestone 9B.0 found a second, previously-unguarded cost surface: the
isolated OpenCode server's own model selection for *delegated* worker
tasks was never pinned, and a real incident showed it silently using a
paid model (`gpt-5.3-chat-latest`) via an ambient, Jarvis-unrelated
`OPENAI_API_KEY` on the host machine. Separately, that same milestone hit
a real, sustained operational problem: free-tier OpenRouter capacity was
100% rate-limited for hours across every free model tried, blocking
delegated review work (D4) entirely.

## Problem

How should Jarvis guarantee its own LLM spend stays free-only by default,
across *two* independently-configured surfaces (the Supervisor's own
model, and OpenCode's delegated-worker model) — while still leaving a
deliberate, auditable way to spend real money when free-tier capacity is
genuinely unavailable and the user explicitly wants to?

## Decision

**Free-only by default, on both surfaces, enforced by the same
underlying validation function, with one narrow, explicitly-approved
emergency override for delegated OpenCode work only.**

- `validate_free_only_model()` (`app/supervisor/llm.py`) is the single
  source of truth for "is this model allowed under
  `JARVIS_LLM_FREE_ONLY`" — governs the Supervisor's own model directly.
- `validate_opencode_model()` (`app/integrations/opencode_adapter.py`)
  wraps this same function for delegated OpenCode worker sessions,
  without modifying it — free-only by default, identical behavior to
  before this wrapper existed.
- **`JARVIS_OPENCODE_ALLOW_PAID`** is a narrow escape hatch: when
  explicitly set to `true`, it permits *exactly one* explicitly-named
  paid model (`deepseek/deepseek-v4-flash` via `openrouter`, currently)
  for delegated OpenCode work only — no other paid model, and no effect
  on the Supervisor's own model choice. Default: unset/`false`, identical
  to the behavior before this flag existed.
- Every delegated OpenCode prompt explicitly pins provider+model (ADR-004)
  — OpenCode is never allowed to fall back to its own default selection,
  which is what made the original silent-paid-call incident possible in
  the first place.
- The override is treated as **temporary, session-scoped, and requiring
  re-approval each time it is enabled** — not a standing configuration
  choice. It was enabled once (Milestone 9B.0, to complete a specific
  blocked delegated review), used for exactly one session, and reverted
  the same session once its purpose was served — with the revert
  independently verified (fresh runtime restart, confirmed rejection of
  the paid model again, Desktop storage confirmed untouched).

## Alternatives Considered

**A single shared "allow paid" flag covering both the Supervisor and
OpenCode delegation.** Rejected: the two surfaces have different risk
profiles — the Supervisor's model choice affects every conversational
turn, while OpenCode delegation is occasional and explicitly initiated.
Conflating them would mean one override decision silently affects both,
which is a larger blast radius than any single approval should grant.

**An unscoped "allow any paid model" flag instead of one explicitly-named
model.** Rejected: this was the exact shape of risk the original
credential-isolation incident (ADR-004) demonstrated — an unscoped
allowance is only as safe as every other constraint around it, and this
project's standing rule (ADR-004, ADR-010) is to assume the permissive
default is unsafe until proven otherwise. Naming exactly one model keeps
the override auditable: a log line showing that model in use is
sufficient evidence the override was exercised as intended, not evidence
of a broader leak.

**Leave the free-tier rate-limiting problem unaddressed and simply retry
or wait it out.** Considered first, and genuinely tried — multiple retry
strategies (different free models, sequential instead of concurrent
delegation) were attempted across several hours before the paid override
was introduced. Rejected as the *sole* strategy once it became clear the
rate-limiting was a sustained, session-wide condition, not a transient
blip retries would clear.

**Make the override persistent (leave it enabled) once introduced, to
avoid needing to re-request it in the future.** Rejected, explicitly, by
direct instruction at the time it was introduced: "future use requires
explicit user approval" — the override's value depends on it never
becoming a silent standing default.

## Consequences

Any future delegated-agent work that hits sustained free-tier
unavailability has a known, safe, auditable path (re-enable
`JARVIS_OPENCODE_ALLOW_PAID`, get explicit approval, revert when done) —
rather than needing to invent a new bypass under time pressure, which is
exactly the condition under which a less-careful escape hatch would be
likely to leak scope.

## Positive Outcomes

- The override closed a real, blocking problem (D4's independent review
  could not complete on any free model for hours) without weakening the
  general free-only guarantee for anything else, including the
  Supervisor's own conversational spend.
- The revert was independently verified with the same rigor as the
  original fix — a fresh runtime restart, a direct check that the paid
  model is rejected again, and a Desktop-storage integrity check — not
  just "set the flag back and assume it worked."
- Four regression tests exist specifically for the boundary conditions
  this policy depends on: rejected by default, allowed only with the flag
  set, still rejects every *other* paid model even with the flag set,
  free models unaffected either way.

## Tradeoffs

- The override, while disabled by default, remains in the codebase as a
  standing capability — a future engineer (or a future version of an
  AI agent working on this codebase) could re-enable it without the same
  deliberation that produced this ADR, if the "requires explicit approval
  each time" norm is not actually enforced procedurally.
- The one-model allowlist is hardcoded (`ALLOWED_PAID_OPENCODE_MODEL_ID`)
  — changing which paid model is permitted requires a code change, not
  just a config change, which is a deliberate friction point, not an
  oversight.

## Future Revisit Conditions

Revisit if free-tier OpenRouter capacity issues become a *recurring*
rather than one-off problem — at that point, a more durable answer
(e.g. a small, explicitly-budgeted standing paid allowance, or a
different free-tier provider) should be considered rather than repeatedly
re-invoking a one-off emergency override. Revisit the hardcoded model
name if `deepseek/deepseek-v4-flash` is deprecated or no longer
available.

## References

- `ARCHITECTURE.md` §4 (LLM provider), §12 (Security Model), §16
  (ADR-012)
- `SESSION.md`, Milestone 5 (`JARVIS_LLM_FREE_ONLY` introduced), Milestone
  9B.0 (cost-boundary incident, fix, paid override introduced and
  reverted), Known Limitations #51–52, #55

## Related Milestones

Milestone 5, Milestone 9B.0

## Related Source Files

- `app/supervisor/llm.py` (`validate_free_only_model`)
- `app/integrations/opencode_adapter.py` (`validate_opencode_model`,
  `ALLOWED_PAID_OPENCODE_MODEL_ID`, `_allow_paid_opencode`)
- `.env` (`JARVIS_LLM_FREE_ONLY`, `JARVIS_OPENCODE_ALLOW_PAID`)
- `tests/test_opencode.py` (paid-model-allowlist regression tests)
