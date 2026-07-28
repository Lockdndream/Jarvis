# Milestone F1 — Foundation Sprint

**Inserted before**: Capability Level 5 (resumed)
**Duration**: 4 weeks
**Rationale**: Both the Principal Engineer Review (2026-07-28) and the Architecture Audit (2026-07-28) independently converge on the same conclusion — the next milestone must be infrastructure, not features. Four verified defects are shipping undetected beneath 23 ADRs and a 653-test suite. The rigor is real; it is enforced by memory, not by tooling. This sprint makes the existing governance executable.

**Orchestrator verification rule**: Every phase requires evidence artifacts (CI logs, test output, diff links) before sign-off. Clean self-reports with 100% pass rates will be challenged, not accepted.

---

## Phase 1 — CI, Packaging, and Static Analysis (Week 1)

### Goal
Every future commit is checked by a machine, not a human reading an ADR.

### Tasks

**F1.1 — pyproject.toml + packaging**
- Create `pyproject.toml` with project metadata, dependencies, and entry points.
- All 38 env vars documented in one place (a config section or a dedicated `config.py` — see F1.12).
- The project must be installable with `pip install -e .`

**F1.2 — Linter: ruff**
- Add `ruff` config to `pyproject.toml`.
- Fix or `noqa`-annotate all existing violations.
- Acceptance: `ruff check .` exits 0 on the current codebase.

**F1.3 — Type checker: mypy**
- Add `mypy` config (start with `--ignore-missing-imports`, gradual strictness).
- Annotate at minimum: `database.py`, `supervisor.py`, `tools.py`, `connection_manager.py`, `interruption_policy.py`.
- Acceptance: `mypy app/` exits 0 on the configured scope.

**F1.4 — CI pipeline**
- GitHub Actions (or equivalent) running on every push and PR to `develop` and `main`.
- Steps: `ruff check`, `mypy`, `pytest tests/ -q --timeout=120`.
- Acceptance: a green CI run on the current codebase (with the known order-dependent failure either fixed here or explicitly `xfail`'d with a ticket reference — not silently skipped).

**F1.5 — Delete spike/production duplication**
- Remove byte-identical copies: `hey_jarvis.tflite`, `MicroWakeWordEngine.cpp`, `MicroFrontendWrapper.cpp`, `CMakeLists.txt` from `spikes/android-wakeword/`.
- Verify `android/` copies are canonical.
- Acceptance: `diff` confirms no divergence; spike directory retains only its README or is removed.

### Phase 1 evidence required
- CI passing on a real push (screenshot or log link, not a self-report).
- `ruff check .` and `mypy app/` output.
- `pip install -e .` in a fresh venv succeeds.

---

## Phase 2 — Fix Live Defects and Test Suite (Week 2)

### Goal
The two verified correctness bugs stop shipping. The test suite becomes trustworthy.

### Tasks

**F1.6 — Fix the malformed LLM message array (TD-028, Principal Engineer Review §3.1)**
- `supervisor.py:259-275`: the current message is sent twice and precedes the history.
- Correct ordering: `[system] → [*history] → [user: context_preamble] → [user: current_message]` (or put context in the system message — either is acceptable, document the choice).
- **Required regression test**: assert the message array shape, not just the response. The test must verify ordering and non-duplication of the current turn. This is the test that should have existed since Milestone 5.
- Acceptance: new test passes; no existing test breaks.

**F1.7 — Fix observer contamination of InterruptionPolicy (TD-029, Principal Engineer Review §3.2)**
- Root cause: `ConnectionManager` has no public concept of "user-facing surface connected." `_connected()` reads `len(_connections)` which includes observers.
- Fix: add `has_user_surfaces()` (or equivalent) that excludes observer connections. Wire `attention_manager._connected()` through this method. Make `_connections` private in fact (no external reads of the raw dict).
- **Required regression test**: "registering an observer does not change InterruptionPolicy's decision." Specifically: with no phone/PWA connected, adding a dashboard observer must leave `connected=False` in the policy input.
- Acceptance: new test passes; the ADR-018 invariant is now machine-verified.

**F1.8 — Fix the order-dependent test failure (TD-032, Principal Engineer Review §3.3)**
- `test_operations_api.py::test_start_failure_reported_as_failed_not_running` fails in the full suite, passes in isolation.
- Root cause is module-global contamination. Identify which global(s) leak and fix the isolation — either by proper teardown fixtures or by removing the module-global dependency.
- Do NOT paper over it with broader `conftest.py` resets that mask the problem. The fix must address the actual contamination source.
- Acceptance: `pytest tests/ -q` passes with 654/654 (or whatever the count becomes after new tests are added). The `xfail` from Phase 1 (if used) is removed.

**F1.9 — Reduce test suite runtime**
- Current: 18m55s. Target: under 5 minutes.
- Profile with `--durations=20`. Likely culprits: fixture setup, real sleeps, unneeded DB I/O.
- Acceptance: `pytest tests/ -q --durations=10` completes in under 5 minutes. No tests deleted — only made faster.

**F1.10 — Fix the circular import inconsistency (TD-030 partial, Principal Engineer Review §3.4)**
- `tools.py:322` does `from app.main import voice_session_manager` inside a function.
- The project already knows the answer: `operations.py` documents avoiding this by using injection.
- Fix: inject `voice_session_manager` into `ToolRegistry` at construction, same pattern as `task_manager` and `opencode_supervisor`.
- Also fix `tools.py:318` (`self._oc.cm` reach-through): inject `connection_manager` directly.
- Acceptance: no function-level imports from `app.main` remain in `app/supervisor/tools.py`. The `from app.main import` grep on the tool layer returns nothing.

### Phase 2 evidence required
- Full `pytest` output showing all tests passing, with runtime.
- Diff of the message-array fix showing before/after ordering.
- The specific new test for message-array shape, pasted or linked.
- The specific new test for observer/policy isolation, pasted or linked.
- CI green after all fixes.

---

## Phase 3 — Database Layer and Schema Versioning (Weeks 3–4)

### Goal
The persistence layer supports transactions, enforces foreign keys, has versioned migrations, and doesn't block the event loop.

### Tasks

**F1.11 — Schema versioning and migration framework (TD-027 partial)**
- Add a `schema_version` table (or equivalent mechanism — Alembic is acceptable but not required; a simple numbered-migration runner is fine).
- Codify the current schema as migration 001.
- Acceptance: starting from an empty DB, migrations produce the current schema. Starting from the current DB, migrations are a no-op. `_ensure_column` is no longer the migration mechanism for new work.

**F1.12 — Config layer (TD-034)**
- Centralize the 38 env vars into a single config module with validation, defaults, and documentation.
- No env var should be read via bare `os.environ.get()` at an arbitrary call site after this.
- Vars frozen at import time must be explicitly documented as such (or made dynamically readable if that's cheap).
- Acceptance: `grep -rn "os.environ" app/` returns only the config module (or a documented, justified exception). `.env.example` covers all 38 vars.

**F1.13 — Enable foreign key enforcement (TD-027 partial)**
- Add `PRAGMA foreign_keys = ON` to every connection (or centralize connection creation so it's set once).
- Verify no orphaned rows exist in production DB. If they do, clean them up in a migration.
- Acceptance: attempting to insert a row with a nonexistent FK parent raises an error. A test verifies this.

**F1.14 — Transaction boundaries (TD-027 partial)**
- Identify all multi-step operations that should be atomic. At minimum: `VoiceSessionManager.open_session()` (the four-write sequence from Principal Engineer Review §3.7 where a failure between lease-claim and session-create leaves a permanently un-bindable AttentionRequest).
- Wrap them in explicit transactions.
- Acceptance: a test simulates a failure mid-sequence and verifies rollback (no orphaned lease, no half-written state).

**F1.15 — Async DB access (TD-035, Architecture Audit Finding 3a)**
- Route `database.py` calls through `asyncio.to_thread` or migrate to `aiosqlite`, behind the existing function boundary.
- Call sites should not change (or change minimally — adding `await` is acceptable).
- Acceptance: no blocking `sqlite3` call is made directly from an `async def` handler. A grep or mypy check can verify this.

**F1.16 — Write-concurrency discipline (Architecture Audit Finding 3b)**
- Set `busy_timeout` on all connections.
- Document the write-concurrency model (single-writer queue, or busy-timeout + retry, or WAL with documented contention budget).
- Acceptance: the chosen model is documented in an ADR or ADR amendment. `busy_timeout` is set.

**F1.17 — ADR namespace collision cleanup (TD-033)**
- `ARCHITECTURE.md` §16 runs a second, colliding ADR namespace (its ADR-004, 005, 006, 007, 010 conflict with `docs/decisions/`).
- Fix: renumber the inline references, or convert them to explicit cross-references to the canonical ADRs, or remove the duplicates.
- Acceptance: `grep -rn "ADR-0" docs/ ARCHITECTURE.md` shows no collisions. The debt register entry is closed.

### Phase 3 evidence required
- Migration framework running from empty DB → current schema (output log).
- Migration framework running on existing DB → no-op (output log).
- FK enforcement test output.
- Transaction rollback test output.
- Grep showing no bare `os.environ.get` outside config module.
- CI green after all changes.
- ADR or ADR amendment for the write-concurrency model.

---

## Exit Criteria for Milestone F1

All of the following must be true before F1 is marked complete:

1. CI runs on every push and is currently green.
2. `ruff check .` exits 0.
3. `mypy app/` exits 0 on configured scope.
4. Full test suite passes (0 failures, 0 xfails that aren't already tracked).
5. Test suite completes in under 5 minutes.
6. The malformed message array is fixed, with a shape-asserting regression test.
7. Observer connections do not contaminate InterruptionPolicy, with a regression test.
8. No function-level imports from `app.main` in the tool layer.
9. Schema versioning exists and the current schema is migration 001.
10. Foreign keys are enforced.
11. Multi-step DB operations use explicit transactions.
12. DB access is async (no blocking sqlite3 on the event loop).
13. `busy_timeout` is set; write-concurrency model is documented.
14. All env vars are centralized in a config module.
15. ADR namespace collision is resolved.
16. Spike/production file duplication is eliminated.

---

## What This Sprint Does NOT Cover

These are explicitly deferred to post-F1 milestones. Listing them here to prevent scope creep:

- Memory subsystem (Architecture Audit Finding 1) — post-F1, pre-Supervisor Intelligence.
- Plan/goal layer (Architecture Audit Finding 2) — post-F1, pre-Supervisor Intelligence.
- Per-message WS error isolation (Architecture Audit Finding 5) — first task after F1.
- Worker/LLM registry seam (Architecture Audit Finding 4) — design during Supervisor Intelligence, not before.
- Plugin readiness (Architecture Audit Finding 7) — deferred until Plugins is scheduled.
- Operations subsystem reduction — not touching it during F1; the boundary is correct even if the scale is debatable.
- WebSocket protocol redesign (Principal Engineer Review §4.2) — deferred; the `client_request_id` gap is real but not blocking.
- `main.py` decomposition (Principal Engineer Review §4.1) — worth doing but not in F1; risk of a large refactor conflicting with the DB layer work.

---

## Sequencing After F1

Once F1 exits clean:

1. **Per-message WS error isolation** (Architecture Audit Finding 5, hours of work, directly protects availability).
2. **Memory subsystem v1** (Architecture Audit Finding 1, highest-value new capability).
3. **Plan/goal layer** (Architecture Audit Finding 2, prerequisite for Supervisor Intelligence).
4. **Resume Capability Level 5** with the foundation in place.

---

*This plan was drafted by the orchestrator based on the Principal Engineer Review (2026-07-28) and the Architecture Audit (2026-07-28). It should be treated as a directive for the coding agent, not a suggestion.*
