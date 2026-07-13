# ADR-013: Standing Delegation-Workforce Cost Policy

## Status

Accepted

## Date

2026-07-13 (Milestone 9B.2)

## Context

Through Milestone 9B.2, the user redefined how this project's engineering
work gets done: Claude becomes Chief Engineer (planning, architecture,
decomposition, review, acceptance) and DeepSeek V4 Flash — via the
existing isolated OpenCode runtime and OpenRouter — becomes the standing
implementation workforce, with explicit role separation (Builder,
Reviewer, Test Engineer, and others as needed) and a Builder never
reviewing its own work.

**The objective of delegation is not to eliminate human (Claude-side)
engineering effort — it is to shift that effort from implementation to
verification, integration, and architectural oversight.** The pilot run
under this policy (Milestone 9B.2) bore this out directly: the delegated
LLM work itself was fast and cheap (a few minutes, a few cents), while
the majority of wall-clock time went to precise task specification,
independent verification of both the Builder's code and the Reviewer's
own claims, and fixing the one real bug the review layer surfaced. That
is not overhead to be optimized away — it is the Chief Engineer role
functioning as intended, and this cost policy is designed around that
model, not around a larger delegation batch size or count.

`deepseek/deepseek-v4-flash` is a paid model. ADR-009 already anticipated
delegated OpenCode work needing a paid model occasionally, and built
exactly this model into `ALLOWED_PAID_OPENCODE_MODEL_ID` — but under a
"temporary, session-scoped, requiring re-approval each time" policy,
explicitly designed as a one-off emergency escape hatch, not a standing
arrangement. A durable multi-agent workforce doing real ongoing
implementation work needs a durable cost policy, not a re-ask on every
delegation batch.

## Problem

How does Jarvis fund a real, ongoing, paid delegation workforce — without
turning a deliberately narrow emergency override into an unaudited,
ever-growing blank check, and without weakening the free-only guarantee
for the Supervisor's own conversational spend?

## Decision

**`JARVIS_OPENCODE_ALLOW_PAID` becomes a standing configuration, scoped
strictly to delegated OpenCode agent work, with exactly one approved
paid model and a credit-threshold guardrail replacing per-use
re-approval.**

- `JARVIS_OPENCODE_ALLOW_PAID=true` is now a standing `.env` setting, not
  re-requested per session or per batch.
- Scope is unchanged from ADR-009: this affects `OpenCodeAdapter`'s
  delegated-worker model selection only. The Supervisor's own model
  (`JARVIS_LLM_FREE_ONLY`, `validate_free_only_model()`) is completely
  unaffected — this ADR does not touch that guard.
- Exactly one paid model remains allowed:
  `ALLOWED_PAID_OPENCODE_MODEL_ID = "deepseek/deepseek-v4-flash"`
  (unchanged constant, `app/integrations/opencode_adapter.py`). No other
  paid model is authorized without the user explicitly naming it — this
  ADR does not open the door to "any paid model," only this one, matching
  ADR-009's original reasoning for why an unscoped allowance is unsafe.
- **New guardrail, replacing per-use re-approval**:
  `app/integrations/openrouter_credits.py` checks real remaining
  OpenRouter credit via `GET https://openrouter.ai/api/v1/key`
  (`limit_remaining` field — verified against the real API during this
  ADR's own design, not assumed from documentation) before a delegation
  batch. If remaining credit is at or below a configurable threshold
  (`JARVIS_OPENCODE_CREDIT_THRESHOLD_USD`, default $1.00 — roughly 20% of
  this project's current $5 OpenRouter key limit, chosen to leave enough
  margin to finish an in-flight task rather than pausing mid-way), Claude
  pauses and asks for explicit approval before further paid delegation,
  rather than continuing to spend silently or degrading unpredictably. A
  key with no spending limit set (`limit_remaining` is `None`) never
  triggers a pause — nothing to guard against.
- **Spend visibility**: Claude reports which model was used for delegated
  work (already true — `send_prompt()` always sends an explicit model
  field, ADR-004) and, where a delegation report is produced, the
  remaining-credit figure the guardrail observed.
- Claude remains Chief Engineer: planning, dependency-graph creation,
  task decomposition, prompt generation, architecture decisions, code
  review, independent spot-checks, regression testing, real-device
  validation, and acceptance decisions are never delegated. Only bounded,
  independently-testable implementation units are.

## Alternatives Considered

**Keep per-batch re-approval, just less frequent than per-task.**
Considered (this was one of the options the user was offered). Rejected
in favor of a credit-threshold guardrail: re-approval-by-schedule doesn't
actually track the thing that matters (how much money has been spent) —
a credit check does, directly, and degrades gracefully (pause and ask)
exactly when it matters instead of on an arbitrary cadence.

**Unscoped standing approval for any paid model.** Rejected, explicitly,
by the user's own instruction ("This does not authorize arbitrary paid
models") and consistent with ADR-009's original reasoning: an unscoped
allowance is only as safe as every other constraint around it, and this
project's standing rule (ADR-004, ADR-009, ADR-010) is to assume the
permissive default is unsafe until proven otherwise.

**No spend guardrail at all — trust the standing approval alone.**
Rejected: a standing policy with no feedback signal is exactly the
"quietly becomes the default with no brake" failure mode ADR-009's
original "requires re-approval each time" language existed to prevent.
The credit-threshold check preserves a real brake without reintroducing
per-use friction.

## Consequences

Delegation batches can be launched without re-asking for paid-model
approval each time, as long as the credit guardrail keeps passing. The
guardrail is real code, not a policy statement — `should_pause_for_credit()`
is called against the live OpenRouter API and unit-tested against both a
real observed response shape and failure modes (network error, HTTP
error, missing key).

## Positive Outcomes

- The guardrail was validated against the real OpenRouter API during
  this ADR's own design (`GET /api/v1/key` on the project's actual
  configured key returned `limit=5, limit_remaining=4.97,
  usage=0.0266`), not just assumed from documentation.
- 11 new unit tests (`tests/test_openrouter_credits.py`) cover the
  threshold default/override/malformed-env cases, the real response
  shape, the unlimited-key case, and every failure mode (missing key,
  HTTP error, network error) — a guardrail that fails silently isn't one,
  so failure-to-check is treated as "cannot proceed," not "assume fine."

## Tradeoffs

- The credit check is a point-in-time read — a burst of concurrent
  delegation batches between checks could still overspend past the
  threshold before the next check catches it. Acceptable given the
  dollar amounts involved (a $5 key, $1 threshold) and this project's
  single-operator, non-adversarial deployment model.
- `JARVIS_OPENCODE_CREDIT_THRESHOLD_USD`'s default ($1.00) is a judgment
  call, not derived from a formal cost model of expected delegation
  volume — revisit once real usage data exists.

## Future Revisit Conditions

Revisit the threshold value once real delegation-workforce usage data
exists (this ADR is written before the pilot task — Milestone 9B.2 — has
run). Revisit the single-model restriction if a second paid model is
genuinely needed (requires explicit new user approval naming it, per
ADR-009's original reasoning, unchanged by this ADR). Revisit the
point-in-time-check limitation if concurrent delegation volume grows
enough for it to matter in practice.

## References

- `ARCHITECTURE.md` §4 (LLM provider), §12 (Security Model)
- `SESSION.md`, Milestone 5 (`JARVIS_LLM_FREE_ONLY` introduced), Milestone
  9B.0 (cost-boundary incident, paid override introduced), Milestone 9B.2
  (Chief Engineer / delegation workforce model adopted, this ADR)
- ADR-004 (OpenCode Runtime Isolation), ADR-009 (Cost Policy — partially
  superseded by this ADR for delegated OpenCode work specifically; the
  Supervisor-side free-only guard is unaffected), ADR-010
  (Evidence-Based Engineering)

## Related Milestones

Milestone 5, Milestone 9B.0, Milestone 9B.2

## Related Source Files

- `app/integrations/openrouter_credits.py` (`get_remaining_credit_usd`,
  `should_pause_for_credit`, `credit_threshold_usd`)
- `app/integrations/opencode_adapter.py` (`ALLOWED_PAID_OPENCODE_MODEL_ID`,
  `_allow_paid_opencode`, `validate_opencode_model` — unchanged by this
  ADR)
- `.env` (`JARVIS_OPENCODE_ALLOW_PAID`, `JARVIS_OPENCODE_CREDIT_THRESHOLD_USD`)
- `tests/test_openrouter_credits.py`
