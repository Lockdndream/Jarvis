# Jarvis — Principal Engineer Technical Due Diligence

**Date**: 2026-07-28
**Branch reviewed**: `develop` @ `b115c13` + uncommitted working tree
**Scope**: full codebase, all 23 ADRs, `ARCHITECTURE.md`, `docs/TECHNICAL_DEBT.md`, the test suite (executed), the Android companion, the frontends.

**Epistemic labels used throughout**: `[VERIFIED]` = I read the code or ran the command. `[PREDICTION]` = inference about future behavior, stated as such. `[JUDGMENT]` = an engineering opinion where reasonable people could differ.

---

## 0. Verification performed

| What | Result |
|---|---|
| `python -m pytest tests -q` | **653 passed, 1 failed, 18m55s** |
| `tests/test_operations_api.py::test_start_failure_reported_as_failed_not_running` in isolation | **passes in 1.67s** → the full-suite failure is **order-dependent** |
| 5 core test files, `--durations=10` | 193 passed in 288s; slowest entries are *fixture setup*, ~2s each |
| Repo size | 17,813 lines non-test Python/Kotlin/JS; 13,294 lines of tracked Markdown; `SESSION.md` alone is 435 KB on disk |
| Tooling present | **No** `pyproject.toml`, `setup.py`, CI config, linter config, formatter config, type-checker config, pre-commit config, or lockfile |
| Spike/production binary duplication | `hey_jarvis.tflite`, `MicroWakeWordEngine.cpp`, `MicroFrontendWrapper.cpp`, `CMakeLists.txt` are **byte-identical** between `spikes/android-wakeword/` and `android/` |

---

## 1. The headline

You have built the governance apparatus of a 100,000-line, multi-contributor system on top of an 18,000-line single-user prototype — and **nothing executable enforces any of it**.

23 ADRs. A 669-line technical debt register with a severity taxonomy. A 1,145-line architecture document with a self-review section. A structured-logging schema specified field-by-field. A trace-correlation model with its own ADR. Every one of those is genuinely good work, and most of it will age well.

And: no CI. No linter. No type checker. No packaging. No schema migration mechanism beyond `ALTER TABLE ADD COLUMN`. A test suite that takes 19 minutes and has an order-dependent failure right now. A `Supervisor` that builds a malformed message array before every LLM call and has done so, undetected, since Milestone 5.

The discipline is aimed at the wrong layer. It is aimed at *decisions* — which are cheap to revisit — and not at *invariants* — which are expensive to violate silently. Every ADR in `docs/decisions/` is a promise enforced by human memory. That works at one contributor. It does not survive the second.

**This is the thing that will hurt you in two years.** Not SQLite. Not the LLM provider. The fact that "the Control Center never affects phone behavior" is a load-bearing invariant declared in ADR-018 and **it is currently violated** (§3.2 below), the tests pass, and nobody knew.

---

## 2. What I would keep untouched

Stated first so the rest is read correctly. These are genuinely good and I would defend them:

- **ADR-001 / ADR-008 — laptop is the brain, clients are surfaces.** This is the single best decision in the project. It is the reason the Android companion, the widget, and the wake word could be added without touching the reasoning core. It will age better than anything else here.
- **ADR-003 — deterministic interruption policy, no LLM in the safety path.** Correct, and unusually disciplined. Do not revisit this even when it becomes tempting.
- **ADR-010 — evidence over inference.** The `session.idle`/`session.error` evidence requirement, the refusal to mark a task `completed` on silence, the `degraded` state on restart. This is the mark of someone who has been burned and learned the right lesson.
- **ADR-004 — OpenCode storage + credential isolation.** Both incidents were real, both fixes are correct, and the `_OS_ESSENTIAL_ENV_VARS` allowlist is the right shape.
- **ADR-020 — `trace_id` via `ContextVar`, persisted on the row for the async leg.** The best-engineered of the recent batch. The reasoning about why SSE handlers read `trace_id` from the row rather than a ContextVar is exactly right.
- **The idempotency-by-constraint pattern.** `dedup_key UNIQUE` + `INSERT OR IGNORE`, conditional `UPDATE ... WHERE status IN (...)`. Correct concurrency primitives, correctly applied.
- **The `spikes/` discipline** — build a disposable thing, get real device evidence, classify it DISPOSABLE. Keep doing this.

---

## 3. Verified defects and violated invariants

### 3.1 The Supervisor sends a malformed message array to the LLM on every turn `[VERIFIED]`

`app/supervisor/supervisor.py:259-275`:

```python
history = _load_conversation(conversation_id, max_turns=10)
context = build_context(history)
messages = [
    {"role": "system",  "content": SYSTEM_PROMPT},
    {"role": "user",    "content": _format_context(context) + "\n\nUser: " + user_message},
]
for h in history:
    messages.append({"role": h["role"], "content": h["content"]})
messages.append({"role": "user", "content": user_message})
```

The resulting sequence is:

```
[system] → [user: context + CURRENT MESSAGE] → [...history...] → [user: CURRENT MESSAGE]
```

The current turn's message is **sent twice**, and the **first copy precedes the conversation history it is supposed to follow**. From the model's perspective the user asks the question, then ten turns of unrelated history happen, then the user asks the identical question again.

**Consequences**: degraded multi-turn reasoning (the model sees scrambled chronology), and a plausible contributor to any "Jarvis forgets context" behaviour observed during capability testing. Note the cost here is **latency and reasoning quality, not money** — `JARVIS_LLM_FREE_ONLY=true` and the configured model is free, so the duplicated input tokens cost nothing billable. That is not a reason to leave it; it is a reason not to dismiss it as a cost issue when it is a correctness issue.

**Why no test caught it**: `FakeLLMProvider.chat_completion()` records `messages` but every assertion in `tests/test_supervisor.py` checks the *response*, never the *ordering of the prompt*. 92 tests in that file; none assert message sequence.

**Fix**: `[system, *history, user(context_preamble), user(current_message)]` — or better, put the context blob in the system message where it belongs. Two lines. Add a test that asserts the message array shape.

This is a two-line bug that has been shipping since Milestone 5 in the most important function in the product. It is the strongest single piece of evidence for the §1 thesis: the review discipline is pointed at decisions, not at code.

---

### 3.2 ADR-018's "single load-bearing invariant" is violated — opening the dashboard changes phone behaviour `[VERIFIED]`

ADR-018 Contract 2 states dashboard clients never affect phone/PWA behavior, and calls the separation "the single load-bearing invariant of the whole design."

The chain:

1. `connection_manager.py:57-64` — `connect()` adds **every** WebSocket to `self._connections`.
2. `connection_manager.py:79` — `mark_observer()` adds the dashboard to `self._observers` **in addition**; it is never removed from `_connections`.
3. `attention_manager.py:117-121` — `_connected(conn_manager)` returns `len(conn_manager._connections) > 0`.
4. `interruption_policy.py:56` — that boolean is passed as `connected=` into the policy decision.

So **a Control Center tab open on the laptop makes `connected=True` even when no phone and no PWA is connected.** Concretely, from `interruption_policy.decide()`:

- **URGENT question/permission, on the first contact attempt**: `connected=False` → `ACTION_PUSH`. `connected=True` → `ACTION_VOICE_WHEN_AVAILABLE` → `VoiceInvitationChannel`, which calls `conn_manager.broadcast(...)` — the **general** fan-out — emitting a `voice_session_invitation` frame to every phone/PWA connection and triggering the call-style UI. A dashboard being open flips an urgent item's first contact from a silent push into an active voice invitation on the phone.

  `[VERIFIED]` that `prior_contact_count == 0` genuinely holds on this path: `database.py:814-848`'s `create_attention_request()` inserts `contact_attempt_count = 0` and returns the full row; `attention_manager.get_or_create()` passes that same row straight into `initiate_contact()`, which reads `attention_row.get("contact_attempt_count", 0)` — all of it *before* `record_attention_contact()` ever increments. So on a freshly created URGENT request this branch is reached. On second and later attempts the decision degrades to `ACTION_IN_APP`, and the contamination is bookkeeping-only.
- **NORMAL urgency, second+ contact, phone offline**: `connected=False` → `ACTION_SILENT` (no contact, schedule a retry). `connected=True` → `ACTION_IN_APP` → a `ContactAttempt` row is written, `record_attention_contact()` increments the counter, and the retry cadence changes.

**Correction to my own first read**: this is *not* "the user never gets notified." `notifications.notify()` (`app/notifications.py:94-100`) calls `push.send_push_to_all()` unconditionally, so both `InAppChannel` and `PushChannel` end up pushing. The damage is to **policy inputs, contact bookkeeping, and the URGENT voice-invitation path** — not to push delivery.

It is still a direct violation of the invariant ADR-018 declares inviolable, it is invisible in the test suite, and it was introduced by *composition* — nobody wrote a bad line; two correct modules were connected through a leaky accessor.

**Root cause worth naming separately**: `_connected()` reaches into `conn_manager._connections` — a **private** attribute of another module. `ConnectionManager` has no public concept of "is a user-facing surface connected." That missing abstraction is what made the bug possible.

**Fix**: give `ConnectionManager` a public `has_user_surfaces()` that excludes observers; make `_connections` private in fact as well as in name. Add a regression test: *"registering an observer does not change `InterruptionPolicy`'s decision."*

---

### 3.3 A test fails only in the full suite `[VERIFIED]`

`tests/test_operations_api.py::test_start_failure_reported_as_failed_not_running` fails in `pytest tests` and passes in isolation. This is a **verified instance** of the module-global contamination problem, not a theoretical one. Candidate culprits (all module-level mutable globals): `operations._opencode_supervisor`, `operations._connection_manager`, `supervisor_module._broadcast_hook`, `supervisor_module._active_turns`, `voice_session_manager_module._broadcast_hook`, `db.DB_PATH`, `projects` module state.

`tests/conftest.py` already exists solely to paper over two of these globals, and its own docstring admits the contamination it prevents "causes no visible failure today." That is no longer true.

---

### 3.4 Circular import, resolved by a function-local import `[VERIFIED]`

`app/supervisor/tools.py:322, 327`:

```python
async def _open_voice_session(self, conversation_id, attention_request_id=None):
    from app.main import voice_session_manager
```

The dependency graph is `app.main → supervisor → tools → app.main`. The import is inside the function purely to defer the cycle to call time. `app/operations.py` explicitly documents avoiding exactly this ("It does not import from app.main (that would be circular)") and uses injection instead — so the project already knows the right answer and applied it inconsistently.

---

### 3.5 `_resume_attention` reaches through `OpenCodeSupervisor` for the ConnectionManager, unguarded `[VERIFIED]`

`app/supervisor/tools.py:318`:

```python
await attention_manager.initiate_contact(self._oc.cm, fresh)
```

`OpenCodeSupervisor.__init__` does set `self.cm = connection_manager` (`opencode_supervisor.py:68`), so this works — but:

- Every other `_oc`-touching tool guards with `if not self._oc: return "Error: ..."`. This one does not → `AttributeError` when OpenCode is unwired.
- The **dependency direction is wrong**: the tool registry obtains the connection manager by reaching two levels through an unrelated subsystem. That is the smell, independent of the crash.

---

### 3.6 Stringly-typed cross-module error contract `[VERIFIED]`

`ToolRegistry.call()` returns `str` for everything, including failures (`"Error: unknown tool 'x'"`, `"Error executing 'x': ..."`). Callers branch on prose:

```python
if result.startswith("Error"):   # supervisor.py:592, 601, 607, 641, 655, 668, 680
```

Any tool that legitimately returns text beginning with "Error" is misclassified. Any future tool author who writes `"Failed to ..."` silently breaks the contract with no compiler, linter, or test to catch it. This is the **exact interface a plugin system would have to be built on**, and it is unsafe for that.

---

### 3.7 Data-integrity gaps in the persistence layer `[VERIFIED]`

- **Foreign keys are never enforced.** `PRAGMA foreign_keys` is never set (SQLite defaults to OFF). `opencode_tasks.task_id TEXT UNIQUE NOT NULL REFERENCES tasks(task_id)` (`database.py:423`) is decorative. Orphaned rows are possible and undetected.
- **No transaction boundaries.** Every function in `database.py` is `get_conn()` → execute → `commit()` → `close()`. Multi-step operations are non-atomic. `VoiceSessionManager.open_session()` performs **four separate committed writes** (claim lease, create session, → OPENING, → LISTENING). If `create_voice_session()` raises after `try_claim_voice_session_lease()` succeeds, the lease is held by a `voice_session_id` that **has no row** — and `VoiceSessionReaper` only reaps rows that exist, so that `AttentionRequest` is permanently un-bindable. Narrow window, unbounded consequence.
- **No schema versioning.** `_ensure_column()` supports additive columns only. There is no `schema_version` table and no down-migration or table-rewrite path. **TD-006** (split permissions out of `questions`) is therefore not just unscheduled — it is *unimplementable* without writing a migration framework first. Same for any future `NOT NULL`, type change, or index redesign.
- **`DB_PATH = "jarvis.db"`** is relative to the process working directory. Start Jarvis from the wrong `cwd` and it silently creates a fresh, empty database.

---

### 3.8 Synchronous SQLite on the asyncio event loop `[VERIFIED code path, PREDICTION on impact]`

`[VERIFIED]`: every `database.py` function is blocking `sqlite3`, opening a fresh connection per call, and is called **directly** from `async def` handlers — `supervisor._broadcast()` calls `db.save_event()`, `main.py`'s `/ws` loop calls `get_recent_events(100)`, `AttentionManager._transition()` calls into `db` from async code. Only two places in the entire codebase offload: `executor.py:33` (`run_in_executor`) and `push.py:83` (`asyncio.to_thread`).

`[PREDICTION]`: at today's scale — one user, one phone, one dashboard — this is invisible and the right call. It stops being invisible the moment any of these lands: background planning loops, multiple concurrent workers, a second user, or an event/task table large enough that a `SELECT ... ORDER BY id DESC LIMIT 200` takes tens of milliseconds. `GET /api/dashboard/snapshot` alone issues ~8 blocking queries including `get_recent_events(200)` — every one of them stalls the loop that is simultaneously serving the phone's voice turn.

I am **not** recommending you rip out SQLite. SQLite is the right database for this system and probably will be in five years. I am recommending you put a boundary in front of it (§8, item 4) *before* you build anything concurrent on top.

---

### 3.9 Smaller verified items

| Item | Location | Note |
|---|---|---|
| `get_latest_device_status()` docstring lies | `connection_manager.py:107-116` | `next(iter(dict.values()))` on an insertion-ordered dict returns the **first-connected** device, not "most recently received" as documented. Harmless today (one phone); wrong the moment there are two. |
| `ConnectionManager.broadcast()` is a serial `await` loop | `connection_manager.py:141-149` | One slow/half-open client delays delivery to every other client. No timeout, no per-connection queue. |
| `InAppChannel` and `PushChannel` are behaviourally near-identical | `contact_channels.py:75-115` + `notifications.py:94` | Both call `_deliver_via_notifications()` → `notify()` → `send_push_to_all()` unconditionally. `InterruptionPolicy` carefully decides IN_APP vs PUSH and the channel layer then does approximately the same thing. ADR-006's abstraction currently abstracts nothing. |
| ~38 environment variables, no config layer | grep across `app/` | Read ad-hoc via `os.environ.get()` at scattered call sites with inline defaults. Some are frozen at **import** time (`interruption_policy.DEFAULT_RETRY_MINUTES`, `LLMProvider.__init__`). `.env.example` documents 8 of 38. There is no single place to see, validate, or document the deployment surface. |
| Free-only cost guard is suffix matching | `llm.py:44` — `model.endswith(":free")` | The entire real-money boundary is a string suffix convention owned by OpenRouter, not by you. If they rename, the guard silently opens. |
| Three independent DB polling loops | `opencode_supervisor._poll_loop` (3s), `attention_scheduler` (15s), `voice_session_reaper` | The 3s loop queries the DB forever regardless of whether any task is running. |

---

## 4. Architecture

### 4.1 `app/main.py` is the god module `[JUDGMENT]`

770 lines containing: the DI container (module-level construction of `ConnectionManager`, `TaskManager`, `OpenCodeSupervisor`, `Executor`, `Supervisor`, `AttentionScheduler`, `VoiceSessionManager`, `VoiceSessionReaper`), the hook wiring, the lifespan hooks, ~12 REST routes, the auth logic, **and** a 230-line WebSocket message loop.

The WS loop is a flat `if data.get("type") == "...": ... continue` chain with **no schema validation, no message registry, and no per-message error handling**. A `KeyError` inside any branch tears down the connection (this has already bitten you once — see the `except Exception` added around the LLM tool loop at `supervisor.py:334`, whose comment explicitly says an uncaught error "tears down the entire connection").

This is the single biggest extensibility choke point in the backend. Every new client capability — a plugin invocation, a multi-agent handoff, a plan-status subscription, an artifact upload — adds a branch to a function that is already too long, in a module that already does five other jobs.

### 4.2 The observer split is a workaround for a missing protocol concept `[JUDGMENT]`

ADR-018 §2 explains that folding dashboard events into `broadcast()` "broke six existing protocol tests through cross-talk." The stated fix — a separate `broadcast_observers()` fan-out — treats the symptom.

The actual cause: **`/ws` multiplexes request/response and server-push on one channel with no correlation identifier.** A client issues `voice_session_open` and then reads frames off the socket assuming the next relevant frame is its reply. Any unsolicited broadcast interleaves and breaks that assumption. `client_request_id` (ADR-017) exists and solves exactly this — but it is applied only to `voice_session_open`, not to the protocol generally.

`[PREDICTION]`: the second time you need a fan-out that some clients want and others don't — plan progress, agent-to-agent status, artifact-ready notifications — you will add a third fan-out list, then a fourth. The fix is one envelope with a `type`, an optional `in_reply_to`, and a `payload`, applied uniformly. TD-024 (`data.content ?? data`) is the frontend half of the same missing envelope.

### 4.3 Hidden singletons and hidden global state `[VERIFIED]`

Six distinct mechanisms, all module-global:

| Global | Module |
|---|---|
| `_broadcast_hook` | `supervisor.py`, `voice_session_manager.py`, `attention_manager.py` (three copies of the same pattern) |
| `_active_turns` (a bare `int`) | `supervisor.py` |
| `_opencode_supervisor`, `_connection_manager` | `operations.py` |
| `_connection_manager` | `operations_connectivity.py` |
| module-level service instances | `main.py:64-71` |
| `DB_PATH` | `database.py` |

`attention_manager` is worse than the others: it takes `conn_manager` as an **explicit parameter** in `get_or_create()` and `initiate_contact()`, while reading the **module global** `_broadcast_hook` for the same object in `_broadcast_attention_update()`. Two injection mechanisms for one dependency in one module.

These are the direct cause of §3.3 (order-dependent test failure) and the direct blocker for multi-agent (§7).

### 4.4 Documentation integrity: two conflicting ADR numbering schemes `[VERIFIED]`

`ARCHITECTURE.md` §16 lists its own numbered "Architectural Decisions" that **collide with `docs/decisions/`**:

| ID | `ARCHITECTURE.md` §16 says | `docs/decisions/` says |
|---|---|---|
| ADR-004 | OpenCode via REST + SSE, not terminal scraping | OpenCode Runtime Isolation |
| ADR-005 | OpenCode storage isolation | Worker / Supervisor Architecture |
| ADR-006 | OpenCode credential isolation | Contact Channel Abstraction |
| ADR-007 | Notifications idempotent by DB constraint | VoiceSession Ownership |
| ADR-010 | VoiceSessionManager never duplicates Supervisor reasoning | Evidence-Based Engineering |

Anyone who reads "see ADR-007" in one document and looks it up in the other gets a **completely different decision**. This is an active landmine in the one artifact set whose entire purpose is to be unambiguous years later. It is **not in the technical debt register.**

Compounding it: `ARCHITECTURE.md`'s header declares itself "authoritative as of Milestone 9B.0 (2026-07-12)" and §17 still states the Android companion is "Built, not yet real-device-validated," wake word "not started," and the VoiceSession ownership guard "designed, not implemented." All three are false. TD-015 flags §18 staleness only, and understates the scope — §16 is not stale, it is **wrong**.

### 4.5 The technical debt register has a systematic blind spot `[JUDGMENT]`

TD-001 through TD-025 are well-written and honest. But look at what they catalogue: missing features, unrun tests, platform limitations, unimplemented channels. Now look at what is **absent**:

sync SQLite on the event loop · no transactions · no FK enforcement · no schema versioning · the `app.main`↔`tools.py` circular import · the stringly-typed tool error contract · the ADR number collision · the malformed LLM message array · the orphaned-lease window · module-global state · no CI/lint/types/packaging · a 19-minute test suite.

The register tracks **product debt** and is blind to **structural debt**. That omission pattern is itself the most important finding about your process: the review lens is "what did we promise and not build," not "what is the shape of the code we built."

---

## 5. Engineering quality

**Naming and readability**: genuinely excellent. Some of the best comment-density calibration I have read — comments explain *why*, cite the milestone and the incident, and are pruned rather than accreted. Do not lose this.

**Cohesion**: good within modules, poor at the wiring layer (§4.1, §4.3).

**Testing strategy**: 673 test functions, real concurrency tests, real state-machine tests, `caplog` assertions that credentials are never logged. Substantively good. But:

- **The suite takes 19 minutes.** The dominant cost is not sleeps — the five core files I profiled contain none, yet averaged **1.5s per test**, with `--durations` showing *fixture setup* at ~2s. The cause is an autouse fixture that runs `tempfile.mkstemp()` + a full `init_db()` (14 `CREATE TABLE`, 20 `CREATE INDEX`, 8 `PRAGMA table_info` migrations) **per test**, on Windows. Switching to an in-memory or session-scoped template database with per-test transaction rollback would plausibly cut this by an order of magnitude. `[PREDICTION]`
- **That fixture is copy-pasted verbatim into ~20 test files** instead of living in `conftest.py`. So is `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))`, which exists only because the project is not an installable package.
- **`tests/` contains ~10 files pytest never collects**: `m5_validate.py`, `m5_validate2.py`, `m5_scenarios.py`, `m6_execution_probe.py`, `m7_browser_validate.py`, `m8_browser_validate.py`, `browser_smoke.py`, `debug_ws.py`, `ws_scenario_a.py`, `check_config.py`, `llm_smoke.py`. Historical milestone validation scripts presenting as tests. They contain most of the 83 `sleep()` calls in the directory. They are dead weight that inflates the apparent test surface.

**Observability**: over-invested relative to everything else (§6).

**Deployment / operational simplicity**: `.bat` files, no packaging, no container, no service definition, no health-check contract beyond an HTTP probe, secrets in `.env` with a documented trailing-newline fragility. Repo root contains 8 stray `.log` files and `opencode.json.bak-20260708`.

---

## 6. Over-engineering: the operations and observability estate `[JUDGMENT — my strongest disagreement with the project]`

Count what has been built to *observe and restart* a single-user local assistant. I deliberately **exclude** `trace.py` and `structured_logging.py` (and their 846 lines of tests) from this count: ADR-020/021 are genuine cross-cutting infrastructure serving every module, and I praised ADR-020 in §2 and §9 — counting it as bloat here would be having it both ways.

| | Lines `[VERIFIED]` |
|---|---|
| `operations.py`, `operations_connectivity.py`, `operations_console.py`, `operation_history.py`, `operational_state.py`, `jarvis_process_manager.py`, `dashboard.js/.css/.html` | 2,448 |
| Dedicated tests (6 files) | 1,387 |
| ADR-018 + 019 + 022 + 023 | 990 |
| **Total** | **4,825** |

Now the like-for-like denominator — the **entire backend product surface**: supervisor + tools + llm + context + projects + attention (manager/policy/scheduler/interruption) + deferral + voice session manager/reaper + task manager + notifications + push + contact channels + worker events + connection manager + executor + protocol + models + all OpenCode integrations + ws_tokens + workers = **6,598 lines** `[VERIFIED]`.

**The code and tests written to observe and restart Jarvis are ~58% of the size of everything Jarvis actually does** — and that is before the ADRs. Put more bluntly: `dashboard.js` (881 lines) is larger than `supervisor.py` (805 lines).

And it is still growing. ADR-022 introduces a **separate long-running process**. ADR-023 introduces a **second SQLite database**, an `operations` table with a five-state lifecycle, an `operator` column in a system whose own ADR says "single-operator system; no invented RBAC," and a connectivity-policy split. All of it to provide seven buttons — start/stop/restart Jarvis, start/stop/restart OpenCode, refresh.

Two of those seven buttons ("start Jarvis," "restart Jarvis") genuinely cannot live inside Jarvis; that reasoning in ADR-022 is correct. But the correct response to "a process cannot restart itself" is a **12-line shell script or an OS service definition**, not a second daemon with its own database, its own operation model, and its own state machine.

Meanwhile, during the same period: no CI was added, no migration mechanism was written, no config layer was built, the `/ws` handler grew to 230 lines of `if/elif`, and the Supervisor kept sending a malformed prompt on every turn.

`[JUDGMENT]` This is the clearest instance in the codebase of effort flowing to the well-specified problem rather than the important one. Observability is *satisfying* to build — it has clean boundaries, it demos well, it produces impressive artifacts. That is exactly why it needs a budget.

**Recommendation**: freeze the operations/observability estate at its current size. Do not build Operations v2. Collapse ADR-022 + ADR-023's Jarvis-lifecycle half into a documented script. Keep the Control Center — it is genuinely useful and read-only is the right call — but it gets no new subsystems until CI, migrations, and a config layer exist.

---

## 7. Readiness of the capability roadmap

| Milestone | Verdict | Why |
|---|---|---|
| **Memory** | **Needs foundation first** | The entire memory model today is the `conversations` table plus `_load_conversation(max_turns=10)`. No embeddings, no summarization, no retrieval, **no token budget of any kind** — the context blob and 10 turns are concatenated and sent regardless of length. Before memory: fix §3.1, add a token-budgeted context assembler, and decide whether memory is a table or a retrieval index. |
| **Supervisor intelligence** | **Needs foundation first** | `MAX_TOOL_CALLS = 5`, no streaming, no structured output, `max_tokens=1024` hardcoded, provider config frozen at import. The prompt is malformed (§3.1). Fix the prompt builder and add token accounting first; both are days, not weeks. |
| **Planning / long-running execution** | **Needs foundation first** | Everything is turn-scoped. `trace_id` = one `process_message()` call. There is no plan entity, no step entity, no durable execution state that survives a turn boundary, and no transaction support to write a multi-step plan atomically (§3.7). This is the milestone that will expose the persistence layer hardest. |
| **Plugins** | **Architectural mismatch as currently shaped** | `ToolRegistry._register_all()` is a hardcoded method with handlers bound to `self`. No discovery, no manifest, no versioning, no per-tool permission model, no isolation, and the error contract is a string prefix (§3.6). A third-party tool today would be a Python function with unrestricted access to `app.database` and the filesystem. The registry must be inverted (declarative registration, capability-scoped, typed results) **before** the first plugin exists, not after. |
| **Multi-agent** | **Architectural mismatch** | One module-global `Supervisor` instance. `_active_turns` is a module-level `int`. **No agent identity column exists in any table** — not `conversations`, not `tasks`, not `events`, not `attention_requests`. There is no way to express "agent A started this task" in the current schema. This is not a gap you close incrementally; it is a schema and lifecycle change, and it needs the migration mechanism (§3.7) to exist first. |
| **Artifact management** | **Needs foundation first** | No artifact concept anywhere. `opencode_tasks.result_summary` (a TEXT column) is the closest thing. Needs a storage decision (blobs in SQLite vs filesystem vs content-addressed store) before code. |
| **Multi-device** | **Partially ready; two known blockers** | The transport, pairing, and lease primitives are genuinely good. Blockers: `get_latest_device_status()` is wrong for >1 device (§3.9); `_device_status` is in-memory-only and lost on restart; TD-025 (`currentTurn` single slot); and no routing layer exists — ADR-012 defines a capability-advertisement protocol that **nothing currently reads**. |
| **Remote operation** | **Needs foundation first — and it is the highest-risk item on the roadmap** | TD-003 (no non-LAN transport) + TD-018 (no credential UI, `JARVIS_API_TOKEN` unset in practice) + no rate limiting + no audit trail of authenticated actions + a `/ws` handler with no message schema validation. Do **not** ship remote access before §8 items 1–4. |

---

## 8. What I would build first — six-month roadmap

Ordered by *what unblocks the most downstream work*, not by feature value. Items 1–5 are the ones everything else depends on.

### Month 1 — Make the invariants executable (highest leverage)

1. **CI, packaging, and static analysis.** `pyproject.toml` making `app` an installable package (deletes ~30 `sys.path.insert` lines). GitHub Actions running pytest + `ruff` + `mypy` on the backend and `./gradlew test` on Android, on every push. A lockfile. Pre-commit hooks. **This is the single highest-value week of work available to you** — it converts every ADR from a promise into something with a chance of being enforced, and it is the prerequisite for a second contributor ever being safe.
2. **Fix the test suite's cost and its order dependency.** Move the DB fixture into `conftest.py`; switch to a session-scoped schema template + per-test rollback. Fix the `test_operations_api` order dependency by eliminating the globals that cause it. Target: under 90 seconds. A 19-minute suite will not be run by a human, and therefore will not be run.
3. **Fix §3.1 (prompt ordering) and §3.2 (observer→policy contamination), with regression tests.** Two small fixes, both currently affecting real behaviour.

### Month 2 — Persistence and configuration

4. **A real data-access layer.** One module owning connection lifecycle: `PRAGMA foreign_keys=ON`, a `busy_timeout`, a context-manager transaction API so multi-step operations are atomic, thread-offload for the async callers, and an absolute `DB_PATH` resolved from config. Then a **versioned migration mechanism** (`schema_version` table, ordered migration files). Do not write another feature that touches the schema until this exists — TD-006 and multi-agent are both blocked on it today.
5. **A configuration layer.** One typed settings object, validated at startup, with every one of the ~38 environment variables declared in one place and `.env.example` generated from it. Fail loudly at boot on invalid config instead of silently at first use.

### Month 3 — Boundaries

6. **Extract the `/ws` protocol.** A message router with a per-type schema (Pydantic), one handler per message type in its own module, structured errors, and a uniform envelope with `type` / `in_reply_to` / `payload`. This retires the observer-split workaround (§4.2) and TD-024, and it is the seam every future client capability plugs into.
7. **Replace `set_broadcast_hook` globals with constructor injection.** An explicit application-composition module; `main.py` becomes routing only. This is the prerequisite for multi-agent and for tests that cannot contaminate each other.
8. **Documentation integrity pass.** Delete `ARCHITECTURE.md` §16's numbering entirely and replace it with links to `docs/decisions/`. Re-baseline §17/§18 against reality. Split `SESSION.md` (435 KB) by milestone. Add the structural items from §4.5 to the debt register.

### Months 4–6 — Only now, capabilities

9. **Token-budgeted context assembly**, then **memory** on top of it.
10. **Invert `ToolRegistry`** into a declarative, capability-scoped, typed-result plugin surface — *before* the first plugin.
11. **A plan/step entity** with durable execution state, built on the transaction support from item 4.
12. **Multi-agent identity in the schema** (an `agent_id` on the relevant tables), delivered via the migration mechanism from item 4.

**Explicitly not in six months**: remote reachability (needs items 1–6 first), ESP device, Operations v2, iOS.

---

## 9. ADR-by-ADR verdicts

| ADR | Verdict | Reasoning |
|---|---|---|
| 001 Laptop is the brain | **Keep unchanged** | The best decision in the project. Will age best. |
| 002 Hybrid Android | **Keep, add addendum** | Sound. But the "two client codebases" cost it names as a tradeoff is now real and unmeasured — voice logic exists twice. Record the actual cost. |
| 003 Deterministic attention | **Keep unchanged** | Correct and disciplined. Do not revisit. |
| 004 OpenCode runtime isolation | **Keep unchanged** | Both incidents real, both fixes right. |
| 005 Worker/Supervisor | **Keep, revise before plugins** | The principle holds. "Bounded ToolRegistry" is not bounded enough to admit third-party code (§7). |
| 006 Contact channel abstraction | **Revise — currently vacuous** | `InAppChannel` and `PushChannel` both funnel into `notify()`, which always pushes (§3.9). The abstraction does not currently differentiate delivery. Either make channels genuinely own delivery, or collapse to one channel and let `InterruptionPolicy` own the decision alone. |
| 007 VoiceSession ownership | **Keep, add status addendum** | Its main documented tradeoff (no multi-client guard) was closed in M9B.4. The ADR still reads as open. |
| 008 Hybrid presence model | **Merge into ADR-001** | Its own Tradeoffs section admits it "states a rule rather than resolving a specific technical question" and largely restates ADR-001. This is ADR inflation — it dilutes the signal of the set. |
| 009 Cost policy | **Keep, revise the mechanism** | Policy correct. The enforcement is `model.endswith(":free")` — a string convention owned by a third party (§3.9). Make the allowlist explicit. |
| 010 Evidence-based engineering | **Keep, reclassify** | Excellent content, but it is an engineering-principles document, not an architecture decision. Move to `ENGINEERING_PRINCIPLES.md` and reference it from the ADR index. |
| 011 TOFU pairing | **Keep unchanged** | Right model, honestly documented tradeoff. |
| 012 Device state-sync protocol | **Revise → mark Experimental** | Defines a capability-advertisement protocol that **nothing reads** (`connection_manager.py:26-28` says so explicitly). Speculative generality. Either build routing on it this year or downgrade its status honestly. |
| 013 Standing delegation cost policy | **Keep** | Narrow, well-scoped. |
| 014 Unified WS auth | **Keep, add a removal date** | Correct. The deprecated `Authorization:` header path needs a scheduled removal, otherwise it is permanent. |
| 015 Attention widget | **Keep unchanged** | Sound. |
| 016 Android voice infrastructure | **Keep unchanged** | Sound. |
| 017 Production wake-word foundation | **Keep** | Good. TD-022 (Doze/battery/adverse-acoustic unproven) remains the live risk. |
| 018 Control Center observability | **Revise — its central invariant is violated** | See §3.2 and §4.2. The invariant is right; the enforcement is absent and the "separate fan-out" fix addresses a symptom. |
| 019 Separation of observability and operations | **Merge into 018** | 208 lines restating ADR-018's Contract 2 and naming a subsystem that ADR-022 then defines. Three ADRs for one boundary. |
| 020 Trace ID model | **Keep unchanged** | The best-engineered of the recent set. |
| 021 Structured logging | **Keep** | Good schema. Worth adding: log volume/rotation is unbounded, same class as TD-019. |
| 022 Jarvis Operations subsystem | **Revise down sharply** | See §6. The "a process cannot restart itself" reasoning is correct; the response (a second daemon) is disproportionate. Reduce to a documented script or OS service unit. |
| 023 JOPS v1.0 operation model | **Merge into 022 and simplify** | A second SQLite database, an `operations` table, a five-state lifecycle, and an `operator` column in a system whose own text says "single-operator; no invented RBAC." This is a schema built for a multi-tenant operations platform serving one person and seven buttons. |

**Meta-observation on the ADR process**: ADRs 001–017 mostly document decisions that were *forced by incidents* — they read as hard-won. ADRs 018–023 mostly document decisions about *Jarvis observing and managing itself* — they read as generated by a process rather than by pressure. Six of the last eight ADRs are about the tooling around the product, not the product. That ratio is the leading indicator of §6.

---

## 10. Brutal honesty

### What I would delete

- **`spikes/android-wakeword/`** — `hey_jarvis.tflite`, `MicroWakeWordEngine.cpp`, `MicroFrontendWrapper.cpp`, and `CMakeLists.txt` are **byte-identical** to their `android/app/src/main/cpp/` counterparts `[VERIFIED]`. Only `MicroWakeWord_jni.cpp` differs. A fix applied to one will silently diverge from the other, and nothing detects it. The spike answered its question; ADR-017 records the answer. Delete it. (`spikes/android-presence/` is genuinely disposable and already superseded — delete that too.)
- **The ~10 uncollected scripts in `tests/`** (`m5_validate*.py`, `m6_execution_probe.py`, `m7/m8_browser_validate.py`, `browser_smoke.py`, `debug_ws.py`, `ws_scenario_a.py`, `check_config.py`, `llm_smoke.py`). They are milestone-validation history, not tests. Move to `scripts/historical/` or delete; they are in git history either way.
- **`ARCHITECTURE.md` §16's numbered list.** Not "revise" — delete. It is a second, colliding ADR namespace (§4.4).
- **The 8 stray `.log` files and `opencode.json.bak-20260708`** in the repo root.
- **The `operator` column in ADR-023's `operations` table.** It has one possible value and encodes an RBAC model the same document disclaims.

### What I would rewrite

- **The `/ws` message loop** in `main.py` → a router with schemas (§8.6). This is the only thing I would call an outright rewrite, and it is ~300 lines.
- **`database.py`'s connection and transaction handling** — not the SQL, not the schema, not the queries. The 20 lines that own connections, plus a migration mechanism (§8.4).
- **`Supervisor._process_message_inner`'s message assembly** — §3.1, ten lines.
- **`ToolRegistry`'s registration and error contract** — before plugins, not after (§7).

### What I would leave completely untouched

The attention/interruption state machines. The evidence-based OpenCode lifecycle. The idempotency-by-constraint patterns. The Android pairing and reconnect layer (`DisconnectClassifier` / `ConnectionGenerationTracker` / `BackoffPolicy` is genuinely good engineering). The comment culture. The `spikes`-then-decide methodology.

### What I would absolutely refuse to build until something else existed

| Refuse to build | Until this exists |
|---|---|
| **Plugins** | An inverted, capability-scoped tool registry with typed results and a permission model. Shipping plugins on today's `ToolRegistry` means arbitrary third-party Python with unrestricted `app.database` access and a string-prefix error contract. |
| **Remote / non-LAN access** | CI, a WS message schema, real authentication with a credential UI, rate limiting, and an auth audit trail. Today's `/ws` is a 230-line `if/elif` over unvalidated JSON with auth that is a no-op by default. Exposing that publicly is the one decision here that could produce a genuinely bad day. |
| **Multi-agent** | The migration mechanism, agent identity in the schema, and constructor injection replacing the module globals. |
| **Autonomous / background planning** | Transactions, and DB access off the event loop. A planner writing multi-step state through four independent auto-committing connections will produce partial plans on the first crash. |
| **Anything new in Operations/Observability** | Items 1–5 of §8. That estate is already 3.8× the reasoning core. |

### Which decisions age best

1. Laptop-is-the-brain (ADR-001/008). Five years from now this will still be right, and it will be why the codebase is still comprehensible.
2. Deterministic interruption policy with no LLM in the safety path (ADR-003).
3. Evidence over inference (ADR-010) — the `degraded` state instead of a guessed `failed`/`completed` is a decision most projects get wrong permanently.
4. Idempotency enforced by database constraint rather than caller discipline.
5. `trace_id` as a `ContextVar` bound per turn, persisted on the row for async legs (ADR-020).

### Which decisions age worst

1. **The absence of CI/lint/types/packaging.** Not an ADR — which is precisely the problem. The most consequential architectural decision in this repository was never written down, never reviewed, and is compounding daily.
2. **ADR-022/023's Operations subsystem.** A second process and a second database for a single-user local tool. In two years this is either dead code you are afraid to delete, or a maintenance burden with its own migration problem.
3. **`ARCHITECTURE.md` §16's parallel ADR numbering.** Documentation debt that actively misleads, in the artifact set built specifically to prevent that.
4. **`_ensure_column` as the entire migration story.** It has already made TD-006 unimplementable. Every additional table makes the eventual real migration worse.
5. **The stringly-typed tool contract.** Fine for 16 hand-written tools. Structurally unsafe as a plugin boundary, and it will be load-bearing before it is fixed.
6. **ADR-012's device-state protocol that nothing reads.** Speculative generality is the cheapest debt to accumulate and the hardest to notice.

---

## 11. Ranked technical debt (structural items merged into the existing register)

New IDs proposed as TD-026+; existing IDs re-ranked against them.

| Rank | Item | Urgency | Probability of biting | Impact | Cost now → cost in 2 yrs | Deadline |
|---|---|---|---|---|---|---|
| 1 | **TD-026 — No CI, packaging, linter, or type checker** | Critical | Certain (already happening — §3.3) | Every invariant in 23 ADRs is unenforced | 1 week → 2–3 months | **Immediate** |
| 2 | **TD-027 — No transactions, no FK enforcement, no schema versioning** | Critical | High | Blocks TD-006, multi-agent, planning; silent data integrity loss | 2 weeks → a data-migration project with live data | **Before any new table** |
| 3 | **TD-028 — Malformed LLM message array** (§3.1) | High | Certain (happening now) | Degraded reasoning + added latency on every turn (not billed cost — free model) | 1 hour → same, but with N months of bad conversation logs | **This week** |
| 4 | **TD-029 — Observer connections contaminate `InterruptionPolicy`** (§3.2) | High | Certain when dashboard is open | Violates ADR-018's stated core invariant; wrong contact behaviour | 1 day → grows with each new `_connections` reader | **This month** |
| 5 | TD-018 — No auth by default / no PWA credential UI | Critical *conditional* | Certain **if** remote is pursued | Full compromise | 1 week → weeks + an incident | **Gate on remote work** |
| 6 | **TD-030 — Module-global state and circular imports** (§4.3, §3.4) | High | Certain (already causing §3.3) | Blocks multi-agent; makes tests order-dependent | 1 week → entangled across 100k lines | **Month 3** |
| 7 | **TD-031 — `/ws` is an unvalidated 230-line `if/elif`** | High | High | Choke point for every future client capability; crash surface | 1–2 weeks → a protocol-versioning project | **Month 3** |
| 8 | TD-019 — Unbounded table growth | Medium | Certain over time | Query degradation, disk | 1 week → data migration under pressure | Before long unattended runs |
| 9 | **TD-032 — 19-minute test suite + 1 order-dependent failure** | High | Certain | Nobody runs it; regressions land silently | 3 days → suite abandoned | **Month 1** |
| 10 | **TD-033 — ADR number collision + stale `ARCHITECTURE.md` §16/17/18** | Medium | High | Actively misleads the next engineer | 1 day → compounds with every new ADR | **Month 1** |
| 11 | TD-022 — Wake-word Doze/battery/acoustics unproven | High *conditional* | High | The whole feature may not work backgrounded | Device time → a shipped feature that fails in the field | Before wake-word production |
| 12 | **TD-034 — 38 env vars, no config layer, some frozen at import** | Medium | Medium | Misconfiguration fails silently at first use | 3 days → grows linearly | Month 2 |
| 13 | TD-003 — Transport reachability | High *conditional* | Certain if phone leaves LAN | Total loss of function off-LAN | Research spike → same | Gate on remote work |
| 14 | **TD-035 — Sync SQLite on the event loop** | Medium *(rises to High with concurrency)* | High once background work exists | Loop stalls under concurrency | Covered by TD-027's boundary → a rewrite | With TD-027 |
| 15 | TD-006 — Permission/Question conflation | Medium | Medium | Schema clarity | **Currently unimplementable** — blocked on TD-027 | After TD-027 |
| 16 | **TD-036 — Spike/production source duplication** (§10) | Medium | Medium | Silent divergence of native wake-word code | 1 hour (delete) → a debugging session | **This week** |
| 17 | TD-024 / TD-025 — Control Center envelope + `currentTurn` | Low | Low today | Dashboard-only | Fold into TD-031 | With TD-031 |
| 18 | TD-012 — iOS Safari untested | Medium | Certain if iOS is claimed | Unknown | Test session → same | Gate on any iOS claim |
| 19 | **TD-037 — `ToolRegistry` string error contract + no plugin permission model** | Medium *(Critical before plugins)* | Certain if plugins ship | Arbitrary code with unrestricted DB access | 1 week → a security boundary retrofit | **Gate on plugins** |
| 20 | TD-004 / TD-007 / TD-008 / TD-009 / TD-010 / TD-011 / TD-013 / TD-014 / TD-015 / TD-016 / TD-017 / TD-020 / TD-023 | Low–Medium | Varies | Localized | Small either way | Opportunistic |

---

## 12. Assumptions embedded in the architecture that will become false

Beyond the ones you already track (single user, SQLite, OpenCode available, laptop online):

| # | Assumption | Where it is baked in | When it breaks |
|---|---|---|---|
| 1 | **One connection ⇒ a human is present** | `attention_manager._connected()` | Already false (§3.2). Breaks harder with every non-human client: dashboards, health checkers, future agents. |
| 2 | **One turn = one unit of work** | `trace_id`, `_active_turns`, `MAX_TOOL_CALLS=5`, no plan entity | The moment planning or background execution exists. |
| 3 | **A `VoiceSession` and a WebSocket connection have the same lifetime** | `main.py`'s `finally` block closing the session on disconnect | The moment a session should survive a reconnect — which is the normal case on mobile. |
| 4 | **Every state change is small enough to be one auto-committed write** | All of `database.py` | The first multi-row operation that must be atomic — i.e. the first plan. |
| 5 | **Config is static for the process lifetime** | `LLMProvider.__init__`, `interruption_policy.DEFAULT_RETRY_MINUTES` at import | The first time quiet hours or a model needs to change without a restart. |
| 6 | **The context window is effectively unbounded** | `_load_conversation(max_turns=10)` + unbounded context blob, no token accounting | The first long conversation, the first large `result_summary`, or memory. |
| 7 | **One phone** | `get_latest_device_status()`, `record_heartbeat()` (global, not per-connection), `_device_status` in-memory only | Two devices. Note this contradicts the multi-device readiness the lease work implies. |
| 8 | **`cwd` is the repo root** | `DB_PATH = "jarvis.db"` | Any service manager, container, or scheduled task. |
| 9 | **"Free" is a model-ID suffix** | `llm.py:44` | A provider naming change silently disables your only real-money guard. |
| 10 | **The `tasks` table is a superset of all work** | `_cancellable_tasks()` scans `get_recent_tasks(50)` | A second worker type that is not subprocess-shaped; a plan that is not a task. |
| 11 | **One reasoning process** | Module-global `Supervisor`, `_active_turns` as an `int` | Multi-agent. This is a schema problem, not a code problem. |
| 12 | **Documentation is read by someone who knows which document is authoritative** | Two ADR namespaces (§4.4), 435 KB `SESSION.md` | The second contributor. |
| 13 | **Every non-terminal entity has a startup reconciliation path** | `main.py`'s `lifespan` reconciles `tasks` (`mark_running_tasks_interrupted`) and `opencode_tasks` (`mark_running_opencode_tasks_interrupted`) — but **not** `voice_sessions` `[VERIFIED]` | Already false. `ARCHITECTURE.md` claims restart survival is uniform ("survive browser closure, phone reboot, and a full Jarvis restart identically"); voice sessions instead rely on `VoiceSessionReaper`'s 60s idle tick, and the orphaned-lease window in §3.7 (lease claimed, session row never created) is reconciled by **nothing at all**, because the reaper only reaps rows that exist. |

---

## 13. Closing

The instinct behind this project is correct: build the boring deterministic infrastructure properly, keep the LLM on a short leash, refuse to accept inference as evidence, write down why. That is a rarer and more valuable instinct than any specific technology choice, and it is why this codebase is worth investing five years in.

The failure mode is not carelessness. It is **misallocated rigor**. The same energy that produced 23 ADRs and a 669-line debt register also produced a malformed LLM prompt that has shipped since Milestone 5, a declared-inviolable invariant that is currently violated, two colliding ADR namespaces, and a test suite that takes 19 minutes and fails. The rigor is real. It is pointed at documents instead of at code.

The correction is not more process. It is **making the existing process executable**: CI, types, a linter, a migration mechanism, a config layer, an injected composition root. One month of unglamorous infrastructure buys you the right to trust every ADR you have already written.

Do that first. Everything on the capability roadmap is downstream of it.

I would not rewrite this system. I would spend one month making it enforceable, then continue.
