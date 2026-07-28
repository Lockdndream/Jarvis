# Jarvis Deep Architecture Audit

**Reviewer role:** Independent Principal Software Architect
**Date:** 2026-07-28
**Scope:** Whole-system architecture reviewed against ADR-018 → ADR-023 and the long-term vision (always-available assistant: long-running conversations, proactive assistance, delegation, task supervision, failure recovery, memory, natural voice, multi-device coordination — without tight coupling or unmaintainability).
**Method:** Read the six ADRs under review plus foundational ADRs (001, 005), the Supervisor, tool registry, data model, connection layer, OpenCode integration, the WebSocket loop, the Operations subsystem, and the technical-debt register. Three load-bearing ADR claims were verified directly against code rather than trusted (results in §5).

> **Reading instruction.** This report deliberately separates *how well the system is governed* from *how well the architecture fits the stated vision*. They are different scores, and the first is high enough to be dangerous if read as the second. Excellently-governed is not the same as ready.

---

## 1. Executive Summary

Jarvis is one of the best-governed small systems I have reviewed. The ADR set is unusually rigorous, evidence-based, and honest; the technical-debt register is candid and structural; and — verified directly — the implementation actually tracks the ADRs, including the security-critical parts. On the axis of "is this well-engineered for what it does today," Jarvis is strong.

But the mission is forward-fit, not present-quality, and here the verdict is more nuanced. **Every load-bearing assumption in the current architecture is singular: one user, one process, one worker (OpenCode), one LLM, one SQLite file, one device that matters, one turn at a time, request→response.** The stated 2–3 year vision breaks each of those assumptions specifically:

- **Memory** — there is no memory subsystem of any kind. Conversation is flat rows, last-10-turns reloaded per turn, no summarization, no retrieval, unbounded growth. "Remember context" has literally nothing to build on yet.
- **Supervisor Intelligence / Planning** — the Supervisor is a stateless, 5-call-bounded ReAct loop that returns one response. There is no plan or goal object that spans turns, no resumable long-running supervision. `trace_id` is deliberately turn-scoped (ADR-020), and ADR-023 already had to mint a separate `operation_id` because operator actions aren't turns — the first visible crack that the turn-scoped model doesn't cover what planning needs.
- **Multi-agent / Plugins** — the worker is wired in singularly (`ToolRegistry(task_manager, opencode_supervisor)`, `self._oc`), tools are hand-registered in one method, and there is no namespacing, scoping, or dynamic registration.
- **Multi-device / Proactive** — single-device assumptions are already baked into the connection layer, and synchronous SQLite on the async event loop is a latent bottleneck the moment background/proactive work contends with foreground turns.

None of this is a crisis. The system is safe and correct for its current single-user LAN deployment, and the boundaries it *has* drawn (read-only observability vs. write operations, supervisor-routes/workers-execute, laptop-is-the-brain) are the *right* boundaries and will survive. The work ahead is **additive foundation-building** (a memory store, a plan/goal layer, a worker registry, async DB access), not undoing bad decisions. That is a good place to be — but it is real, unbuilt work, and some of it should land *before* Supervisor Intelligence, not alongside it.

---

## 2. Architecture Score

**Overall: 7 / 10** — a strong, safe, well-governed foundation that is not yet shaped for the vision it intends to grow into.

Because a single number hides the real story, it decomposes as:

| Dimension | Score | Rationale |
|---|---|---|
| **Governance & architectural discipline** | 9 / 10 | ADR quality, evidence-based decisions, honest debt register, boundary enforcement. Best-in-class for a system this size. |
| **Current-scope correctness & safety** | 8 / 10 | Faithful ADR implementation (verified), sound security model *for the stated LAN/single-user scope*, bounded tool surface, real failure-injection testing. |
| **Forward-fit for the 2–3 year vision** | 5.5 / 10 | No memory store, no plan/goal object, single-worker/single-LLM wiring, sync IO on the event loop, single-device assumptions in the connection layer. All additive to fix, but all currently absent. |

The gap between 9 and 5.5 is the whole point of this report.

---

## 3. Strengths

1. **ADR governance is genuinely load-bearing, not ceremonial.** ADR-019 (don't let observability drift into control) and ADR-005 (supervisor routes, workers execute) are real constraints that have demonstrably redirected work (CCOU.1 → ADR-022). Decisions cite real incidents, real test failures, and real diffs.
2. **The core boundary is correct and clean.** "Supervisor decides which bounded tool to call; workers execute" (ADR-005) is faithfully implemented in `tools.py` — the Supervisor never opens a file or runs a shell command. This is the single most important boundary for a system that will delegate more, and it holds.
3. **`trace_id` is a genuinely good primitive.** The `contextvars.ContextVar` propagation (`app/trace.py`, bound once in `process_message`) plus persisting `trace_id` onto the `opencode_tasks` row so the async completion leg can recover it (rather than threading a callback parameter) is exactly right, and forward-compatible.
4. **Read-only / write separation is architecturally enforced, not just intended.** `broadcast_observers()` is a structurally separate fan-out (`connection_manager.py:151`), and `has_observers()` gates even the `save_event` write, so observability is truly zero-cost when nothing is watching (`supervisor.py:70`).
5. **The security model is proportionate and *verified*.** `_require_localhost` fails closed with 403 regardless of `JARVIS_API_TOKEN` (`operations.py:44`), destructive endpoints require `confirm=true`, and the ADR-022-mandated `_stopped=False` restart fix is actually present (`opencode_supervisor.py:135`).
6. **Evidence-based engineering is a culture, not a slogan (ADR-010).** Real-device failure injection found and fixed real defects; the register distinguishes FACT from REQUIRES-EXPERIMENT honestly (e.g. TD-022, TD-023).
7. **Delegated result capture already exists in embryo.** `opencode_tasks.result_summary` + `_capture_task_result` + `get_task_result` (`tools.py:144`) mean Jarvis can already answer "what did that task find" without re-running it — a concept many systems miss entirely.

---

## 4. Weaknesses

1. **No memory subsystem.** Not weak — *absent*. The largest single gap against the vision.
2. **No plan/goal abstraction above the turn.** The Supervisor cannot represent, persist, or resume multi-step intent.
3. **Synchronous SQLite on the async event loop**, compounded by a **single-writer file** that background loops (SSE, poll, reaper, scheduler) already contend on.
4. **Singular wiring** for worker, LLM, and device — none of which is the number the vision needs.
5. **The connection is the failure blast radius** — no per-message isolation in the WebSocket loop.
6. **Leaky cross-module coupling** in the tool layer (`from app.main import ...` inside tool handlers; reaching through `self._oc.cm`).
7. **Module-global singletons** encode the single-process assumption throughout.
8. **Unbounded growth** across 10 of 11 tables (TD-019), only partially addressed.

---

## 5. ADR Compliance Matrix

Legend: ✅ Implemented correctly · 🟡 Partial / drift (disclosed) · ⚪ Deferred by design · ❌ Missing/violated.
Rows marked **[verified]** were checked against code in this review, not accepted on the ADR's own testimony.

| ADR | Status | Evidence / Notes |
|---|---|---|
| **ADR-018** Control Center Observability | ✅ **[verified]** | Separate `broadcast_observers()` fan-out (`connection_manager.py:151`); `has_observers()` gates the `save_event` write *and* the broadcast (`supervisor.py:70`); snapshot endpoint carries `_require_api_token` (`main.py:363`). Load-bearing invariant (observer/general split) is real in code. |
| **ADR-019** Separation of Observability & Operations | ✅ **[verified]** | Control Center source untouched by Operations; `app/operations.py` is a physically separate router. The read-only guarantee is structural, not aspirational. |
| **ADR-020** trace_id Execution Correlation | ✅ **[verified]** | `ContextVar` in `app/trace.py`; bound once in `process_message` (`supervisor.py:155`); persisted on task rows; four nullable additive columns present (`database.py:585-588`). Strongest single decision in the set. *Caveat, not a defect:* it is turn-scoped by design — see Finding 2. |
| **ADR-021** Structured Logging | ✅ (🟡 by design) | Console stays unstructured deliberately; JSONL file + `JarvisContextFilter`; targeted `extra=` at real call sites (`supervisor.py:311`, `operations.py:_audit`). `conversation_id`/`task_id` present only at specific sites — disclosed tradeoff, not drift. |
| **ADR-022** Jarvis Operations Subsystem | ✅ **[verified]** | `_require_localhost` fails closed and even covers `::1` (`operations.py:44`) — *slightly stricter than the ADR text*; `confirm=true` enforced (`operations.py:101`); the required `_stopped=False` restart fix is present (`opencode_supervisor.py:135`); claim/run state machine implemented. Minor drift: endpoints live in a mounted `operations.py` router, not literally "on `app/main.py`" — this is *cleaner* than the ADR described. |
| **ADR-023** JOPS v1 (operation model, self-management, connectivity) | ✅ / ⚪ **[verified]** | `operations` table lives in a **separate** `operations.db` under a console-local dir, not `jarvis.db` (`operation_history.py:33-43`) — exactly as claimed, and correctly so. `operation_id` is its own namespace. Android connectivity *enforcement* is ⚪ deferred and disclosed. *Not re-verified this pass:* that `GET /api/settings` actually returns `connectivity_mode` (claimed; low-risk; worth a spot check). |
| ADR-001 Laptop is the Brain | ✅ | Reasoning/state/persistence centralized; clients are pass-through. Holds. |
| ADR-005 Supervisor/Worker | ✅ | Every tool delegates to `TaskManager`/`OpenCodeSupervisor`; no direct execution path. Holds — but see Finding 4 on the *singular* worker. |

**Net:** No compliance violations found. Two of the three deep-verified claims were implemented *better* than their ADR text. This is a high-integrity codebase.

---

## 6. Top 20 Findings

Severity scale: **Critical** (blocks the vision or is a present safety risk) · **High** (must be resolved before the dependent milestone) · **Medium** (real, schedule it) · **Low** (watch / cheap).

---

### Finding 1 — No memory subsystem exists
- **Severity:** Critical (for the vision; N/A today)
- **Evidence:** `_load_conversation` reloads the last 10 user/assistant rows per turn (`supervisor.py:757-762`); context is rebuilt from scratch each turn (`_format_context`, `supervisor.py:767`). No `memory`, `facts`, `summaries`, or embedding table in the schema (`database.py:382-596`). No summarization/compaction anywhere.
- **Impact:** "Remember context," "long-running conversations," and "proactive assistance" all depend on a durable, retrievable, salience-ranked memory that does not exist. Every turn is effectively amnesiac beyond 10 messages.
- **Recommendation:** Design a Memory subsystem as a first-class peer to the Supervisor *before* proactive/long-conversation milestones: a store (facts/episodes/summaries), a write path (turn/task completion → candidate memories), a retrieval path (relevance selection injected into `build_context`), and a rollup/compaction policy. Use `trace_id` as the provenance key per ADR-020's governing invariant. This is the highest-ROI new subsystem.

### Finding 2 — No plan/goal object above the turn; `trace_id` is turn-scoped by design
- **Severity:** Critical (for Supervisor Intelligence)
- **Evidence:** `process_message` runs a loop bounded to `MAX_TOOL_CALLS = 5` (`supervisor.py:22,292`) and returns a single `{"response", "conversation_id", "trace_id"}` dict. ADR-020 fixes a trace as *one turn*; ADR-023 already had to invent a separate `operation_id` because operator actions aren't turns (`operation_history.py:10-12`).
- **Impact:** A "plan" (decompose a goal, execute steps across many turns/tasks, track progress, resume after failure) has no home. Supervisor Intelligence cannot be bolted onto the current turn loop; it needs an orchestration layer *above* it with its own persisted identifier.
- **Recommendation:** Introduce a `goal_id`/`plan_id` correlation tier above `trace_id` (a plan is many traces, exactly as a session is many traces). Persist plan state (`plans`/`plan_steps` tables). Keep the turn loop as the execution primitive; add a planner/executor on top. Do this before Supervisor Intelligence, not during.

### Finding 3a — Synchronous SQLite blocks the async event loop
- **Severity:** High (latent; Medium today)
- **Evidence:** Every `db.*` function opens a fresh blocking `sqlite3.connect`, executes, and closes (`database.py:10-25`, pattern repeated throughout). All are called directly from `async` code with no thread offload.
- **Impact:** Each DB call blocks the single event loop that also serves every WebSocket connection. At one user it's invisible; with proactive background work, multiple devices, or multiple delegated agents, foreground voice latency degrades under DB load.
- **Recommendation:** Route DB access through a thread pool (`asyncio.to_thread`) or migrate to `aiosqlite`, behind the existing `app/database.py` function boundary so call sites don't change.

### Finding 3b — Single SQLite file is a single-writer serialization point
- **Severity:** Medium (rising)
- **Evidence:** One `jarvis.db` (`database.py:7`), WAL mode, written by the Supervisor, OpenCode SSE loop (`_sse_loop`), poll loop (`_poll_loop`), voice-session reaper, and attention scheduler concurrently.
- **Impact:** Distinct from 3a — async offload does *not* fix this. WAL allows concurrent readers but still serializes writers; more background writers means more write contention and `SQLITE_BUSY` risk.
- **Recommendation:** Keep SQLite (right choice for local-first), but treat write concurrency explicitly: a single writer actor/queue, or `busy_timeout` + retry, before background/proactive writers multiply. Do **not** conflate this fix with 3a in planning.

### Finding 4 — Worker and LLM are wired in singularly; no multi-agent/multi-LLM seam
- **Severity:** High (for multi-agent, plugins, multi-LLM)
- **Evidence:** `ToolRegistry(task_manager, opencode_supervisor)` takes exactly one worker (`tools.py:19`); routing decides worker by `if db.get_opencode_task(...)` (`tools.py:177,201`); `_configure_llm` builds one provider singleton (`supervisor.py:116-122`).
- **Impact:** "Multiple delegated agents" and "multiple LLMs" both require abstractions that don't exist. Adding a second worker type today means more `if`-by-type branches, not registration.
- **Recommendation:** Introduce a Worker/Agent registry (workers register by capability; routing dispatches by task type, not `if`) and an LLM-router abstraction (model selection by task class/cost, extending ADR-009's cost policy). Neither is urgent until the second worker/model actually arrives — but design the seam before Supervisor Intelligence assumes only OpenCode exists.

### Finding 5 — The WebSocket connection is the failure blast radius
- **Severity:** High
- **Evidence:** The message loop is `while True: raw = await ws.receive_text(); data = json.loads(raw)` with **no per-message try/except** (`main.py:540-542`); the only handler is the outer `except Exception` that disconnects the whole connection (`main.py:749`). `Supervisor.process_message` guards *its own* LLM loop (`supervisor.py:334`) — but that is a point mitigation added after a real RC incident, not a structural guarantee. A malformed frame (`json.loads` on line 542) or any unguarded handler tears down the connection.
- **Impact:** For an always-available assistant, one bad frame or one unguarded code path drops the user's live session. Resilience is currently defense-in-depth applied *only where an incident already happened*.
- **Recommendation:** Wrap per-message handling in a try/except that logs and sends a structured error frame, keeping the connection alive. Make per-turn error isolation the structural default, not the exception.

### Finding 6 — Leaky cross-module coupling in the tool layer
- **Severity:** Medium
- **Evidence:** Tool handlers import a `main.py` module global at call time (`from app.main import voice_session_manager`, `tools.py:322,327`) and reach through the OpenCode supervisor to get the connection manager (`self._oc.cm`, `tools.py:318`).
- **Impact:** Circular-ish dependencies and hidden wiring make the tool layer hard to test in isolation and hard to relocate (e.g. into a plugin process). Erodes the clean ADR-005 boundary at the edges.
- **Recommendation:** Inject dependencies explicitly into `ToolRegistry` (voice-session manager, connection manager) rather than importing globals mid-call. Cheap now; expensive after a plugin SDK depends on the current shape.

### Finding 7 — No tool namespacing, scoping, or dynamic registration (plugin readiness)
- **Severity:** Medium (High when Plugin SDK is scheduled)
- **Evidence:** `_register_all()` hand-registers every tool in one method with flat names (`tools.py:333-350`). No namespace, no per-tool permission/scope, no capability gating, no registration API.
- **Impact:** A Plugin SDK needs third-party tools to register dynamically, be namespaced, and be scoped/sandboxed. The current registry is a closed, trusted, flat set — the opposite shape.
- **Recommendation:** When Plugins are scheduled, evolve `ToolRegistry` into a namespaced, permissioned registry with a registration contract. Keep the bounded-surface guarantee of ADR-005 (plugins register *bounded* tools, never an escape hatch).

### Finding 8 — Unbounded table growth (TD-019) largely unaddressed
- **Severity:** Medium
- **Evidence:** Only `events` has a retention function, and it isn't scheduled (`database.py:55-71`); the other nine tables grow indefinitely (TD-019). A real incident already occurred: 131 stale `tasks` rows + 126 dependent `questions` rows (ADR-018 Context).
- **Impact:** An always-on assistant runs unattended for long periods — the exact condition this defers. Query degradation and the "stale data pollutes reasoning" failure mode both compound.
- **Recommendation:** Wire `purge_events_older_than` to the scheduler and extend a retention/rollup policy to `conversations`, `tasks`, `opencode_tasks`. Couple this with the Memory subsystem (Finding 1) — summarize-then-purge is the natural rollup.

### Finding 9 — Module-global singletons encode the single-process assumption
- **Severity:** Medium
- **Evidence:** `_broadcast_hook`, `_active_turns`, and the LLM provider are module/instance globals (`supervisor.py:43,52,113`); `set_broadcast_hook` mutates module state. The conftest autouse fixture that resets these between tests (ADR-018 Related Files) is itself evidence of the coupling.
- **Impact:** Single-process is baked in. Any future process split (a dedicated planner, a plugin host) inherits shared-mutable-global friction, and tests already pay a tax.
- **Recommendation:** Not urgent. When the first process split is contemplated, move these onto an explicit application context/object rather than module globals.

### Finding 10 — Single-device assumptions are already in the connection layer
- **Severity:** Medium (for multi-device)
- **Evidence:** `get_latest_device_status` returns `next(iter(self._device_status.values()))` — "the phone" == "any one device" (`connection_manager.py:107-116`); heartbeat is recorded globally, not per-connection (`connection_manager.py:118-125`, and its own comment says so); TD-025 is a single global `currentTurn` slot that silently drops a concurrent turn.
- **Impact:** "Coordinate multiple devices" requires per-device identity and routing that the connection layer explicitly does not have yet (it stores `device_status` but "not yet read by anything").
- **Recommendation:** Before multi-device coordination, give `ConnectionManager` per-device addressing (route to device N), not "latest wins." The primitive (`_device_status` keyed by connection) is there; the routing is not.

### Finding 11 — Permission/Question conflation in one table (TD-006)
- **Severity:** Medium
- **Evidence:** Permissions are stored as `questions` rows with a `"Permission:"` text prefix (`tools.py:80`, `supervisor.py:630,661`). String-prefix branching decides type throughout.
- **Impact:** Two semantically distinct concepts share a schema and are disambiguated by string parsing — fragile as permission handling grows (e.g. richer permission types for plugins/delegated agents).
- **Recommendation:** Split into a `permissions` table at the next schema-migration pass. Low functional risk today, compounding clarity cost.

### Finding 12 — Process/store proliferation needs a stated complexity budget
- **Severity:** Low–Medium (governance)
- **Evidence:** The system now runs the main app + a separate Operations console process (`operations_console.py:300`, binds `127.0.0.1`) and a second SQLite file (`operations.db`).
- **Impact:** The Operations *process* is genuinely forced — a process cannot restart itself, so that split is correct, not gratuitous. The concern is the *pattern*: the read/write-purity instinct (ADR-019) plus "each subsystem gets its own store/process" could, extrapolated, yield a constellation of processes for a single-user machine.
- **Recommendation:** The principle is sound and *today's* instances are justified — keep them. Add an explicit rule to the governance docs: a new subsystem earns its own process only when it genuinely cannot live in-process (self-restart, isolation-critical), and its own store only when it must survive the main DB being unavailable. Prevent proliferation by policy, not case-by-case.

### Finding 13 — Delegated result capture is a single unstructured column
- **Severity:** Low (Medium for the Artifacts milestone)
- **Evidence:** A completed task's result is one `result_summary` TEXT column on `opencode_tasks` (`database.py:444`).
- **Impact:** Good enough for "what did it find," but the Artifact Management milestone (named in ADR-020) needs structured artifacts (files changed, diffs, produced outputs) — a column won't hold them.
- **Recommendation:** When Artifacts is scheduled, add an `artifacts` table keyed by `trace_id` (per ADR-020's invariant). Leave `result_summary` as the human-readable rollup.

### Finding 14 — LLM provider abstraction is thin and single-model
- **Severity:** Low (Medium for multi-LLM)
- **Evidence:** `_configure_llm` picks one `LLMProvider` or `FakeLLMProvider` (`supervisor.py:116-122`); no per-task model routing.
- **Impact:** "Multiple LLMs" (cheap model for classification, strong model for planning) has no seam. Cost policy (ADR-009) exists for OpenCode but not for the Supervisor's own model choice.
- **Recommendation:** Introduce a model-router when the second model arrives; not before. Note it in the roadmap so the planner (Finding 2) is designed model-agnostic.

### Finding 15 — No turn-level idempotency / dedup
- **Severity:** Low
- **Evidence:** `client_request_id` correlates session-*open* (ADR-017) but is not carried on per-turn messages; `process_message` mints a fresh `trace_id` every call (`supervisor.py:155`). A retried/duplicated user message is reprocessed as new work. ADR-020 lists client-seeded `trace_id` as a deferred revisit.
- **Impact:** On flaky mobile networks (the actual deployment), a re-sent frame could double-execute a tool (e.g. start two tasks).
- **Recommendation:** Carry `client_request_id` on turn messages and dedup at `process_message` entry, closing the loop ADR-020 left open.

### Finding 16 — OpenCode uses dual SSE + poll mechanisms
- **Severity:** Low
- **Evidence:** Both `_sse_loop` and `_poll_loop` run concurrently (`opencode_supervisor.py:502,517`), both writing DB.
- **Impact:** Belt-and-suspenders reliability (defensible), but two event paths + two writers add complexity and contention (compounds Finding 3b). SSE reconnect has no dedicated test (TD-011).
- **Recommendation:** Keep both if reliability demands it, but document which is authoritative and add the missing reconnect test before relying on it more heavily.

### Finding 17 — `reconcile_on_startup` doesn't use the working endpoints (TD-008)
- **Severity:** Low
- **Evidence:** Degraded-task reconciliation still uses the old `GET /session/{id}` check, never upgraded to the message-history endpoint (TD-008; `opencode_supervisor.py:233`).
- **Impact:** Tasks left `degraded` by a Jarvis restart may never reconcile even when they now could — directly relevant to "recover from failures."
- **Recommendation:** Fold into the next OpenCode-adapter pass; small, self-contained.

### Finding 18 — `_require_localhost` trusts `request.client.host`
- **Severity:** Low (High *if* remote/proxy is ever introduced)
- **Evidence:** The gate compares `request.client.host` to localhost (`operations.py:50`). Correct under direct uvicorn, but spoofable if a reverse proxy or `--proxy-headers`/`X-Forwarded-For` handling is ever added.
- **Impact:** None today. Becomes a real hole the moment TD-003 (remote reachability) puts anything in front of the app.
- **Recommendation:** Add a one-line note to ADR-022/TD-018: this gate assumes a direct bind; revisit it as part of any remote-reachability work, never loosen it silently.

### Finding 19 — Context assembly is static and unranked
- **Severity:** Low (rising with scale)
- **Evidence:** `build_context` + `_format_context` dump *all* projects, active tasks, pending questions/permissions, running OpenCode tasks, and recent activity into the prompt every turn (`supervisor.py:767-805`).
- **Impact:** As state and tools grow (memory, more workers, plugins), the prompt bloats unboundedly — token cost and relevance both degrade. This is where Memory (Finding 1) and context assembly must meet.
- **Recommendation:** Move to relevance-selected context (retrieve what this turn needs) as part of the Memory subsystem, rather than dumping full state.

### Finding 20 — `push_subscriptions` write-ownership violates single-owning-module (TD-001)
- **Severity:** Low
- **Evidence:** `push_subscriptions` is written from `main.py` but read from `push.py`/`notifications.py` (TD-001) — the only table without a single owning module.
- **Impact:** Minor today; sets a precedent that erodes the "one owning module per table" principle the data layer otherwise follows cleanly — worth protecting as more tables (memory, plans, artifacts) arrive.
- **Recommendation:** Move the write into `push.py` at the next notifications pass; more importantly, *hold the line* on single-ownership for every new table.

---

## 7. Architectural Risks

### Immediate (0–3 months)
- **Building Supervisor Intelligence on the turn loop as-is** (Findings 1, 2). The single highest risk: bolting planning onto a stateless 5-call loop with no plan object and no memory will produce a brittle result that has to be undone. Address the foundations first.
- **A single unguarded WebSocket frame dropping live sessions** (Finding 5) — small, real, cheap to fix, and directly undermines "always available."
- **Stale-data-pollutes-reasoning recurrence** (Finding 8) — already happened once (ADR-018 Context); grows with unattended runtime.

### Medium (3–12 months)
- **Event-loop contention** as proactive/background work lands (Findings 3a/3b). Foreground voice latency is the visible symptom.
- **Second worker or second LLM** exposing the singular wiring (Findings 4, 14) — every added `if`-by-type is debt.
- **Multi-device coordination** hitting the "latest wins" connection layer (Finding 10).

### Long-term (1–3 years)
- **Plugin SDK** against a closed, flat, trusted tool registry (Finding 7) — a redesign if not seam-prepared.
- **Process/store proliferation** without a budget (Finding 12) turning a single-user machine into a distributed system by accident.
- **Unbounded data** (Finding 8) becoming a genuine operational load rather than a hypothetical.

---

## 8. Refactoring Roadmap (ordered by ROI)

1. **Per-message error isolation in the WS loop** (Finding 5). Hours of work; directly protects availability. Do first.
2. **Async/threaded DB access behind `app/database.py`** (Finding 3a). Localized to one module boundary; unblocks the event loop for everything that follows.
3. **Explicit write-concurrency handling for SQLite** (Finding 3b). `busy_timeout`/single-writer discipline before background writers multiply.
4. **Memory subsystem v1** (Finding 1). The highest-value *new* capability; gates proactive/long-conversation milestones. Design with retention/rollup (Finding 8) folded in.
5. **Plan/goal layer above the turn** (Finding 2). The prerequisite for Supervisor Intelligence.
6. **Worker/Agent registry + LLM router seam** (Findings 4, 14). Design the seam now; populate when the second worker/model arrives.
7. **Dependency injection into `ToolRegistry`** (Finding 6) and **schema hygiene** (Findings 11, 20). Cheap, compounding clarity wins.
8. **Plugin-ready tool registry** (Finding 7) — only when Plugins is actually scheduled.

Items 1–3 are pure foundation and unlock everything else; 4–5 are the vision-critical new subsystems; 6+ are seam-preparation and hygiene.

---

## 9. Things to change BEFORE implementing Supervisor Intelligence

1. **Introduce a plan/goal correlation tier above `trace_id`** (Finding 2). Supervisor Intelligence *is* multi-turn orchestration; it has no object to hang state on today.
2. **Stand up Memory v1** (Finding 1). Intelligent supervision that can't remember what it decided last turn isn't intelligent. At minimum: durable decisions/summaries + retrieval into context.
3. **Make DB access non-blocking** (Finding 3a). A planner will issue many more DB reads/writes per user-visible action; blocking the loop turns that into latency.
4. **Add per-message error isolation** (Finding 5). Longer autonomous sequences mean more places to throw; one throw shouldn't drop the session.
5. **Define the Worker/LLM-router seam** (Findings 4, 14) — even if only OpenCode/one model is wired, design the planner to dispatch through a seam, not a hardcoded `self._oc`.

These five are foundation, not features. Doing Supervisor Intelligence without them means building the interesting part on sand.

## 10. Things to intentionally leave unchanged

1. **The ADR/governance discipline and the debt register.** This is the system's best asset. Keep writing ADRs before building.
2. **`trace_id` and the ContextVar propagation model (ADR-020).** Correct and forward-compatible. Extend it upward (goal_id), never replace it.
3. **Supervisor-routes / workers-execute (ADR-005).** The right boundary; do not let the planner or plugins erode it into direct execution.
4. **Read-only Control Center vs. write-capable Operations (ADR-018/019).** A genuinely valuable boundary — do not merge them for convenience.
5. **The Operations process split and its localhost-only, confirm-gated, separate-store model (ADR-022/023).** The self-restart problem forces the process split; the security model is proportionate and verified. Keep it.
6. **Laptop-is-the-brain / thin clients (ADR-001).** The single decision that keeps reasoning consistent across an expanding client surface. Foundational; keep it.
7. **SQLite as the store.** Right choice for local-first — fix *how* it's accessed (Findings 3a/3b), not *that* it's used.

---

## 11. Overall Verdict

**If this were my project, would I continue building on this architecture? Yes — with a sequencing condition.**

I would continue because the things that are *expensive to fix* are already right: the reasoning boundary (ADR-005), the correlation model (ADR-020), the observability/operations split (ADR-018/019), the trust model (ADR-001), and above all the culture of deciding-in-writing-with-evidence. Those are the load-bearing walls, and they're sound. The things that are *missing* — memory, a plan layer, async DB access, a worker/LLM seam — are all **additive**. Nothing needs to be torn down. That is the rare, good position: the foundation is correct and the remaining work builds *on* it rather than *against* it.

The condition is sequencing. The strongest temptation this codebase creates is to read its excellent governance as readiness and jump straight to Supervisor Intelligence. It is not ready for that yet — not because anything is broken, but because the turn-scoped, stateless, memoryless, single-worker execution core is the wrong shape to hang planning on, and building planning first would mean rebuilding it. Do the five foundation items in §9 first. Then Supervisor Intelligence, Memory, and Planning will fit the way `trace_id` fit: additively, cleanly, and provably.

This is a 7/10 architecture with a 9/10 trajectory — provided the next milestone is foundation, not features.
