# ADR-029: Walk-Away Mode — Delegate, Notify, Recall

## Status

Accepted

## Date

2026-07-31 (Month 2, Weeks 7-8 — post-F1 roadmap, walk-away mode, final
milestone of the two-month plan)

## Context

The two months preceding this milestone built the three pieces walk-away
mode ties together: STT (Week 1 — clean voice transcription), the
worker registry and strategist (Week 2 — ADR-026), the plan executor
(Weeks 3-4 — ADR-027, autonomous multi-step workflows with restart
recovery and escalation), and memory v1 (Weeks 5-6 — ADR-028, FTS5-based
episodic recall). None of these pieces, individually, let a user say
"run my tests while I'm gone, ping me when done," walk away, and trust
the result without checking the laptop. Mapping the existing loop
(Step 0) found the gap was narrower than it first appeared:

- A plan or task that finished successfully produced **no notification
  at all** — `plan_executor.py`'s `_run_plan` success path never called
  any notification mechanism. This, not a missing capability elsewhere,
  was the single hard blocker: "ping me when done" literally did not
  happen on success.
- `retrieve_memories()` (ADR-028) answers "what do we know about X" by
  keyword relevance — it structurally cannot answer "what happened
  recently," since a natural recall question shares no vocabulary with
  the actual stored content. This was ADR-028's own documented lead gap
  for the next iteration, and is the second hard blocker: "what
  happened while I was gone" returned nothing.
- Everything else found in the gap map (thin escalation/completion
  notification bodies, no natural-language-to-plan decomposition
  guidance, no `catch_me_up` tool) was confirmed at Opus Checkpoint #1
  to be polish, not blocking — the loop can technically complete without
  them, just roughly.

A separate, load-bearing finding surfaced only during this milestone's
real-device test (Step 5), independent of anything built here: the
`project_dir` isolation the codebase has described as a safety boundary
since Milestone 9A (`jarvis-app-src`'s own description) provides **no
actual containment** — confirmed live, an OpenCode task escaped its
assigned sandbox with zero permission gate and ran the real test suite
against the live repository, secrets included. This is recorded as
TD-026 (Critical) and is explicitly **not** addressed by this ADR — see
Non-Goals.

## Decision

**Four small, additive changes, each answering one piece of "delegate →
execute → notify → resume": a second, time-ordered retrieval path
alongside FTS5; a `catch_me_up` tool that combines it with live
plan/escalation state; prompt-level guidance (not a new mechanism) for
turning a natural multi-step request into `create_plan`+`start_plan`
calls; and wiring the already-existing `notifications.notify()`
completion path into the one place that never called it.**

### Recency-first retrieval is a second query shape, not a smarter FTS5

`app/memory.py` gained `get_recent_activity(since, project, limit)` — a
plain `created_at DESC` query against `category='episodic'` rows, no
`MATCH`, no relevance ranking — and `get_activity_summary(since,
project)`, which groups the result by `source` (plan_completion /
task_completion / conversation / strategist) into a human-readable
recap, always returning an honest "nothing happened" string rather than
an empty one when there's genuinely nothing to report.

`app/supervisor/context.py`'s `build_context()` gates between the two
retrieval paths with a deliberately simple fixed-phrase check
(`_is_temporal_query` — "what happened," "catch me up," "while i was,"
etc.), reusing the same `relevant_memories` context key so
`format_memory_sections()` needed zero changes. This mirrors memory
v1's own precedent of not over-engineering a trigger (the every-10-turn
conversation summarizer): a phrase list that occasionally fires when
FTS5 relevance would have been marginally better costs nothing (recency
always returns something useful); the reverse — a keyword search
silently returning nothing for an obviously temporal question — was the
actual problem ADR-028 flagged.

### `catch_me_up` is a tool result, not a context-injection path

`get_activity_summary`'s output is deliberately multi-line and
structured. `format_memory_sections()` collapses embedded newlines in
every memory it renders (a fix from memory v1's own Checkpoint #2, to
stop multi-line content escaping its labeled bullet) — running
`catch_me_up`'s summary through that same renderer would flatten it
into one unreadable line. The tool returns its combined string directly
as the tool result instead, exactly like `plan_status` already does,
bypassing `build_context`/`format_memory_sections` entirely.

`catch_me_up(since, project)` combines three sources: `get_activity_
summary` for the recap; a live check of `paused`/`failed` plans
(rendered with the exact "say retry, skip, or abort" language an
escalation notification uses, for consistency); and unresolved
`SUPERVISOR_ESCALATION` attention requests **not already covered** by a
reported plan (deduplicated via the `task_id == plan_id` correlation
`_escalate()` already establishes — an escalation and the paused plan
it belongs to are the same underlying event, never reported twice).

Determining the default time window ("since last conversation")
required threading `conversation_id` into tool calls for the first time
— `ToolRegistry.call(name, args)` had never received it. Fixed
additively: an opt-in `wants_conversation_id` flag on `_register()`/
`call()`, defaulting to `False` for every existing tool (zero behavior
change), set only for `catch_me_up`. `app/database.py`'s
`get_previous_conversation_boundary(current_conversation_id)` returns
the most recent conversation activity timestamp excluding the current
conversation (or the global max if none is given).

### Natural plan creation is prompt guidance, not a new tool or parser

`create_plan`'s tool schema was already sufficient; the gap was
entirely that `SYSTEM_PROMPT` gave the LLM no signal for *when* to use
it. A new section distinguishes three shapes: a single action (direct
tool call, no plan); a sequential request with a real precondition
("run the tests, then lint, then push if clean" — `on_failure=stop`,
the existing default, already *is* "stop the plan if an earlier step
fails," so "push if clean" needs no new mechanism); and branching on
*content* rather than *failure* ("check CI status, and if it's red, run
tests locally") — which `on_failure` cannot express, since a status
check succeeds technically whether the status is good or bad, and
folding a condition into a `create_plan` step would silently produce a
plan that runs both steps regardless of outcome. For the latter, the
prompt directs the LLM to either fold the condition into one worker
instruction, or make the first tool call itself and decide the next one
as an ordinary follow-up.

**Verified against the real, production-configured LLM** (not
`FakeLLMProvider`) via a harness replicating `Supervisor.process_message`'s
actual tool-calling loop shape, not a single round-trip: all three
canonical requests produced the intended decomposition. One real defect
surfaced in the model's own passing output — a plan step's LLM-generated
description read "If all tests and linting passed, commit and push
changes," a condition the step's own worker cannot evaluate (by the
time step 3 runs, `on_failure=stop` already guarantees steps 1-2
succeeded; the worker has no visibility into that). Fixed with one
paragraph: step descriptions must be imperative and self-contained,
never phrased as a condition on an earlier step. Re-verified live after
the fix — the same request now produces "Commit and push the changes."
Also hardened `create_plan`'s `worker_name` schema property with an
`enum` (`opencode`, `strategist`) — previously unvalidated, unlike
`on_failure` — so a hallucinated worker name fails at plan-creation time
instead of silently creating a plan doomed to fail its first dispatched
step.

### Two separate notification mechanisms, and completion belongs to the one that already existed

`app/attention_manager.py` → `interruption_policy.py` → `contact_
channels.py` is for things requiring a user *decision*
(`SUPERVISOR_ESCALATION`, `QUESTION`, `PERMISSION`) — reusing this for
a successful plan completion (no decision needed) would have meant
adding a new attention type to `interruption_policy.decide()`'s table,
an ADR-003-adjacent change for something that isn't actually a safety-
path decision. `app/notifications.py`'s `notify(conn_manager, kind,
...)`, gated by `attention_policy.decide(kind)`, already existed for
exactly this shape of event (`KIND_TASK_COMPLETED`, already wired to
`ACTION_NOTIFY` when the user's `notify_on_completion` setting is true
— confirmed default `True`) and was already used by `task_manager.py`
and `opencode_supervisor.py` for task completions. Plan completion —
both the success path and the previously entirely-silent stop-without-
escalation failure path — now calls this same mechanism, reusing the
same `KIND_TASK_COMPLETED` kind rather than inventing a parallel one.
**Zero changes to `interruption_policy.py`'s decision table.**

`_escalate()`'s summary (the literal text a push notification body is
built from — `contact_channels.py`'s `_deliver_via_notifications` reads
`attention_row["summary"]` verbatim) was enriched to front-load the
plan title, truncated error detail, and an explicit "Reply retry, skip,
or abort" instruction, measured against `app/push.py`'s confirmed
120-character body truncation (a realistic example measured at 90
chars; the pathological worst case — a 30-char title and 40-char error
simultaneously — measured at 121, one character over, costing only a
trailing period, never the actionable instruction). OpenCode task and
local task completions were enriched with their actual result summary
(OpenCode) or exit code (local tasks, honestly scoped — no fabricated
result text for a task type that never captures one).

**Verified on the real production server, not just tests**: after
discovering the live server had been running since before this
milestone's (and Month 1's) code ever landed — confirmed directly, the
live `jarvis.db` had none of `plans`/`plan_steps`/`memories` — the
server was restarted with explicit user sign-off, and the full loop was
exercised for real: a voice command dispatched a real OpenCode task,
completion produced a real enriched push notification the user actually
received on their phone, and a later "what happened while I was gone"
correctly used `catch_me_up`.

## Alternatives Considered

**A new `AttentionRequest` type for plan completion (e.g.
`PLAN_COMPLETED`), routed through `interruption_policy`.** Rejected. A
successful completion requires no user decision — routing it through
the same mechanism as things that do would mean editing ADR-003's
deterministic decision table for something outside the safety path it
exists to govern, and would give plan completions their own escalation/
retry semantics they don't need.

**A smarter conversation-aware or embedding-based retrieval instead of
a second plain query shape for recency.** Rejected — ADR-028 already
rejected embeddings for v1 on local-first/no-new-dependency grounds;
nothing about the recency gap changes that calculus. A `created_at`
ordered query is the correct, minimal answer to "what happened
recently," not a reason to revisit that decision.

**Building `catch_me_up`'s "since last conversation" default via a new
constructor-injected `conversation_id` on `ToolRegistry`.** Rejected —
`conversation_id` is per-call, per-turn state, not a fixed dependency
the registry is constructed once with; threading it through `call()`
as an optional keyword (with a registration flag so only tools that
need it receive it) is the shape that matches how every other per-call
argument already flows through the same method.

**A natural-language date/time parser inside `catch_me_up` for
resolving phrases like "since yesterday" or "while I was at lunch."**
Rejected — the tool's schema description instructs the calling LLM to
resolve relative time references into an ISO datetime itself, matching
the existing "LLM decides tool arguments, the tool doesn't NLP-parse"
contract already established by `remember_this`'s project-scoping
instruction.

**Fixing TD-026 (the `project_dir` containment gap) as part of this
milestone**, since it was discovered during this milestone's own
real-device test. Rejected — explicit user decision. It is a separate,
larger-scoped problem (an OpenCode-side permission-model question, not
something this codebase's own project-alias configuration alone
controls) that does not block shipping walk-away mode under its
current, explicitly accepted scope (single user, WiFi-only, user
present or nearby) — but escalates sharply the moment any future
milestone removes that assumption further. Documented as TD-026,
Critical severity, not silently deferred.

## Consequences

### Positive Outcomes

- The two confirmed hard blockers for "run my tests while I'm gone, ping
  me when done" are both closed: plan/task completions now always
  notify (subject to the existing `notify_on_completion` setting), and
  a later "what happened" question can now actually retrieve the
  answer via `get_recent_activity`/`catch_me_up`.
- Verified end-to-end on a real device, not just against unit tests:
  a real voice command, a real dispatched OpenCode task, a real push
  notification received on the user's phone with enriched content, and
  a real follow-up recall question answered correctly from memory.
- Natural plan creation requires zero new tools or parsing
  infrastructure — verified against the real production LLM, with one
  real decomposition defect (conditional step descriptions) found and
  fixed before shipping, not left latent.
- `interruption_policy.py`'s deterministic decision table (ADR-003) is
  completely untouched — plan completion notifications reuse an
  existing, already-gated mechanism rather than expanding the safety-
  path decision surface.
- A genuinely critical, previously-undiscovered isolation gap (TD-026)
  was found and documented with full reproduction detail as a direct
  result of doing this milestone's real-device test seriously, rather
  than settling for unit-test coverage alone.

### Tradeoffs

- **Recent-activity content can surface twice in one turn.** If the
  same message both matches `_is_temporal_query` (triggering context-
  block injection) and causes the LLM to call `catch_me_up` (per the
  new system-prompt rule), the same episodic memories can appear once
  as injected "Relevant recalled information" and again as the tool's
  own result. Same class of issue as memory v1's core-fact double-
  render, lower stakes (a duplicated tool result vs. contradictory
  framing, not a broken labeling guarantee). Not fixed this milestone.
- **`catch_me_up`'s escalation/failure-plan check does not cover every
  possible attention-request shape** — only `SUPERVISOR_ESCALATION`
  rows not already tied to a reported paused/failed plan are surfaced;
  a `QUESTION`/`PERMISSION` type pending elsewhere is not summarized by
  this tool (existing tools — `get_attention` — already cover those,
  and duplicating that coverage here was judged unnecessary).
- **The escalation summary's 120-character budget has a one-character
  overflow in the pathological worst case** (simultaneous max-length
  title and error) — costs only a trailing period, never the
  actionable "reply retry/skip/abort" text. Acceptable; not tuned
  further.
- **TD-026 remains open and, per explicit decision, unaddressed here.**
  Every safe-project description asserting a scoping guarantee
  (`jarvis-app-src`, and the `jarvis-repo-tests` alias added this same
  milestone specifically to let the real test suite run) is currently
  only as strong as the calling LLM's own default behavior — there is
  no enforced fallback. This is now explicitly disclosed in
  `jarvis-repo-tests`'s own `projects.json` description rather than
  silently assumed away.
- **No proactive "you're back" detection.** `catch_me_up` is entirely
  user-initiated; nothing detects a phone reconnecting and offers a
  summary unprompted. Explicit non-goal for this milestone — see
  Non-Goals.

## Non-Goals (explicit, this milestone)

- **Proactive "you're back" detection** (treating a phone reconnect
  event as a trigger to summarize activity unprompted). `catch_me_up`
  is deliberately user-initiated only, matching the two-month plan's
  explicit instruction not to build this yet.
- **Parallel plan step execution.** Unchanged from ADR-027 — plans
  remain strictly sequential; this milestone did not revisit that
  decision.
- **Background acoustic-noise resilience for STT.** Out of scope —
  this milestone did not touch `app/stt.py` or the STT pipeline in any
  way; a noisy/garbled transcript observed during the real-device test
  (unrelated ambient conversation picked up mid-session) was handled
  by the existing "ask for clarification" rule, not anything new here.
- **Fixing TD-026** (the `project_dir` containment gap) — see
  Alternatives Considered.
- **A memory-management or plan-management UI.** Unchanged from ADR-028
  — voice/chat remains the only interface, consistent with the
  Control Center's read-only observer mandate.
- **Tailscale, mobile-network, or any non-LAN connectivity path.**
  Explicitly out of scope for this sprint per the original brief; TD-003
  (Transport Reachability) and TD-026 both bear directly on why this
  boundary matters more, not less, before any future milestone
  revisits it.

## Future Revisit Conditions

- **TD-026 needs a real fix** before any milestone considers fully
  unattended overnight autonomous plan execution or any form of
  remote/non-LAN access (TD-003) — both remove the "user present and
  able to notice something wrong" assumption this milestone's own scope
  decision still partially relies on.
- The recent-activity double-surfacing tradeoff produces a genuinely
  confusing user-facing duplication (not just theoretical) — revisit
  either suppressing `_is_temporal_query`'s context injection when
  `catch_me_up` is the tool actually called, or documenting it more
  prominently if it proves harmless in practice.
- A future milestone wants `catch_me_up` or plan escalations to
  surface `QUESTION`/`PERMISSION`-type attention requests too, not just
  `SUPERVISOR_ESCALATION` — revisit the dedup logic then, with a
  concrete case in hand.
- The 120-character escalation-summary overflow in the pathological
  worst case is observed to actually truncate meaningful content for a
  real plan title/error combination — revisit the truncation lengths
  then.
- Natural plan decomposition produces a bad plan on a real user request
  not covered by this milestone's three tested shapes — revisit the
  system-prompt guidance with the specific failing example in hand,
  not speculatively.

## References

- `CLAUDE.md` (Deterministic interruption policy — ADR-003, untouched
  by this milestone; evidence over inference — ADR-010, the standard
  this ADR's real-device verification was held to; no over-engineering).
- `docs/decisions/ADR-003-deterministic-attention-architecture.md`
  (the decision table this milestone's notification-mechanism choice
  deliberately did not touch).
- `docs/decisions/ADR-004-opencode-runtime-isolation.md` (the isolation
  model TD-026 found does not actually hold for at least one class of
  action — this ADR does not revise ADR-004, it documents the gap).
- `docs/decisions/ADR-026-worker-registry-and-strategist-consultation.md`
  (the worker abstraction `create_plan`'s steps dispatch through).
- `docs/decisions/ADR-027-plan-executor-pattern.md` (`on_failure`
  semantics this milestone's prompt guidance and the conditional-step-
  description fix both depend on understanding precisely; sequential-
  only execution, unchanged here).
- `docs/decisions/ADR-028-memory-v1-three-tier-model.md` (FTS5
  retrieval, `format_memory_sections`'s newline-collapsing behavior
  that `catch_me_up` deliberately routes around, and the recency-gap
  finding this milestone closes).
- `docs/TECHNICAL_DEBT.md` TD-026 (the isolation-containment gap found
  during this milestone's real-device test, documented there in full
  reproduction detail rather than here).

## Related Milestones

Month 2, Weeks 7-8 of the post-F1 feature roadmap (walk-away mode),
Steps 0-6 — the final milestone of the two-month plan.

## Related Source Files

- `app/memory.py` (`get_recent_activity`, `get_activity_summary`)
- `app/db_async.py` (async wrappers for both)
- `app/supervisor/context.py` (`_is_temporal_query`, `build_context`'s
  recency/FTS5 gating)
- `app/supervisor/tools.py` (`catch_me_up`, `_register`/`call`'s
  `wants_conversation_id` mechanism, `create_plan`'s `worker_name` enum
  hardening)
- `app/supervisor/supervisor.py` (`SYSTEM_PROMPT`'s plan-decomposition
  guidance, rule 13, the two `conversation_id`-passing call sites)
- `app/database.py` (`get_previous_conversation_boundary`)
- `app/plan_executor.py` (`_run_plan`'s new completion/failure
  notification calls, `_escalate`'s enriched summary)
- `app/integrations/opencode_supervisor.py` (enriched task-completion
  notification body)
- `app/task_manager.py` (enriched local-task-completion notification
  body)
- `projects.json` (`jarvis-repo-tests`, the new project alias added to
  let the real test suite run — see TD-026 for the tradeoff this
  alias's own description discloses)
- `docs/TECHNICAL_DEBT.md` (TD-026)
