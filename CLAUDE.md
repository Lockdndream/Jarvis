# CLAUDE.md — Jarvis Project Instructions

## What This Project Is

Jarvis is a local-first personal AI assistant. Laptop is the brain, Android is the interface. The codebase is ~18,000 lines of Python (backend) + Kotlin (Android) + JS (dashboard), governed by 23 ADRs and a technical debt register.

## Current State

A Principal Engineer Review (2026-07-28) and an Architecture Audit (2026-07-28) both found that the governance is excellent but the tooling enforcement is absent. Four verified defects are shipping undetected. The test suite takes 19 minutes and has an order-dependent failure. There is no CI, no linter, no type checker, no packaging.

**The active milestone is F1 (Foundation Sprint)** — see `docs/MILESTONE_F1_FOUNDATION_SPRINT.md` for the full plan. Nothing on the capability roadmap proceeds until F1 exits clean.

## Key Files

- `docs/MILESTONE_F1_FOUNDATION_SPRINT.md` — the current milestone plan (16 tasks, 3 phases)
- `docs/PRINCIPAL_ENGINEER_REVIEW.md` — the review that found the four defects
- `docs/ARCHITECTURE_AUDIT_2026-07-28.md` — the architecture audit
- `HANDOVER.md` — project history and context
- `docs/decisions/` — canonical ADR directory (ADR-001 through ADR-023)
- `docs/TECHNICAL_DEBT.md` — the debt register
- `ARCHITECTURE.md` — architecture doc (note: §16 has a colliding ADR namespace that F1.17 fixes)

## Architectural Invariants (Do Not Violate)

1. **Laptop is the brain** (ADR-001). All reasoning, state, and persistence stay on the laptop.
2. **Supervisor routes, workers execute** (ADR-005). The Supervisor never opens a file or runs a shell command.
3. **Read-only Control Center** (ADR-018/019). The dashboard observes; it never writes. Observer connections must not affect phone behavior.
4. **Deterministic interruption policy** (ADR-003). No LLM in the safety path.
5. **Evidence over inference** (ADR-010). Don't mark things completed on silence.
6. **Local-first** — no cloud dependencies for core operation.

## The Four Verified Defects (Fix in F1 Phase 2)

1. **Malformed LLM message array** — `supervisor.py:259-275` sends the current message twice and puts it before history. Fix: correct the ordering. Add a test that asserts message array shape, not just response content.
2. **Observer contamination of InterruptionPolicy** — `ConnectionManager` has no `has_user_surfaces()`. Dashboard connections make `connected=True` when no phone is attached, changing URGENT item behavior. Fix: add `has_user_surfaces()` excluding observers. Add a regression test.
3. **Order-dependent test failure** — `test_operations_api.py::test_start_failure_reported_as_failed_not_running` fails in the full suite due to module-global contamination. Fix the isolation, don't paper over it.
4. **Circular import in tools.py** — `from app.main import voice_session_manager` inside a function. The project already uses injection elsewhere (`operations.py`). Apply the same pattern.

## Development Rules

- **No over-engineering.** Redis, Celery, Docker, frontend frameworks are out of scope.
- **Prefer additive changes** over rewrites.
- **ADR-driven.** New architectural decisions get an ADR before implementation.
- **Evidence before assumptions.** Test on real devices when possible.
- **Every fix needs a regression test.** If a defect had no test, the fix includes one.

## Model Routing (Subagents)

This project uses Claude Code as the orchestrator with OpenCode Go models for implementation subagents:

- **Main session (Claude):** orchestration, judgment, code review, architectural decisions
- **DeepSeek V4 Pro:** complex implementation — DB migration framework, transaction boundaries, async conversion
- **Kimi K2.7 Code / Qwen3.7 Max:** standard implementation — ruff/mypy config, test fixes, config layer, straightforward code changes
- **DeepSeek V4 Flash:** read-only tasks — grep audits, file reading, verification checks, finding contamination sources

## Verification Requirements

Every completed phase requires evidence artifacts, not self-reports:
- CI logs (real push, not local run)
- Full `pytest` output with pass count and runtime
- `ruff check .` output
- `mypy app/` output
- Diffs of changed files
- New test source code (pasted, not summarized)

Do not accept a subagent's "all tests pass" claim without seeing the output.
