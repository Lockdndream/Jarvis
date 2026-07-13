# ADR-005: Worker / Supervisor Architecture

## Status

Accepted

## Date

2026-07-09 (Milestone 5, Supervisor introduced), reinforced 2026-07-09
(Milestone 6, verified-evidence state machine)

## Context

Milestone 4 gave Jarvis the ability to supervise OpenCode sessions
mechanically (start, monitor, answer questions). Milestone 5 added a
conversational layer — free-text WebSocket messages needed to be routed
somewhere and turned into action. The natural, simplest design would let
the conversational LLM directly execute file/shell operations itself, the
same way a coding-agent CLI does. Jarvis deliberately did not build it
that way.

## Problem

Should the conversational LLM (the `Supervisor`) be allowed to execute
work directly (run shell commands, edit files), or should it be
restricted to *delegating* work to separate worker processes it
supervises but does not become?

## Decision

**The Supervisor routes; workers execute.** `app/supervisor/supervisor.py`
owns a bounded `ToolRegistry` (`app/supervisor/tools.py`) — a fixed set of
tools the LLM can call (start an OpenCode task by safe project alias,
relay a follow-up, answer a question, resolve a permission, cancel a
task, report status). Every tool, without exception, delegates back to
`TaskManager` or `OpenCodeSupervisor` to actually perform work. The
Supervisor itself never opens a file, never runs a shell command, and
never establishes its own connection to do work — its authority is
limited to *deciding which bounded tool to call*, not to acting directly.

Delegated work itself is bounded further by **safe project aliases**
(`app/supervisor/projects.py`, `projects.json`) — the LLM never receives
or reasons about raw filesystem paths, only named, pre-registered
aliases scoped to specific directories.

Verification of delegated work follows ADR-010 (Evidence-Based
Engineering): a delegated task's completion is never inferred from the
LLM's own prose describing what it did — it requires the same native,
verified evidence any other worker completion requires.

## Alternatives Considered

**Let the Supervisor execute directly (function-calling into real
filesystem/shell operations).** Rejected: this is architecturally
identical to *becoming* OpenCode rather than supervising it, and directly
violates ADR-001's "clients (and, by extension, any single component)
never become brains that also do unbounded work" principle applied at
the component level rather than the device level. It would also remove
the evidence-based verification boundary ADR-010 depends on — there
would be no separate, independently-observable "did the work actually
happen" check if the same component both decided and executed.

**A single merged Supervisor+Worker process instead of a supervised
subprocess model.** Rejected: OpenCode's own REST+SSE server model
(chosen for other reasons — see the OpenCode integration decision
documented in `SESSION.md`) already provides isolation and independent
observability that a merged process would give up for no benefit.

**Unbounded tool access instead of a fixed `ToolRegistry`.** Considered
implicitly and rejected — an LLM with unrestricted tool access
(arbitrary shell execution, arbitrary file paths) reintroduces the exact
risk this decision exists to bound. The registry is deliberately small
and named, not a generic "run this" escape hatch.

## Consequences

Any new capability the Supervisor should be able to trigger must be added
as a new, named, bounded tool in `ToolRegistry` — never as a general
escape hatch. This has held through every subsequent milestone; the
attention/voice tool additions in Milestone 8
(`test_attention_supervisor_tools.py`) followed the same pattern rather
than introducing a new execution path.

## Positive Outcomes

- A small, auditable surface for "what can actually change the real
  world" — every tool call is logged and every effect traces back to
  `TaskManager` or `OpenCodeSupervisor`, not to opaque LLM-decided
  actions.
- Delegation to OpenCode (Milestone 9A/9B.0's real delegated sessions)
  reuses this exact same bounded pattern rather than requiring new
  Supervisor logic — `OpenCodeSupervisor.start_session()` with a
  project-alias directory is the same shape of delegation the Supervisor
  itself already performs.
- Real bugs were caught specifically because tool-call boundaries are
  explicit and testable (e.g. the M5 closure's `task_id`/`question_id`
  truncation bug, found because a tool call's exact-match lookup failed
  in a reproducible, boundary-specific way).

## Tradeoffs

- Every new capability requires deliberate registry work, not emergent
  LLM tool discovery — a real development-velocity cost accepted in
  exchange for the bounded-surface guarantee.
- The Supervisor cannot improvise a novel action outside its registered
  tools even when a human would consider it an obviously reasonable
  thing to do — by design, not an oversight.

## Future Revisit Conditions

Revisit only if a new category of work emerges that genuinely cannot be
expressed as a bounded tool delegating to a supervised worker (no such
category has arisen through Milestone 9B.0) — and even then, the correct
response is likely a new tool, not a loosening of the boundary itself.

## References

- `ARCHITECTURE.md` §2 (Design Principles — "The Supervisor owns
  reasoning; workers execute"), §4 (Supervisor, TaskManager, OpenCode
  integration, Projects)
- `SESSION.md`, Milestone 5 (Supervisor introduced), Milestone 5
  Validation Closure (real bugs found at tool-call boundaries), Milestone
  6 (verified-evidence worker state machine), Architecture Decision #5

## Related Milestones

Milestone 4, Milestone 5, Milestone 6

## Related Source Files

- `app/supervisor/supervisor.py`
- `app/supervisor/tools.py`
- `app/supervisor/projects.py`
- `app/task_manager.py`
- `app/integrations/opencode_supervisor.py`
