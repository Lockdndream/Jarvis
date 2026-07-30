# ADR-027: Plan Executor Pattern

## Status

Accepted

## Date

2026-07-30 (Month 1, Weeks 3-4 — post-F1 roadmap, plan executor)

## Context

Before this change, the Supervisor could dispatch exactly one worker call
per conversational turn (`consult_strategist`, `start_opencode_task`, etc.
— see ADR-026). Chaining several dispatches into a multi-step, autonomous
workflow required the user to stay in the loop and issue each step
manually. This is the central blocker to a "walk away" mode: a user
saying "run these N things while I'm gone" and trusting the result,
without babysitting each step.

Two properties of the existing worker abstraction shape this decision
directly:

- **`OpenCodeWorker.invoke()` always returns `DISPATCHED`** (ADR-026) —
  the result arrives later via `OpenCodeSupervisor`'s SSE-driven
  completion path (`_handle_session_idle`/`_handle_session_failed` →
  `_completion_events[task_id]` set → consumed via
  `wait_for_completion(task_id, timeout)`), not synchronously. Any
  multi-step chain that includes an OpenCode step must therefore
  represent "waiting for an async result" as a first-class state, not an
  afterthought.
- **`mark_running_opencode_tasks_interrupted()`** force-degrades any
  `running`/`pending`/`waiting_for_user` `opencode_tasks` row on every
  server restart, and **`OpenCodeSupervisor.reconcile_on_startup()` never
  upgrades a `degraded` row back to a terminal state** — it is a
  structural no-op for that case today. A plan step whose OpenCode task
  was genuinely in-flight at the moment of a crash has no path back to
  "we know what happened"; the executor's own restart-recovery has to
  treat `degraded` as permanently ambiguous, not "retry the wait."

## Decision

**A `PlanExecutor` (`app/plan_executor.py`) that runs a `Plan` — an
ordered, persisted list of `PlanStep`s — sequentially, one worker
dispatch at a time, entirely in the background.** The Supervisor's LLM
creates and starts plans via five new tools; it never drives the
step-by-step loop itself.

### Sequential-only, by design, for v1

`PlanStep.depends_on` exists in the schema (`list[str] | None`) but is
unused — every plan currently runs its steps in `step_index` order, one
at a time. This is deliberate, not a placeholder for "not implemented
yet": a single user with a handful of ad-hoc plans has no demonstrated
need for parallel branches, and parallel execution multiplies the
restart-recovery and escalation-ordering questions (which failed step do
you report first? what does "resume" mean when two branches are in
different states?) that sequential execution avoids entirely. Revisit
only if a real workflow needs it — see Future Revisit Conditions.

### One `asyncio.Task` per running plan

`start_plan(plan_id)` claims the plan (`claim_plan_start()` — a
conditional-UPDATE-by-rowcount, the same pattern as
`transition_attention_status()`) and spawns
`asyncio.create_task(self._run_plan(plan_id))`, tracked in
`self._running_plans: dict[str, asyncio.Task]`. This is the same
per-unit-of-work-gets-its-own-task shape as
`AttentionScheduler`/`VoiceSessionReaper`'s background-service pattern,
applied per-plan instead of as a single shared tick loop — a plan
blocked waiting on a slow OpenCode step must never stall any other
running plan, the Supervisor's own conversation turns, or the process's
event loop generally.

### DISPATCHED steps: persist before waiting, hook into the existing SSE completion path, never invent a parallel one

For a step whose worker returns `WorkerResult.status == DISPATCHED`
(currently only `OpenCodeWorker`), `_run_step`:

1. Persists `worker_task_id` to the `plan_steps` row **before** awaiting
   anything further. This ordering is what makes restart recovery
   possible at all — without it, a crash during the wait would leave no
   record of which OpenCode task the step was even waiting on.
2. `await self._oc.wait_for_completion(task_id, timeout=...)` — the
   *existing* `OpenCodeSupervisor` mechanism (an `asyncio.Event` per
   task_id, set by the same SSE handlers that already drive every other
   OpenCode completion path). The executor does not poll, does not
   subscribe to a second event stream, and does not invent its own
   async-result mechanism — it is one more caller of the same
   already-proven bridge between OpenCode's SSE events and asyncio.
3. Re-reads `opencode_tasks.status` after the wait resolves (the
   completion event fires on both success *and* failure — the event
   alone only means "something terminal happened") to determine
   succeeded vs. failed, then persists the step's own terminal state.

Because `wait_for_completion` runs inside that plan's own `asyncio.Task`,
this wait is non-blocking with respect to everything else in the
process — confirmed directly (Opus Checkpoint #2) by tracing the
implementation, not just from the design.

### Restart recovery: reconcile once at startup, never resume a stale wait

`reconcile_on_startup()` finds every plan still marked `running`, and for
each one whose current step was itself `running` at the moment of the
crash:

- No `worker_task_id` recorded → step failed, "state unknown after
  restart" (crash happened before the persist-before-wait write above).
- `opencode_tasks` row is `completed` → step marked `succeeded` (the work
  genuinely finished before the crash; `mark_running_opencode_tasks_
  interrupted()`'s sweep only targets non-terminal statuses, so a
  `completed` row survives the restart untouched — no data loss, no
  duplicate side effect).
- `opencode_tasks` row is `failed` → step marked `failed` with the
  recorded error.
- Anything else (in practice: `degraded`, per the structural gap
  described in Context) → step marked `failed`, "state unverifiable
  after restart."

In every case, a fresh `asyncio.Task` is spawned to resume `_run_plan`
from the plan's `current_step_index` — but the loop's reconciliation-
aware branch applies the step's `on_failure` policy directly to an
already-terminal step rather than re-invoking the worker. This is the
concrete answer to "what happens if the OpenCode task's own result
arrives after the plan step has already been marked failed by
reconciliation" (Opus Checkpoint #2's question): it can't happen for
anything that matters, because nothing ever waits on a stale event or
re-dispatches a worker for a step reconciliation has already resolved —
avoiding, for example, a duplicate `git push` from a step whose
underlying work had actually already succeeded.

No code path today ever upgrades a `degraded` `opencode_tasks` row after
the fact, so a step whose task was genuinely in-flight at crash time is
permanently unrecoverable to "succeeded" — the executor fails it and
lets escalation (below) put the decision in front of the user. This is
an accepted, documented gap, not an oversight — see Future Revisit
Conditions.

### Escalation: reuse the existing AttentionRequest path, resume is a manual reconnection for v1

When a step fails and its `on_failure` is `escalate`, `_escalate()` calls
`attention_manager.get_or_create()` directly — the same idempotent-by-
`(source_type, source_id)` mechanism every other escalation in this
codebase uses (`QUESTION_REQUIRED`, `PERMISSION_REQUIRED`, `TASK_FAILED`
in `opencode_supervisor.py`/`task_manager.py`), with `source_type=
"plan_step"`, `task_id=plan_id`, `attention_type="SUPERVISOR_ESCALATION"`,
and `urgency="HIGH"` hardcoded — matching every one of those other call
sites exactly; there is no dynamic urgency-scaling anywhere in this
codebase, and a plan escalation blocking further autonomous progress
does not warrant inventing one. `context_json` carries `plan_id`,
`step_id`, `step_description`, and `error` — enough for the Supervisor's
`plan_status` tool to answer a follow-up question in full once the user
reconnects.

The `worker_events.py` `REVIEW_REQUIRED`/`SUPERVISOR_ESCALATION` pair was
considered and bypassed: its `source_type` property and `worker_type`
field are shaped for worker-sourced question/permission/failure events,
not plan-step escalations, and forcing plan-step data through that shape
would have distorted it more than calling `attention_manager` directly.

Resuming is a **manual reconnection**, not an automatic callback: the
user sees the notification, opens Jarvis, and says "skip that step" /
"retry it" / "abort the plan" in natural language. The Supervisor's LLM
recognizes this as a plan-related instruction and calls the `resume_plan`
tool (`plan_id`, `instruction: retry|skip|abort`). This is deliberately
the simplest thing that works for v1 — see Alternatives Considered.

### Five Supervisor tools, no execution-loop logic in the LLM's hands

`create_plan`, `start_plan`, `plan_status`, `resume_plan`, `list_plans`
(`app/supervisor/tools.py`), following the exact registration convention
already established for `consult_strategist` (ADR-026): guarded on
`if not self._plan_executor`, `plan_executor` threaded as a 5th optional
constructor parameter through `Supervisor.__init__` → `ToolRegistry.
__init__`, defaulting to `None`. `PlanExecutor` itself is constructed at
module level in `app/main.py` alongside `worker_registry`, and its
`start()`/`stop()` are wired into `lifespan()` immediately after/before
`opencode_supervisor.start()`/`.stop()` respectively — `reconcile_on_
startup()` depends on `opencode_tasks` rows that `opencode_supervisor.
start()`'s own restart sweep has already settled.

Decomposition (which steps, which workers, what order) is entirely the
calling LLM's judgment, expressed as `create_plan`'s arguments — the
executor has no natural-language understanding and does not generate
plans itself.

## Alternatives Considered

**Poll for DISPATCHED completion instead of hooking into the existing
SSE-driven `wait_for_completion`.** Rejected. `OpenCodeSupervisor`
already has a working, tested async-bridge from SSE events to asyncio;
polling would add latency, duplicate that mechanism, and give the
executor its own independent (and inevitably slightly different) notion
of "done" to keep in sync with the real one.

**An automatic resume callback (the executor re-subscribes and continues
the moment the user answers, no explicit tool call needed).** Rejected
for v1. The Supervisor has no existing mechanism for a background event
to inject itself into an arbitrary future conversation turn without the
user initiating it — building one is a larger, more speculative change
than a plan executor's first version needs. Manual reconnection via
`resume_plan` gets the same outcome (plan continues after the user's
decision) through the conversational path that already exists for every
other kind of attention request.

**Parallel step execution via `depends_on`.** Rejected for v1 — see
Decision, "Sequential-only, by design."

**Plan templates / reusable plan patterns.** Rejected. Every `create_plan`
call is fully ad-hoc; a template system would be premature abstraction
for a single user with no demonstrated repeat-plan-shape need yet.

**A plan editor UI.** Rejected — out of scope by explicit instruction;
plans are created only via the existing voice/chat path through the
Supervisor, consistent with "Supervisor routes, workers execute"
(ADR-005) — the executor is not a second entry point.

## Consequences

### Positive Outcomes

- The Supervisor can now express and run a multi-step autonomous
  workflow ("run tests, lint, push") from a single user request, only
  re-contacting the user when a step's `on_failure` policy says to.
- DISPATCHED-step handling reuses `OpenCodeSupervisor`'s existing,
  already-tested SSE completion bridge rather than introducing a new
  async-result mechanism the rest of the codebase would need to learn.
- Escalation reuses the existing `AttentionRequest`/`attention_manager`
  path exactly — a plan-step failure surfaces to the phone the same way
  every other kind of attention request already does, with no new
  notification-transport code.
- Restart recovery is bounded and explicit: every plan step's post-crash
  fate is one of a small, enumerated set (succeeded / failed-known-
  reason / failed-unverifiable) — never a hang, a duplicate dispatch, or
  silent data loss.

### Tradeoffs

- A step whose OpenCode task was genuinely in-flight at the exact moment
  of a crash is permanently unrecoverable to "succeeded" — the work may
  have actually finished, but the executor has no way to know and fails
  the step "unverifiable after restart." This is inherited from
  `OpenCodeSupervisor.reconcile_on_startup()`'s existing no-op for
  degraded-task upgrades, not something this ADR introduces, but the
  plan executor is the first consumer for whom this gap has a concrete,
  user-visible consequence (a step reported as failed that may have
  actually succeeded).
- Resume is a manual reconnection — the user must notice the phone
  notification and respond; there is no timeout-driven or automatic
  retry behavior for an escalated step. Acceptable for v1's scale (one
  user, ad-hoc plans) but a real constraint on how "walk away" a
  multi-hour plan can be if `on_failure=escalate` is used mid-plan.
- Sequential-only execution means a plan's wall-clock time is the sum of
  its steps' durations, including any DISPATCHED wait — there is no way
  to run independent steps concurrently yet.
- **No step-to-step data flow.** Every step's `description` is frozen at
  `create_plan` time; `Plan.context`/`context_json` exists in the schema
  and is accepted by `create_plan_record`, but nothing writes to it after
  a step completes or reads from it before dispatching the next step. A
  plan cannot express "run the tests, then fix whatever failed" — the
  fix has to already be known at plan-creation time. This is the primary
  gap to close before building memory on top of this executor (see
  Future Revisit Conditions).
- **`on_failure=continue` can make a plan with a real failure report as
  `completed`.** `list_plans`' one-line summary shows only overall status
  — a plan with a failed continue-step looks identical to a fully
  successful one unless the user separately calls `plan_status` to see
  per-step detail. Acceptable for v1 given `plan_status` already exposes
  the truth on request, but worth flagging as a trust gap for "walk away
  and glance at the summary later" usage.

## Future Revisit Conditions

- **Before building memory on top of this executor**: add step-to-step
  data flow (write a step's result into `Plan.context`, make it
  available to later steps' descriptions/verification) — the single
  largest gap identified by Opus Checkpoint #3's review, and the exact
  seam a memory layer would need.
- A real plan needs two independent branches (e.g. "lint the frontend"
  and "lint the backend" with no ordering dependency between them) —
  revisit `depends_on` and parallel execution then, with a concrete case
  in hand rather than speculatively.
- The degraded-task-upgrade gap in `OpenCodeSupervisor.reconcile_on_
  startup()` causes a real, observed loss of a completed-but-unreported
  plan step — worth fixing at the `OpenCodeSupervisor` layer (so every
  consumer benefits, not just the plan executor) rather than working
  around it here.
- Escalations become frequent enough that manual reconnection feels like
  friction rather than an acceptable v1 simplification — revisit an
  automatic-resume mechanism then.
- Plans start repeating the same shape often enough that ad-hoc
  `create_plan` calls become repetitive — revisit a lightweight template
  mechanism then, not before.

## References

- `CLAUDE.md` (Deterministic interruption policy — ADR-003; Evidence
  over inference — ADR-010; no over-engineering; ADR-driven architecture
  rule).
- `docs/decisions/ADR-005-worker-supervisor-architecture.md` (Supervisor
  routes, workers execute — the executor is a background consumer of
  workers, not a second router).
- `docs/decisions/ADR-026-worker-registry-and-strategist-consultation.md`
  (`Worker`/`WorkerRegistry`/`WorkerResult` — the abstraction this ADR
  builds on top of, including why `OpenCodeWorker.invoke()` always
  returns `DISPATCHED`).
- `docs/decisions/ADR-024-sqlite-write-concurrency-model.md` (the
  conditional-UPDATE-by-rowcount pattern `claim_plan_start()` and
  `transition_attention_status()` both use).
- F1.11 migration framework (`app/migrations.py`) — plan/plan_steps
  tables added as migration 2.

## Related Milestones

Month 1, Weeks 3-4 of the post-F1 feature roadmap (plan executor),
Steps 0-6.

## Related Source Files

- `app/plan_executor.py` (new — `PlanExecutor`)
- `app/database.py` / `app/db_async.py` (plan/plan_step CRUD:
  `create_plan_record`, `create_plan_step_record`, `get_plan`,
  `get_plan_steps`, `get_plan_step`, `get_recent_plans`,
  `get_plans_by_status`, `update_plan_status`, `update_plan_step`,
  `claim_plan_start`)
- `app/migrations.py` (migration 2 — `plans`/`plan_steps` tables)
- `app/config.py` (`plan_step_timeout_seconds`)
- `app/supervisor/tools.py` (`create_plan`, `start_plan`, `plan_status`,
  `resume_plan`, `list_plans`)
- `app/supervisor/supervisor.py` (`plan_executor` constructor param)
- `app/main.py` (`PlanExecutor` construction, `lifespan()` wiring)
