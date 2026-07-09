# Jarvis Engineering Session Log

## Project Vision

Jarvis is a laptop-resident supervisor agent that the user communicates with from a phone. It supervises applications, coding agents, tasks, internet research, and eventually broader computer workflows. The phone is a conversational supervision interface rather than a traditional remote desktop.

### Intended Interaction Model

```
User → Phone → Jarvis Supervisor → Application/Agent Adapters → Laptop Tools and Internet
```

Jarvis should:
- accept goals from the user
- delegate work
- observe progress
- detect questions and permission requests
- contact the user when human input is required
- relay answers back to the same task/session
- report completion, failure, or important state changes
- maintain persistent project and task context
- use structured application interfaces where available
- use GUI/vision control only as a fallback

## Architecture Principles

- **Local-first supervisor**: All logic runs on the laptop; no cloud dependency for core supervision.
- **Phone as communication interface, not remote desktop**: The phone UI is a chat-like supervision panel, not a screen-sharing or remote-control tool.
- **Structured APIs over terminal scraping**: Prefer documented REST, JSON-RPC, SSE, or native event interfaces over parsing terminal output, ANSI codes, or screen content.
- **Control priority**: API → CLI → application scripting → accessibility → vision/mouse (descending preference).
- **Jarvis as supervisor, not primary worker**: Jarvis delegates tasks to workers/agents and supervises their execution; it does not do the work itself.
- **Bounded autonomy**: Workers run with defined scope; questions and permissions gate operations requiring human judgment.
- **Questions and permissions are distinct concepts**: Questions ask for a decision (e.g., "Which approach?"); permissions request approval for an action (e.g., "Write to /etc/config"). They use different API endpoints and different UI rendering in Jarvis.
- **Persistent task/session mapping**: Each Jarvis task maps to an external session ID (e.g., OpenCode session_id); the mapping survives server restart.
- **Tasks continue independently of phone connectivity**: Workers run until completion, question, or error regardless of WebSocket state.
- **Event-driven supervision**: SSE streams (or equivalent) provide real-time observation; polling is a fallback.
- **Normalized internal Jarvis event model**: External events (question_created, permission.requested, message.delta) are converted to a common Jarvis event format before broadcast.
- **No arbitrary shell execution in early milestones**: Milestones 1–3 use only predefined `/demo-task` and `/mock-agent` subprocesses.
- **Safe project aliases**: Phone-supplied filesystem paths are not used directly in early milestones; directory selection is explicit via command arguments.
- **Assist mode as intended default autonomy model**: The user approves operations within a bounded context, rather than fully autonomous or fully manual control.

## Milestone Timeline

### Milestone 1 — Basic Phone-to-Laptop Loop

- **Timestamps**: Not precisely recoverable (no git history, no file timestamps recorded for M1)
- **Original goal**: Establish a WebSocket-based chat interface between a phone browser and a laptop FastAPI server, with basic command routing.
- **Files created**:
  - `app/main.py` — FastAPI server with WebSocket endpoint at `/ws`, static file serving
  - `app/connection_manager.py` — WebSocket broadcast set
  - `app/executor.py` — Command routing (`/status`, `/processes`, `/pwd`)
  - `app/database.py` — SQLite persistence (`events` table)
  - `app/models.py` — `EventType`, event constants
  - `app/static/index.html` — Mobile-optimized chat HTML
  - `app/static/app.js` — WebSocket client with timeline rendering
  - `app/static/style.css` — Dark-theme mobile styles
- **Commands implemented**: `/status`, `/processes`, `/pwd`
- **Event flow**: User types message → WebSocket → `main.py` → `executor.py` → response events broadcast to all clients
- **Tests**: None at this milestone
- **Known limitations at completion**: No auth, no TLS, LAN-only, single-user, no task system, no persistence beyond events
- **Important decisions**: WebSocket chosen over polling for real-time bidirectional communication; SQLite chosen for zero-config persistence; FastAPI chosen for async-native Python web framework

### Milestone 2 — Long-Running Task Supervision

- **Timestamps**: Not precisely recoverable
- **Original goal**: Add supervised subprocess execution with stdout/stderr streaming, task persistence, cancellation, and an Active Tasks UI.
- **Files created**:
  - `app/task_manager.py` — Async subprocess lifecycle, stream readers, exit monitoring, cancellation
  - `tests/test_tasks.py` — 8 pytest tests
- **Files modified**:
  - `app/database.py` — Added `tasks` table, CRUD functions, `mark_running_tasks_interrupted()`
  - `app/executor.py` — Added `/demo-task`, `/tasks`, `/cancel`
  - `app/main.py` — Startup/shutdown task management, running tasks and pending questions sent on WebSocket connect
  - `app/static/app.js` — Active Tasks panel with name, elapsed time, latest output line, Cancel button
  - `app/static/index.html` — Active Tasks section
  - `app/static/style.css` — Task item styles
- **Architecture**:
  - `TaskManager` owns subprocess lifecycle: `_processes`, `_stdins`, `_readers`, `_cancelled`
  - Stream readers (`_read_stream`) read stdout/stderr line by line and broadcast events
  - `_monitor_exit` waits for process exit, then emits completion/failure
  - Cancellation: `terminate()` → 3s timeout → `kill()`
  - On server shutdown, all running processes are cancelled; on startup, tasks left as `running` are marked `failed`
- **Key behaviors discovered/fixed**:
  - Race condition: `_monitor_exit` could fire before `_readers` were fully populated → readers are now stored then gathered on exit
  - Cancellation must drain readers before cleanup to avoid orphan tasks
  - Process termination is best-effort on Windows (some processes ignore SIGTERM)
- **Test result**: 8/8 automated tests passing
- **Manual smoke test**: Verified via manual WebSocket interaction (no formal smoke test script at this point)

### Milestone 3 — Human Supervision Loop

- **Timestamps**: Not precisely recoverable
- **Original goal**: Allow interactive workers to ask questions, receive answers via stdin, and wait for human input. Introduce the `JARVIS_QUESTION:` protocol, the Needs Your Attention UI, and reconnection restoration.
- **Files created**:
  - `app/protocol.py` — `parse_line()` for `JARVIS_QUESTION:` prefix detection
  - `app/workers/mock_worker.py` — Deterministic interactive subprocess (progress → question → stdin read → complete)
  - `tests/test_questions.py` — 16 pytest tests
  - `tests/smoke_test.py` — WebSocket-based manual smoke test (3 scenarios)
  - `tests/browser_smoke.py` — Playwright browser smoke test (3 scenarios)
- **Files modified**:
  - `app/database.py` — Added `questions` table, CRUD functions
  - `app/task_manager.py` — Question detection in stdout stream, `_handle_question()`, `answer_question()`, stdin write, cancellation while waiting, `_pending_questions` tracking
  - `app/executor.py` — Added `/mock-agent`, `/answer`, `/attention`
  - `app/static/app.js` — Needs Your Attention section, option buttons, custom answer input, pending question tracking
  - `app/static/index.html` — Needs Your Attention section
  - `app/static/style.css` — Question/option styles
- **Architecture**:
  - `JARVIS_QUESTION:` prefix detected in `_read_stream` → `_handle_question()` → persist + broadcast
  - Task status transitions to `waiting_for_user`
  - Stdin write via `_stdins[task_id].write()`
  - Questions persisted in `questions` table; only `pending` questions restored on reconnect
  - `mark_running_tasks_interrupted()` also cancels pending questions
  - `/cancel` during `waiting_for_user` cancels question + terminates process
- **Test results**:
  - Python test suite: **24/24 passed** (8 task tests + 16 question tests)
  - Browser smoke test (initial run): 2 failures, 1 pass
- **Browser smoke issues discovered and fixed**:

  | Issue | Root cause | Fix |
  |-------|-----------|-----|
  | Async predicate returning generator instead of bool | `wait_until()` checked truthiness of async generator, not boolean | Added explicit `await` in predicate or added `return` keyword |
  | `.first` targeting wrong task's answer button | Both workers had identical question text; `.first` always hit worker 1 | Changed to `items.nth(0)` and `items.nth(1)` for explicit indexing |
  | Cross-scenario pending-question state leak | Scenario A left a question pending; scenario B inherited it | Added `cleanup_tasks()` to cancel all tasks between scenarios |

  - After fixes: **Browser smoke test: 19/19 PASS, 0 failures**
- **Known limitations at completion**:
  - No timeout for unanswered questions
  - Answer delivery via stdin is best-effort
  - No way to retract an answer
  - Mock worker uses fixed delays (1s normal, 0.1s test mode)
- **Important decisions**: The `JARVIS_QUESTION:` protocol is a structured JSON payload on stdout (not stderr, not a separate channel). This allows existing subprocess supervision to detect questions without changes to the subprocess launch code.

### Milestone 4 Reconnaissance — OpenCode Integration Surface

- **Timestamps**: Reconnaissance performed in a dedicated experiment directory at `C:\Users\Admin\AppData\Local\Temp\jarvis-m4-recon/`
- **Date**: 2026-07-08 (file timestamps from probe scripts and report)
- **Original goal**: Investigate how to integrate Jarvis with the locally installed OpenCode (v1.15.10) supervision loop, evaluating all available integration surfaces.

#### Installed OpenCode Environment

| Property | Value |
|----------|-------|
| **Version** | 1.15.10 |
| **Executable** | `C:\Users\Admin\.bun\bin\opencode.exe` |
| **Installation method** | Bun via `opencode.ai` install script |
| **Provider** | OpenRouter (API key in `auth.json`, never recorded in project files) |
| **Database** | `C:\Users\Admin\.local\share\opencode\opencode.db` (SQLite) |
| **Config** | `C:\Users\Admin\.config\opencode\opencode.jsonc` |

#### Integration Surfaces Investigated

1. **`opencode serve` (HTTP Server API)** — Score: **47/50**
   - REST API with OpenAPI 3.1 spec at `/doc`
   - SSE event stream at `GET /global/event`
   - Dedicated question endpoints: `GET /question`, `POST /question/{requestID}/reply`, `POST /question/{requestID}/reject`
   - Dedicated permission endpoints: `GET /permission`, `POST /permission/{requestID}/reply`
   - Session CRUD, abort, fork
   - HTTP basic auth (`OPENCODE_SERVER_PASSWORD`)
   - Sessions persist in SQLite across restarts
   - One server manages all sessions concurrently

2. **`opencode acp` (ACP Protocol)** — Score: **40/50**
   - JSON-RPC 2.0 over stdio (ND-JSON)
   - Verified working: handshake completed, agent capabilities discovered
   - All key features (session create, prompt, close) available
   - No TTY required
   - Requires implementing ACP client-side handlers for questions/permissions

3. **`opencode run --format json` (ND-JSON CLI)** — Score: **25/50**
   - ND-JSON event output
   - Cannot handle question/permission loops (fire-and-forget)
   - No API for answering questions
   - Blocked by "Session not found" error in experiment directory

#### Key Verified API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/global/health` | Health check |
| `POST` | `/session` | Create session |
| `GET` | `/session/{sessionID}` | Get session details |
| `POST` | `/session/{sessionID}/prompt_async` | Send async prompt |
| `POST` | `/session/{sessionID}/abort` | Abort session |
| `GET` | `/question` | List pending questions |
| `POST` | `/question/{requestID}/reply` | Answer a question |
| `POST` | `/question/{requestID}/reject` | Reject a question |
| `GET` | `/permission` | List pending permissions |
| `POST` | `/permission/{requestID}/reply` | Approve/deny permission |
| `GET` | `/global/event` | SSE event stream |
| `GET` | `/api/session/{sessionID}/message` | Get session messages |

#### Permission Default Semantics

The reconnaissance established that the default permission policy is `deny` for `question`, `plan_enter`, and `plan_exit`. This was verified from server logs. This means all question/permission requests are presented to the client for manual approval — exactly what Jarvis needs.

#### Recommendation

Use `opencode serve` as the primary integration surface (REST for commands, SSE for observation). Fall back to ACP only if server mode is unavailable. Reject terminal scraping, PTY control, and `opencode run --format json` as primary integration methods.

### Milestone 4 — Real OpenCode Supervision (IMPLEMENTED)

- **Timestamps**: Implementation occurred 2026-07-08. All phases completed in a single engineering session.
- **Original goal**: Build the OpenCode supervision adapter using `opencode serve`, REST APIs, and SSE events, enabling Jarvis to supervise real OpenCode sessions with native question and permission handling.

#### Files Created

| File | Purpose |
|------|---------|
| `app/integrations/__init__.py` | Package marker |
| `app/integrations/opencode_server.py` | Managed subprocess for `opencode serve` with auto-restart |
| `app/integrations/opencode_adapter.py` | HTTP client for verified REST API endpoints + SSE consumer |
| `app/integrations/opencode_events.py` | Normalizes native OC events into Jarvis internal event format |
| `app/integrations/opencode_supervisor.py` | Orchestrator tying server, adapter, DB, and task manager together |
| `tests/test_opencode.py` | 13 unit tests with in-process FakeOpenCodeServer |

#### Files Modified

| File | Changes |
|------|---------|
| `app/main.py` | Added `OpenCodeSupervisor`; startup starts server + SSE/poll loops; shutdown stops supervisor; WebSocket reconnect sends opencode status |
| `app/executor.py` | Added 6 new commands: `/opencode-start`, `/opencode-cancel`, `/opencode-answer`, `/opencode-reject`, `/opencode-permit`, `/opencode-status` |
| `app/database.py` | Added `opencode_tasks` table (task_id, session_id, project_dir, instruction, status, timestamps) + CRUD functions + `mark_running_opencode_tasks_interrupted()` |
| `app/static/app.js` | Permission approve/deny buttons, OpenCode status indicator, new event type handlers (opencode_message, opencode_task_completed, task_permission, etc.) |
| `app/static/style.css` | Permission button styles (`.permission-approve-btn`, `.permission-deny-btn`) |

#### Architecture

```
OpenCodeServerManager (subprocess lifecycle)
        │
        ▼
OpenCodeAdapter (HTTP client for REST API + SSE)
        │
        ▼
OpenCodeSupervisor (orchestrator)
        │
        ├── SSE loop (real-time event consumption)
        ├── Poll loop (backup question/permission detection, 3s interval)
        └── Session management (start, cancel, answer, reject, permit)
        │
        └── TaskManager (existing)
              └── ConnectionManager → WebSocket clients
```

- **`OpenCodeServerManager`**: Starts `opencode serve --port <port>` as a managed subprocess, polls `/global/health` every 15s, auto-restarts on failure.
- **`OpenCodeAdapter`**: Stateless HTTP client with methods for all verified endpoints. Builds Basic auth from `OPENCODE_SERVER_USERNAME` and `OPENCODE_SERVER_PASSWORD` env vars. SSE consumer with exponential backoff reconnection.
- **`OpenCodeEvents`**: Pure functions that normalize OpenCode event payloads into Jarvis internal format. `normalize_question()`, `normalize_permission()`, `normalize_message()`, `process_sse_event()` for routing.
- **`OpenCodeSupervisor`**: High-level orchestrator. `start_session()` creates OC session + Jarvis task simultaneously, persists the mapping, sends the prompt asynchronously. Background `_sse_loop` consumes events; `_poll_loop` polls questions/permissions every 3s as backup. `_handle_sse_event` routes to appropriate emission methods.

#### REST Endpoints Used

- `GET /global/health` — health check
- `POST /session` — create session (body: `{}`)
- `GET /session/{sessionID}` — get session info
- `POST /session/{sessionID}/prompt_async` — send instruction (body: `{"parts": [{"type": "text", "text": "..."}]}`)
- `POST /session/{sessionID}/abort` — cancel session
- `GET /question?directory=<path>` — poll pending questions
- `POST /question/{requestID}/reply` — answer question
- `POST /question/{requestID}/reject` — reject question
- `GET /permission?directory=<path>` — poll pending permissions
- `POST /permission/{requestID}/reply` — approve/deny permission
- `GET /api/session/{sessionID}/message` — get messages
- `GET /global/event?directory=<path>` — SSE event stream
- `POST /global/dispose` — graceful server shutdown

All session-scoped endpoints require the `directory` query parameter.

#### SSE Connection and Routing

- `consume_events()` opens `GET /global/event?directory=*` as an SSE stream
- Streams bytes → buffers until `\n\n` → parses `event:` and `data:` lines → yields dicts
- Exponential backoff on connection loss (1s → 2s → 4s → ... → max 30s)
- Supervisor `_sse_loop` calls `_handle_sse_event()` for each event
- Event types handled: `question`, `question_created`, `permission`, `permission_created`, `message`, `message_created`, `session_status` (with completed/failed/cancelled), `error`

#### Task ID ↔ Session ID Mapping

- New DB table `opencode_tasks` with columns: `id`, `task_id` (FK to `tasks.task_id`), `session_id`, `project_dir`, `instruction`, `status`, `created_at`, `updated_at`
- Jarvis task IDs are generated as `oc_<12 hex chars>` (e.g., `oc_a1b2c3d4e5f6`)
- Lookup: `get_opencode_task(task_id)` → gets mapping by Jarvis ID; `get_opencode_task_by_session(session_id)` → gets mapping by OC session ID
- On startup, `mark_running_opencode_tasks_interrupted()` marks any left-as-running as `failed`

#### Question Normalization and Answer Flow

1. SSE event `question` (or poll detected question) → `normalize_question()` → `_emit_question()`
2. `_emit_question()` creates Jarvis question record in `questions` table, broadcasts `task_question` event with source `"opencode"`
3. Task status transitions to `waiting_for_user`
4. User answers via `/opencode-answer <question_id> <answer>` or clicks option button in UI
5. `answer_question()` calls `POST /question/{requestID}/reply` on OpenCode, updates Jarvis records

#### Permission Normalization and Approval/Rejection Flow

1. SSE event `permission` (or poll detected) → `normalize_permission()` → `_emit_permission()`
2. `_emit_permission()` creates Jarvis question record with text `"Permission: <action> <path>"`, broadcasts `task_permission` event
3. Task status transitions to `waiting_for_user`
4. User approves/denies via `/opencode-permit <permission_id> approve|deny` or Approve/Deny buttons in UI
5. `approve_permission()` calls `POST /permission/{requestID}/reply`, updates Jarvis records

#### Cancellation Behavior

- `/opencode-cancel <task_id>` → `cancel_session()` → calls `POST /session/{sessionID}/abort` → updates both `tasks` and `opencode_tasks` status to `cancelled`
- Sets completion event for waiters
- Broadcasts `opencode_task_cancelled`

#### Phone Reconnect Restoration

On WebSocket connect, `main.py` sends:
- Event history (last 100 events) via existing mechanism
- Running tasks (existing)
- Pending questions (existing)
- OpenCode status: `{"type": "opencode_status", "server_alive": bool, "running_tasks": [...], "pending_questions": [...]}`
- OpenCode tasks with status `running` are re-polled via the background poll loop

#### Jarvis Restart Reconciliation

- `mark_running_opencode_tasks_interrupted()` runs in `startup()`, setting any `opencode_tasks` left as `running` or `pending` to `failed`
- The OpenCode server is restarted on each Jarvis startup
- OpenCode sessions themselves persist in the OpenCode DB and can be resumed, but Jarvis's task mapping is reconciled via the interrupted marking

#### Multiple-Session Isolation

- Each OpenCode task creates a separate OpenCode session via `POST /session`
- Sessions are isolated by `sessionID` and `project_dir` in OpenCode
- The `opencode_tasks` table maps each Jarvis task to exactly one OpenCode session
- The poll loop iterates over all running tasks' project directories

#### Event Filtering/Deduplication

- Poll loop checks `db.get_question_record(request_id)` before creating a new question record — prevents duplicate emission if SSE and poll both detect the same question
- Questions are stored with OpenCode's `requestID` as the Jarvis `question_id`

#### Configuration Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENCODE_SERVER_PASSWORD` | (required) | Server basic auth password |
| `OPENCODE_SERVER_USERNAME` | `opencode` | Server basic auth username |
| `JARVIS_OPENCODE_PORT` | `4097` | Port for `opencode serve` |
| `JARVIS_OPENCODE_EXE` | `C:\Users\Admin\.bun\bin\opencode.exe` | Path to opencode binary |

#### Known Limitations

- `OPENCODE_SERVER_PASSWORD` must be set in the environment before Jarvis starts
- Server auto-restart on health check failure was implemented but not tested in production conditions
- SSE event stream uses `directory=*` as the parameter — the exact SSE behavior with `*` was not verified against the real server (the survey script used `directory=<explicit_path>`)
- Permission handling stores permissions in the `questions` table with a prefixed question text rather than a separate permissions table — this works but conflates two semantically distinct concepts in the database
- The poll loop runs every 3 seconds regardless of SSE health; this is a safety fallback
- `/opencode-permit` has a redundant `db.get_opencode_task_by_session("")` call in `approve_permission()` that should be cleaned up (it looks up by empty session ID, then also by task_id from the record)
- OpenCode v1.15.10 was used for verification; API changes in future versions may require adapter updates
- If the OpenCode server crashes and restarts, in-flight sessions are lost (OpenCode does not automatically reconnect to sessions)

#### Test Results

- **13/13** new OpenCode unit tests pass
- **24/24** existing Milestone 1–3 tests pass (zero regression)
- **Total: 37/37 automated tests passing**
- **Real OpenCode integration smoke test**: Passed against `opencode v1.15.10` — server start, health check, session creation, prompt send, session info retrieval, abort, server stop — all verified
- The `httpx` package was added at test time but is not in `requirements.txt`; it is present in the environment

### Milestone 5 — Conversational Supervisor Layer (IMPLEMENTED)

- **Original goal**: Add an LLM-based supervisor that interprets natural-language user goals over the existing WebSocket chat and routes them to task/question/permission/OpenCode actions, per the design drafted at the end of Milestone 4.
- **Files created**:
  - `app/supervisor/__init__.py` — package marker
  - `app/supervisor/supervisor.py` — `Supervisor` orchestrator: fast-path deterministic status answers, LLM tool-call loop (max 5 tool calls/turn), conversation persistence
  - `app/supervisor/tools.py` — `ToolRegistry`: bounded tool set (`get_attention`, `list_tasks`, `get_task_status`, `start_opencode_task`, `send_opencode_instruction`, `answer_question`, `resolve_permission`, `cancel_task`, `recent_activity`, `get_projects`) backed by `TaskManager`/`OpenCodeSupervisor`
  - `app/supervisor/context.py` — `build_context()`: compact, bounded snapshot of active tasks, pending questions/permissions, OpenCode-running tasks, recent activity, conversation history, and safe project aliases, formatted for the LLM prompt
  - `app/supervisor/llm.py` — `LLMProvider` (OpenAI-compatible chat-completions client via `httpx`, no SDK dependency) + `FakeLLMProvider` (deterministic test double) + `validate_free_only_model()` / `ModelNotAllowedError` free-tier guard
  - `app/supervisor/projects.py` — safe project alias resolution (`resolve_project`, `get_projects`) reading `projects.json`, with exact/display-name/substring matching
  - `projects.json` — safe project registry; `jarvis-test` alias points at the sandboxed `tests/test_projects/safe-test` directory
  - `tests/test_supervisor.py` — supervisor/tool/context/LLM unit tests
  - `tests/test_env.py` — `.env` / `python-dotenv` loading and free-only-guard interaction tests
  - `tests/m5_scenarios.py`, `tests/m5_validate.py`, `tests/m5_validate2.py` — real end-to-end WebSocket validation scripts (not part of `pytest tests/`; require a live server + real or fake LLM)
  - `.env.example` — documents `JARVIS_LLM_PROVIDER`, `JARVIS_LLM_BASE_URL`, `JARVIS_LLM_MODEL`, `JARVIS_LLM_FREE_ONLY`, `JARVIS_LLM_API_KEY`
- **Files modified**:
  - `app/main.py` — non-slash WebSocket messages now route to `Supervisor.process_message()` instead of being ignored; emits `supervisor_thinking` then `supervisor_message` (with `conversation_id`) events
  - `app/database.py` — added `conversations` table + `save_conversation_message()`, `get_conversation_messages()`, `conversation_exists()`
  - `requirements.txt` — `httpx` now listed (resolves M4 known-limitation #13)
- **Architecture**:
  - Every non-slash user message either resolves via `_fast_path()` (deterministic string-matched status questions: attention/tasks/recent-activity — answered directly from the DB, no LLM call, no side effects) or goes through `Supervisor.process_message()`'s tool-call loop: build context → call LLM with tool definitions → execute any requested tool calls via `ToolRegistry` → feed results back → repeat until the LLM returns plain content or 5 tool calls are used.
  - `LLMProvider` speaks the OpenAI-compatible `/chat/completions` schema; `JARVIS_LLM_PROVIDER=openrouter` + `JARVIS_LLM_BASE_URL=https://openrouter.ai/api/v1` selects OpenRouter. `JARVIS_LLM_FREE_ONLY=true` rejects any model that is not `openrouter/free` or does not end in `:free` — enforced both at `LLMProvider` construction (sets `is_configured=False` and a `_rejection_reason`) and returns a "Configuration error" message rather than silently calling a paid model.
  - Every conversation turn is persisted to the `conversations` table (`user`, `assistant`, and `tool`-role rows); `main.py` does **not** carry a `conversation_id` across separate WebSocket messages, so each turn starts a fresh conversation — see Known Limitations.
  - `ToolRegistry.call()` never raises: `TypeError`/generic exceptions are caught and returned as `"Error: ..."` strings so a bad LLM tool call cannot crash the WebSocket handler.
- **Test results at implementation time**: full `pytest tests/` suite passing (exact M5-only count not separately recorded before this closure pass — see Validation Closure below for the authoritative full-suite result).

### Milestone 5 Validation Closure — 2026-07-09 02:29 IST

Closure pass: ran the full automated suite, investigated and fixed genuine M5 regressions found along the way, added the WebSocket double-encoding regression test, critically reviewed and strengthened the real end-to-end validation script, and ran that validation against a real local OpenCode server and a real free OpenRouter model.

#### OpenCode test server setup used

Jarvis's own `OpenCodeServerManager` (in `app/integrations/opencode_server.py`) manages the OpenCode test server — there is no separate manually-run instance. `tests/m5_validate2.py` starts the full Jarvis app (`uvicorn app.main:app`) as a subprocess; Jarvis's `startup()` hook then launches `opencode serve --port 4097` itself, cwd'd to the Jarvis project root, using the existing safe project registry (`projects.json`'s `jarvis-test` alias → `tests/test_projects/safe-test`, a sandboxed directory containing only `hello.py`/`README.md`/`src`). No new server configuration was introduced. Before the closure validation runs, a **stale `opencode.exe` process from a prior session** was found already bound to port 4097 with a mismatched password (401 on every request) — killed so Jarvis could start a clean instance. This happened again after each of the three validation runs in this closure pass: `OpenCodeSupervisor.stop()`/`OpenCodeServerManager.stop()` reliably terminates Jarvis's own `uvicorn` process but **does not terminate the underlying `opencode serve` child process**, which is left listening on port 4097 after Jarvis exits. Manually killed each time; recorded as a new known limitation below (pre-existing M4 code, out of M5 scope to fix here).

#### Full pytest suite result (before any M5 fixes — baseline)

```
pytest tests/ -v --tb=short
115 collected... (baseline: 108 collected)
108 passed, 0 failed, 0 skipped, 2 warnings, 186.43s (0:03:06)
```
The 2 warnings are both pre-existing/unrelated: a `PytestUnraisableExceptionWarning` from `_ProactorBasePipeTransport.__del__` during Windows asyncio teardown (documented since M4), and a `websockets.legacy` deprecation warning from the test dependency itself.

No test failures at baseline — every bug found below was discovered by **critically reviewing the real validation-script assertions and the real LLM/OpenCode run**, not by a failing pytest test. That is itself the finding: existing unit tests exercised these code paths only with full, untruncated IDs supplied directly by the test, so none of them caught the truncation or crash bugs.

#### Bug 1 — WebSocket double-encoding (regression test added; code already fixed)

- **Symptom**: supervisor/OpenCode notification broadcasts (e.g. `opencode_task_created`, `opencode_message`, `task_question`) would arrive at a WebSocket client as a JSON string literal containing escaped JSON text, instead of a JSON object — i.e. `"{\"type\": \"opencode_message\", ...}"` instead of `{"type": "opencode_message", ...}`. Any client doing `JSON.parse(event.data)` would get back a string, not an object, and `.type`/`.content` access would fail.
- **Root cause**: `app/connection_manager.py`'s `ConnectionManager.broadcast(data: dict)` already does its own `json.dumps(data)` before `ws.send_text(...)`. `OpenCodeSupervisor._notify_broadcast()` (in `app/integrations/opencode_supervisor.py`) previously called `self.cm.broadcast(json.dumps(msg))` — passing an already-serialized string into a broadcast layer that serializes again, double-encoding the payload.
- **File/function**: `app/integrations/opencode_supervisor.py::OpenCodeSupervisor._notify_broadcast`
- **Fix**: found already applied in the current code — `_notify_broadcast` now calls `self.cm.broadcast(msg)` with the raw dict, letting `ConnectionManager.broadcast()` own all serialization. Confirmed by direct verification: replaying the old buggy call pattern (`cm.broadcast(json.dumps(msg))`) against the real `ConnectionManager` reproduces a `str` on the client's `json.loads()`; the current code produces a `dict`.
- **Regression test**: `tests/test_opencode.py::test_notify_broadcast_sends_json_object_not_string` — instantiates a real `ConnectionManager` + `OpenCodeSupervisor` with a fake recording WebSocket, calls `_notify_broadcast()`, and asserts `json.loads(sent_text)` is a `dict` (not a `str`), with the specific fields intact. A companion test, `test_notify_broadcast_survives_disconnected_client`, confirms `_notify_broadcast` doesn't raise when there are no connected clients.

#### Bug 2 — task_id/question_id truncation broke every follow-up tool call (found and fixed)

- **Symptom**: discovered by critically reviewing Scenario D/J instead of trusting the loose original assertions, then confirmed against a real LLM session's server log (`server_err.log`, 2026-07-09 01:01–01:03 IST): the LLM called `send_opencode_instruction({"task_id":"oc_922206d97", ...})` and `cancel_task({"task_id":"oc_922206d97"})` twice each — but **no `POST /session/{id}/prompt_async` or `POST /session/{id}/abort` HTTP call was ever made** to OpenCode for those calls, meaning both silently failed.
- **Root cause**: `task_id` is `"oc_" + 12 hex chars` (15 chars total), but every LLM-facing surface (`start_opencode_task`'s success message, `get_attention`, `list_tasks`, `get_task_status`, and `build_context()`'s `active_tasks`/`opencode_running`/`pending_questions`) truncated it to `[:12]` for compact display — silently dropping the last 3 characters. `db.get_opencode_task()`/`db.get_task()`/`db.get_question_record()` all do exact-match `WHERE task_id=?` lookups, so an LLM echoing the truncated ID back (the *only* ID it is ever shown) always failed with `"is not an OpenCode task"`/`"not found"`. The same `[:12]` truncation applied to `question_id`, which would break `answer_question`/`resolve_permission` the same way.
- **Files/functions**: `app/supervisor/tools.py` (`_get_attention`, `_list_tasks`, `_get_task_status`, `_start_opencode_task`, `_send_opencode_instruction`, `_answer_question`), `app/supervisor/context.py` (`build_context`, `_check_pending_permissions`), `app/supervisor/supervisor.py` (`_fast_path`)
- **Fix**: removed `[:12]`/`[:16]` truncation everywhere `task_id`/`question_id` are used as the LLM's handle for a later tool call. Free-text fields (instructions, question text) and purely-cosmetic `session_id` display (never used as a lookup key by any tool) are left truncated.
- **Regression tests**: `tests/test_supervisor.py::test_start_opencode_task_reports_full_task_id`, `::test_follow_up_instruction_reaches_session_started_via_tool_output_id` (full round-trip: start a task, parse the ID out of the tool's own text response exactly as an LLM would, use it in `send_opencode_instruction`, assert the underlying adapter call actually fires), `::test_get_attention_reports_full_task_id_for_waiting_task`, `::test_context_builder_reports_full_task_and_question_ids`.

#### Bug 3 — fast-path "what needs my attention" crash on a waiting OpenCode task (found and fixed)

- **Symptom**: `_fast_path()`'s "what needs my attention" branch contained `f"...{'t'.get('instruction','')[:60]}..."` — calling `.get()` on the string literal `'t'` instead of the loop variable `t` (a dict). Reproduced directly: `AttributeError: 'str' object has no attribute 'get'`.
- **File/function**: `app/supervisor/supervisor.py::_fast_path`
- **Fix**: corrected to `t.get('instruction','')[:60]` (and dropped the now-redundant `[:12]` truncation of `t['task_id']` from Bug 2 in the same line).
- **Note**: this branch reads `db.get_opencode_running_tasks()`, which SQL-filters to `status IN ('running','pending')` — and nothing in the codebase ever sets `opencode_tasks.status` to `'waiting_for_user'` (only `tasks.status` gets that value, via `db.update_task_status` in `_emit_question`/`_emit_permission`). So this crash was **not reachable through the real DB pipeline** at the time of discovery — a separate, deeper gap (see Known Limitations) that also means the "N OpenCode task(s) are waiting" line in both `_fast_path` and `tools.py::_get_attention` never fires in practice; the working, tested path for "what needs my attention" is the `pending_questions` table, which is unaffected. Fixed anyway since it's unambiguously wrong code and would resurface the moment the status-propagation gap is closed.
- **Regression test**: `tests/test_supervisor.py::test_fast_path_attention_with_waiting_opencode_task_does_not_crash` (monkeypatches `db.get_opencode_running_tasks()` to directly exercise the formatting code, independent of the separate reachability gap) and `::test_get_attention_reports_full_task_id_for_waiting_task` for the equivalent `tools.py` branch (which did not have the crash, only the truncation).

#### Full pytest suite result (after fixes — final)

```
pytest tests/ -v --tb=short
115 collected, 115 passed, 0 failed, 0 skipped, 2 warnings, 175.14s (0:02:55)
```
+7 tests vs. baseline (2 double-encoding regression tests in `tests/test_opencode.py`, 5 truncation/crash regression tests in `tests/test_supervisor.py`). Same 2 pre-existing warnings as baseline.

#### m5_validate2.py changes reviewed and (where necessary) strengthened

- **Longer WebSocket timeout, idle-loop handling, `ConnectionClosed` handling**: accepted as-is — legitimate for real integration validation against a free-tier LLM with highly variable per-call latency (observed 2s–90s+ per call across scenarios in this closure pass) and multi-round tool loops.
- **Scenario D ("Tell it not to modify any files")**: the original assertion (`ok = bool(fm)`) only checked that *some* text came back — it did not check the instruction reached scenario B's session at all. Rewritten with a hard DB-verified gate: pull the turn's `conversation_id` out of the `supervisor_message` event, read back its persisted `tool`-role messages from the `conversations` table, and assert `send_opencode_instruction` was actually called with B's exact `task_id` and did not return an error. This is what M5 (the supervisor/tool-routing layer) is responsible for and can prove.
  - A second, independent check — cross-referencing OpenCode's own `GET /api/session/{id}/message` log for a new message containing the instruction text — was added and then **demoted from a hard gate to diagnostic-only** after direct verification (see Known Limitations: OpenCode message-log endpoint) showed it returns `{"items": []}` for every API-created session in this environment regardless of directory/projectID params or elapsed time, including sessions that received real, successfully-delivered prompts. It remains in the script's logged output for visibility but no longer fails the scenario.
  - Semantic wording checks (`"not modify"` / `"analysis only"`) are retained only inside that diagnostic, per the instruction to keep semantic-not-exact-string checks where they add value without letting wording variance gate pass/fail.
- **Scenario J ("Start working on nonexistent-project...")**: the original assertion only checked for one of ~20 broad honesty keywords in the reply text. Rewritten to hard-gate on: (1) `opencode_tasks` row count unchanged before/after (direct SQL `COUNT(*)`, not the Jarvis-reported list, which is itself status-filtered), (2) no phrase in the reply claims success (`"task started"`, `"session created"`, etc.), and (3) no `start_opencode_task` tool call in the turn's persisted tool-call log reported success. The original honesty-keyword check is retained but demoted to a non-gating diagnostic — this was empirically justified in the final validated run, where the real LLM phrased its refusal without matching *any* of the keyword list (used a non-ASCII hyphen in "nonexistent‑project") yet the three hard checks still correctly proved the required behavior.
- **Subprocess stdout/stderr**: `asyncio.create_subprocess_exec(...)` previously piped the managed Jarvis server's stdout/stderr via `asyncio.subprocess.PIPE` without ever draining them — a latent Windows asyncio risk (the OS pipe buffer can fill during a long, log-heavy run and stall the child). Changed to redirect to `m5_validate_server.log` in the project root instead, both removing that risk and giving a persistent server-side log for debugging future runs.

#### Real end-to-end validation result — 7/7 PASSED

Ran three times during this closure pass (first two runs surfaced the findings above and a monitoring false-alarm — see below — the third is the clean, final result with all fixes and script changes applied):

```
[PASS] A - Deterministic attention query
[PASS] B - Natural-language task creation
[PASS] C - Live status query
[PASS] D - Follow-up instruction reaches existing session
[PASS] H - Cancellation
[PASS] I - Browser disconnect/reconnect
[PASS] J - Failed action creates nothing and claims no success
7 passed / 0 failed / 7 total
```

- **LLM configuration**: real OpenRouter call, `JARVIS_LLM_PROVIDER=openrouter`, `JARVIS_LLM_MODEL=openrouter/free`, `JARVIS_LLM_FREE_ONLY=true` (enforced — a paid model would be rejected before any call). API key read from the local, gitignored `.env`; **no key value is recorded here or anywhere in this repo.**
- **Long LLM latency handling**: observed real per-call latency from ~1s (fast tool calls) up to several minutes for multi-round tool loops under free-tier load (Scenario B in the final run used its full 5-tool-call budget and returned "I've reached the maximum number of actions..." — accepted per the existing PASS criteria, since it still produced a task and no error). `cli.send()`'s 30s-per-recv / up-to-180s-total budget absorbed this correctly.
- **Monitoring false alarm**: during the second run, a scenario appeared stalled for several minutes based on a `Bash`-redirected log file that turned out to be significantly stale relative to the process's real progress (the process had actually already crashed inside Scenario D by the time the log file visibly showed only through Scenario B). The server was killed to investigate, which was a mistake — a direct WS probe would have shown the server was healthy. No product code was affected; this only cost validation time and is noted here as a process lesson, not a code finding.
- **Playwright/browser validation**: **not performed for M5.** The M3 Playwright browser smoke test (19/19 PASS, documented above) remains the only browser-DOM-level validation on record; it predates M5 and does not exercise the conversational supervisor path. `tests/m5_validate2.py` exercises the same WebSocket protocol the browser UI uses, but never drives an actual browser/DOM. This is recorded as a limitation, not fabricated as done.
- **Supervisor/environment pytest result referenced in the task brief ("71/71 passed")**: not reproducible from anything in this repository — the full suite at both baseline and after fixes was 108/115, not 71. Recorded honestly rather than silently substituted.

#### New discovery: OpenCode message-log endpoint (`GET /api/session/{id}/message`) unreliable against the real server

Direct probing during Scenario D investigation (2026-07-08, real `opencode v1.15.10` on port 4097): `GET /api/session/{id}/message` returns `{"items": [], "cursor": {...}}` for **every** API-created session tested, regardless of `directory`/`projectID` query params, and regardless of whether the session ever received a real, successfully-delivered prompt (confirmed via `GET /session` showing `cost:0, tokens: all 0` even for sessions from prior validation runs, hours old — contrasted with one genuinely interactive session, created outside the API via the `opencode` CLI/TUI directly, which does show real non-zero token usage). This endpoint was implemented and unit-tested only against the in-process `FakeOpenCodeServer` test double in M4 and was **never in M4's list of endpoints verified against the real server**. Whether this means OpenCode's own coding-agent turn never actually executes for API-created sessions, or whether message history is retrievable through some other undiscovered path, is unresolved and out of M5's scope to chase further. Scenario D was redesigned so it does not depend on this endpoint for pass/fail (see above). **This should be a priority investigation before relying on OpenCode-driven task results being real** — flagged prominently in Known Limitations below.

#### Remaining Milestone 5 limitations

- Whether OpenCode's own agent actually executes work for API-created sessions is unverified (see discovery above) — Jarvis can confirm its own REST calls succeed (HTTP 204) but currently has no reliable way to confirm OpenCode did anything with them.
- `OpenCodeSupervisor.stop()`/`OpenCodeServerManager.stop()` does not terminate the underlying `opencode serve` child process; it is left orphaned on the configured port after Jarvis exits. Reproduced in all 3 validation runs this session.
- `main.py` does not pass a `conversation_id` across separate WebSocket `user_message`s, so every turn starts a brand-new conversation with no chat history — the LLM's only continuity signal for "it"/"that task" across turns is the live DB context (`build_context()`), not remembered conversation. Works today because context always includes full task IDs (post Bug 2 fix), but limits the "maintain concise conversational context" M5 goal.
- `opencode_tasks.status` never transitions to `'waiting_for_user'` (only `tasks.status` does), and `get_opencode_running_tasks()`'s SQL filters to `('running','pending')` — so the OpenCode-specific "N task(s) are waiting" attention line is currently dead code in both `_fast_path` and `tools.py::_get_attention`. The `pending_questions`-based attention path is unaffected and works correctly.
- No Playwright/browser-DOM-level validation exists for M5 (see above).
- All M4 known limitations not specifically addressed above remain open (no auth/TLS, unbounded table growth, permission/question DB conflation, etc.).

### Milestone 6 — Reliability and Observability — 2026-07-09 09:53 IST

Goal: resolve the M5 trust/lifecycle gaps (real-execution uncertainty, process orphaning, dead attention code, no conversation continuity, no browser-DOM validation) with verified evidence, not new product surface area. Per explicit scope boundaries: no voice, no push notifications, no new application adapters, no general browser control, no expanded LLM autonomy.

#### Baseline

`pytest tests/ -v --tb=short` before any change: **115 collected, 115 passed, 0 failed, 0 skipped, 2 warnings** — matched the expected M5-closure baseline exactly.

#### Phase 1/3 — OpenCode execution-uncertainty investigation (root cause found)

Reproduced with a minimal, standalone probe against the real `opencode serve` v1.15.10, safe sandbox only (`tests/test_projects/safe-test`):

- `POST /session` → 200, session created.
- `POST /session/{id}/prompt_async` (the endpoint Jarvis uses) → **204 accepted**.
- **~0.2s later, the SSE stream delivered `session.error`**: `SQLiteError: NOT NULL constraint failed: session_message.seq` (Bun/SQLite stack trace, originating inside OpenCode's own message-persistence path, before any model call).
- `POST /session/{id}/message` (synchronous v1 endpoint, different session) → **HTTP 500**, same underlying failure surfaced directly.
- `POST /api/session/{id}/prompt` (v2) → 400, different request schema (`{"prompt":...}` not `{"parts":[...]}, — a script/usage detail, not the root cause).
- `GET /session/{id}/message` and `GET /api/session/{id}/message` stayed empty at every poll (t+0 through t+55s) — because the message write never succeeded, not because of a version/endpoint mismatch as originally suspected in the M5 closure notes.

**Root cause, confirmed by read-only inspection of the shared global `~/.local/share/opencode/opencode.db`** (no writes performed): `session_message.seq` is `INTEGER NOT NULL` with no default. The 59 existing rows with valid `seq` values **all belong to sessions created by OpenCode 1.14.39–1.14.44** (tens of millions of real tokens — almost certainly the Desktop app). **Every session created via the API through the installed 1.15.10 `opencode serve` binary fails this constraint, 100% of the time, across every submission path tried.** This is exactly the "Desktop/CLI shared-database version skew" scenario the task's safety boundaries named explicitly ("stop and report if it directly blocks M6") — confirmed to directly block Goal #1 (prove real execution) and Phase 2. Per that boundary, this was reported to the user rather than worked around; the user chose to proceed with the rest of M6 with this documented as a blocker, rather than have this session modify the OpenCode installation or its database (explicitly out of scope either way).

No `--data-dir`-equivalent isolation flag exists on `opencode serve` to sidestep the shared DB; `opencode db migrate` is an unrelated JSON-import tool, not a schema fixer. **Conclusion: (C) reproducible OpenCode bug/version-skew, evidenced, not fixable from Jarvis's side, not addressed per explicit instruction not to touch the OpenCode installation.**

A second, independent, Jarvis-side bug was found during this same investigation (see Phase 10 below): the SSE event schema Jarvis assumed (`{"event": "...", "data": {...}}` with named `event:` SSE fields) was never real — the actual server sends unnamed `data:`-only frames with the type nested at `data.payload.type`. This was fixed (Jarvis-side, in scope) independently of the OpenCode DB bug.

#### Phase 2 — Deterministic real-execution proof

`tests/m6_execution_probe.py` (gated, not part of `pytest tests/` — matches the existing `m5_validate2.py` convention). Uses the exact production code path (`OpenCodeSupervisor.start_session` → `OpenCodeAdapter.send_prompt`), bypassing the LLM/Supervisor layer (M5 already covers tool-routing correctness). Submits a unique `JARVIS_EXECUTION_PROBE_<id>` marker instruction asking OpenCode to write `jarvis_execution_probe_<id>.txt` with exact content, safe sandbox only. Snapshots the sandbox directory before/after to catch unexpected file changes.

**Result (2026-07-09, real run): FAIL — real execution not proven**, exactly as predicted by the Phase 1/3 finding:

| Evidence | Result |
|---|---|
| A. Jarvis task/session mapping exists | ✅ True |
| B. Instruction submission API succeeded | ✅ True |
| C. Relevant OpenCode events observed | ✅ True (`session.error` via SSE) |
| D. Expected file exists | ❌ False |
| E. Content exact match | ❌ False (no file) |
| F. No unexpected files modified | ✅ True |
| G. Terminal state via verified semantics | ✅ True — reached `failed` in ~1s via `session.error`, not a 90s timeout |

This is the desired outcome given the current environment: previously this would have left the task silently stuck "running" forever (or worse, been misreported); now it correctly, quickly, and verifiably reports `failed` with cited evidence. The test is committed and ready to serve as real proof the moment the upstream OpenCode issue is resolved, without reconstruction.

#### Phase 4 — Verified OpenCode task lifecycle

Native evidence required for each transition (implemented in `opencode_supervisor.py`, driven by the corrected SSE event catalog — see Phase 10):

| State | Verified evidence |
|---|---|
| RUNNING | Any `message.part.delta`, `session.next.*`, or `session.diff` SSE event (`_handle_activity`) — never inferred from elapsed silence. |
| WAITING_FOR_USER | A pending native `question.asked` or `permission.asked` (both funnel through the existing `questions` table; SSE now correctly triggers an immediate poll — see Phase 10). |
| COMPLETED | `session.idle` **and** no `session.error` observed since the last prompt (tracked per-task in `_error_since_prompt`, reset on every new `send_instruction`) **and** no pending question/permission for that task. Idle alone is *not* sufficient — this directly answers the "is idle equivalent to complete?" question: no. |
| FAILED | `session.error` — definitive, takes precedence over any later idle for the same turn. |
| CANCELLED | Successful `POST /session/{id}/abort` response (client-initiated, already-verified evidence; unchanged). |
| DEGRADED (new) | Non-terminal OpenCode tasks after a Jarvis restart. Sessions persist in OpenCode's own DB independent of the `opencode serve` process, so a restart does not prove failure — `mark_running_opencode_tasks_interrupted()` now sets `'degraded'`, not `'failed'` (previously a false-failure claim). `OpenCodeSupervisor.reconcile_on_startup()` attempts to upgrade degraded tasks to a verified status once the fresh server is up; currently nothing is upgradable given the Phase 1/3 blocker (message-log endpoint unusable), so tasks correctly stay `degraded` rather than being guessed at. |

Idempotency: `_handle_session_failed`/`_handle_session_idle` no-op on an already-terminal task (protects against duplicate/late SSE delivery). All transitions tested in `tests/test_opencode_lifecycle.py` (15 tests) against real-shaped SSE frames, including two-session isolation, unknown-session safety, and reconciliation.

#### Phase 5 — OpenCode server process ownership (root cause found and fixed)

**Root cause, confirmed empirically** (Windows process-tree inspection via `Get-CimInstance Win32_Process`): `opencode.exe serve`, launched via `asyncio.create_subprocess_exec`, is a Bun-wrapped launcher that spawns the real long-running server as a child and then **the launcher itself exits almost immediately**. The PID `asyncio.create_subprocess_exec` returns is that launcher — already dead by the time `terminate()`/`kill()` runs — while the real server becomes a **parentless orphan**, invisible to PID-based termination. Directly reproduced: a `Start-Process`-launched PID (6296) no longer existed seconds later; the process actually holding the port (19752) had a *different*, also-already-dead parent PID (10936).

**Fix** (`app/integrations/process_utils.py` new module + `opencode_server.py` rewrite): resolve server identity from **what's actually listening on the port** (`netstat -ano` parsing) after startup, not the launcher PID. Track `owned: bool` explicitly. Shutdown: graceful HTTP `/global/dispose` → poll port release → `taskkill /PID <resolved_pid> /T` (graceful) → poll → `taskkill /PID <resolved_pid> /T /F` (forced) → poll → log the final outcome. Never matches or kills by process name — only by the specific PID resolved from the port, so OpenCode Desktop or an unrelated CLI session sharing the machine is never touched.

**Real verification** (not just mocked unit tests): 3 consecutive real start/stop cycles run manually — each correctly resolved a distinct owned PID (different from the launcher PID every time), each correctly released the port after the graceful-kill step failed (expected — a headless server doesn't handle `WM_CLOSE`) and forced-kill succeeded. Also exercised end-to-end for real during the Phase 2 execution-probe run and the Phase 11 Playwright session. 15 deterministic mocked unit tests in `tests/test_process_ownership.py` cover: owned-stopped, external-preserved, graceful-then-forced escalation, port-release verification, 3× repeated start/stop with no leftover ownership marker, and shutdown-after-startup-failure safety.

#### Phase 6 — Stale/incompatible server diagnostics

`classify_existing_server()` probes anything already listening on the configured port before Jarvis acts: `NONE` (nothing there, spawn normally) / `HEALTHY_EXTERNAL` (responds correctly, not proven to be ours — attach, `owned=False`, never killed) / `HEALTHY_OWNED_STALE` (responds correctly **and** matches an on-disk ownership marker (`.jarvis_opencode_owner.json`, written on every successful owned start, cleared on clean stop) from a prior Jarvis run — safe to clean) / `AUTH_MISMATCH` (401 — reported with PID/process diagnostics, never attached or killed) / `INCOMPATIBLE` (wrong response shape) / `UNRESPONSIVE`/`UNKNOWN` (fails closed, reported, never killed).

**Real verification**: (1) a genuinely stale orphaned `opencode.exe` from an earlier part of this session was found with mismatched credentials before testing began — exactly the scenario this phase defends against; (2) a real external server with a deliberately different `OPENCODE_SERVER_PASSWORD` was started, and `OpenCodeServerManager.start()` correctly raised `OpenCodeServerError` reporting the PID/process without attaching or killing it — confirmed alive afterward, then manually cleaned up by this session (not by Jarvis). Unit-tested for all six classifications in `tests/test_process_ownership.py`.

#### Phase 7 — conversation_id continuity

New WS handshake: client sends `{"type":"conversation_init","conversation_id": <stored-or-null>}`; server validates format (`^conv_[0-9a-f]{12}$` — anything else, including SQL-injection-shaped strings, wrong types, or oversized values, is silently replaced with a fresh server-minted ID, never trusted) and replies `{"type":"conversation_ready","conversation_id":...,"history":[...]}` with bounded (`CONVERSATION_HISTORY_LIMIT=20`) prior **assistant** messages only — user messages are already replayed by the pre-existing global `history` event, so restoring them again here would duplicate, not fill a gap; assistant replies were never persisted anywhere else before this, so restoring them is a real fix, not a duplicate. `app.js` persists the ID to `localStorage` and attaches it to every outgoing `user_message`. `main.py`'s `user_message` handler now actually passes `conversation_id` into `Supervisor.process_message()` (previously never passed at all, despite the parameter existing since M5). Older clients that never send the handshake (e.g. `m5_validate2.py`) still work exactly as before, with a lazily-minted ID and no cross-turn continuity — no breaking change.

Real, live-browser verification via Playwright (Phase 11 Scenario A/E): fresh connection got a valid `conv_...` ID in localStorage; a follow-up turn reused it; closing and reopening the WebSocket directly (`ws.close()` from the page) preserved the exact same ID and the auto-reconnect logic correctly re-sent it. 6 additional deterministic tests in `tests/test_conversation_continuity.py` (using FastAPI's `TestClient` against the real `app.main` WS endpoint, with `opencode_supervisor.start/stop` mocked to avoid spawning a real server) cover: first-connection ID issuance, turn reuse, reconnect preservation + history restoration, second-conversation isolation, malformed-ID rejection (including an injection-shaped string), and bounded-history enforcement.

**Bug found while building this test**: importing `app.main` triggers its module-level `load_dotenv()`, which — the first time it happens in a pytest process — pulls the real `.env` (including the real `JARVIS_LLM_API_KEY`) into `os.environ` via a direct mutation `monkeypatch` cannot see or auto-revert. Undetected, this silently leaked the real key into every later test file in the same pytest run, switching `Supervisor()` from `FakeLLMProvider` to a real `LLMProvider` — caught because `test_supervisor_unknown_message` (asserting `"Fake LLM" in result["response"]`) failed on the very next full-suite run. Fixed: import `app.main` once at test-module load time and permanently strip anything `load_dotenv()` added (plain `os.environ` mutation, not `monkeypatch`, so nothing "restores" the leak later).

#### Phase 8 — OpenCode waiting/attention state (dead code fixed)

Root cause of the dead "N OpenCode task(s) waiting" line (flagged as a known limitation in the M5 closure): `_emit_question`/`_emit_permission` only ever updated `tasks.status`, never `opencode_tasks.status`, so `get_opencode_running_tasks()`'s filter (`running`/`pending`) could never match a waiting task even after also broadening it to include `waiting_for_user`. Fixed both sides to move together, plus added `_clear_waiting_state()` (called from `answer_question`/`reject_question`/`approve_permission`) to resume a task once nothing is left pending for it — previously a resolved question never returned the task to `running` on either table at all.

Redesigned the attention surface to be **one coherent, deduplicated list** instead of two overlapping sections (a "pending questions" list plus a separately-derived "waiting tasks" list that would have shown the exact same item twice once the status-propagation bug above was fixed): `_get_attention`/`_fast_path`'s attention branch now derive everything from `db.get_pending_questions()` alone, labeling each item "is waiting for your answer" or "needs permission" based on the existing `"Permission:"` text-prefix convention. Verified live via Playwright (Scenario D): the "Needs Your Attention" panel correctly renders a real pending question with working option buttons, and correctly clears (`display:none`, zero children) once answered.

Also fixed while touching this code (M4 known limitation #15): removed the dead `db.get_opencode_task_by_session("")` line in `approve_permission()`.

#### Phase 9 — Observability

Added structured `logger.info(...)` calls (task_id/session_id included) for: task/session mapping created, instruction submitted, question/permission received, answer delivered, permission resolved, terminal state observed (with the evidence type that proved it), first execution event observed, server classification result, server ownership established, shutdown initiated, owned server terminated + port released, stale/incompatible server detected. No API keys, auth headers, or full raw prompts are logged. No new monitoring framework — this is all through the existing Python `logging` module already used throughout the codebase.

#### Phase 10 — SSE reconnection and event-evidence hardening (major bug found and fixed)

**Root cause, confirmed via direct probe and the OpenAPI `/doc` schema**: the SSE event shape Jarvis's `process_sse_event()` assumed since M4 (`{"event": "question", "data": {...}}`, expecting a named top-level SSE `event:` field) was **never real**. Every real frame from `opencode serve` 1.15.10 is an unnamed `data:`-only line containing a `GlobalEvent` envelope (`{"directory":..., "payload": {"id":..., "type": "session.error", "properties": {...}}}`) — the type discriminator lives at `payload.type`, three levels deeper than assumed, and only visible in the JSON body, not the SSE frame headers. Because `sse_data.get("event", "")` was always `""`, **every single real SSE event was silently dropped by Jarvis from M4 through M5** — session completion/error/activity were never observed via SSE at all (this compounds with, but is independent of, the Phase 1/3 OpenCode DB bug: even with a working OpenCode, Jarvis could not have learned about completion this way). Confirmed this was never actually exercised: `FakeOpenCodeServer` in `test_opencode.py` has no `/global/event` SSE endpoint at all, and the existing `process_sse_event` unit tests hand-constructed events matching the invented (never-real) schema.

Question/permission detection survived this bug in practice because the poll loop (`GET /question`/`GET /permission` every 3s) is a fully independent detection path using the real, different, legacy REST shape — unaffected by SSE parsing. Fix keeps that poll loop as the source of truth for question/permission *content*, and uses the now-correctly-parsed SSE `question.asked`/`permission.asked` events only as an immediate "poll now" trigger (avoiding a second, riskier reimplementation of question/permission field parsing against a third schema shape under time pressure).

Fixed (`opencode_events.py::process_sse_event` rewritten against the verified real schema; `opencode_supervisor.py::_handle_sse_event` rewritten to route `session.error`→`_handle_session_failed`, `session.idle`→`_handle_session_idle`, `message.part.delta`/`session.next.*`/`session.diff`→`_handle_activity`, `question.asked`/`permission.asked`→immediate poll trigger). Added: a bounded (500-entry) SSE event-id dedup cache so reconnect replay cannot double-fire a terminal transition; a broad `try/except` around the whole event-handling path so one malformed/unrecognized event type can never kill the SSE loop; unknown-session events (e.g. from an OpenCode Desktop session sharing the same install) are safely ignored rather than mapped to a fabricated `"unknown"` task.

**Real end-to-end verification**: the Phase 2 execution-probe run genuinely exercised this — a real `session.error` SSE frame from the real server was correctly parsed, routed, and turned into a verified `failed` terminal state in ~1 second. Also observed live via Playwright (an `OpenCode Error` timeline bubble appeared correctly, concurrently with a pending supervisor "Thinking..." turn — see Phase 11 Scenario C). 7 new deterministic tests in `test_opencode.py` (`test_events_process_sse_*`) cover the real envelope shape, activity-event prefixes, unrecognized-type silence, and malformed-frame safety; 15 tests in `test_opencode_lifecycle.py` cover dedup, unknown-session safety, and two-session routing isolation end-to-end through `_handle_sse_event`.

Reconnect mechanics themselves (bounded exponential backoff on connection loss) were already implemented correctly in `opencode_adapter.py::consume_events()` from M4 and were not changed — only the event *parsing/routing* on top of that stream was broken. Not given a dedicated reconnect-mechanics test (would require mocking `httpx`'s streaming client); the idempotent, evidence-based handlers built in Phase 4 structurally guarantee that a reconnect replaying or missing events cannot falsely complete/fail a task, which is the property that mattered most here.

#### Phase 11 — Real Playwright DOM validation

Ran against a real Jarvis server (real OpenRouter free-tier LLM, real `opencode serve`) using `playwright-cli`, inspecting actual DOM state (not just the WebSocket protocol, which M5 already validated).

- **Scenario A (conversation)**: user bubble appeared, response rendered as clean readable text (not double-encoded JSON), no duplicates, `conversation_id` handshake completed and persisted to `localStorage`. **PASS.**
- **Scenario B (task start)**: user message, "Thinking..." indicator, final supervisor response all rendered correctly. Given the Phase 1/3 blocker, the *real* outcome was a task failure, not completion — and that rendered correctly and honestly (see Scenario C/F below) rather than as a false success. **PASS** (adjusted for the documented environment constraint).
- **Scenario C (concurrent events)**: naturally demonstrated by the environment — an `OpenCode Error` bubble (from the real `session.error` SSE event, Phase 10) rendered correctly *while* the LLM turn was still showing "Thinking...", with no timeline corruption and the final supervisor response still rendering correctly afterward. **PASS.**
- **Scenario D (attention)**: used the existing safe `/mock-agent` worker (same "Needs Your Attention" UI component OpenCode questions/permissions use) to trigger a real pending question. Panel rendered with working option buttons; clicking one delivered the answer and the panel correctly emptied (`display:none`, 0 children); the timeline preserved both the question and answer events. **PASS.**
- **Scenario E (reconnect)**: established a conversation, closed the WebSocket directly from the page, waited >5s for auto-reconnect. Same `conversation_id` preserved in `localStorage`; connection status returned to "Connected". **Found and fixed a real duplicate-rendering bug** (see below) before this scenario could pass cleanly. **PASS** (after fix).
- **Scenario F (failure)**: effectively covered by the real OpenCode failures observed in B/C — "Thinking..." cleared correctly, an honest failure indication rendered, the UI remained fully usable immediately afterward (verified by sending one more message and getting a correct fast-path reply), and the LLM's final text did not claim success. **PASS.**

**Two genuine bugs found and fixed via live DOM inspection** (neither would have been caught by protocol-level testing alone):

1. **`opencode_task_completed`/`opencode_task_cancelled` events never reached the timeline renderer.** `handleEvent()` early-returned after `removeActiveTask(...)` without ever calling `addToTimeline(...)`, so a task's terminal outcome was never shown to the user at all beyond it silently disappearing from the Active Tasks panel — and the earlier M5-closure fix that made this text status-aware ("OpenCode task failed/completed/cancelled") was consequently dead code from the moment it was written. Fixed: also call `addToTimeline(data)` in that branch. Verified live: "OpenCode task failed: oc_..." now renders correctly.
2. **Reconnect duplicated any event that was both live-broadcast and later replayed by the `history` event** — a pre-existing gap in the original M1–M3 reconnect design (not M6-introduced), first observed as a real duplicate "Question" bubble after a live WebSocket reconnect (question count 1→2). Root cause: `question_asked` (and any other persisted event type) renders once live via broadcast, then again from the `history` replay on every reconnect, with no de-duplication. Fixed with a client-side dedup guard in `addToTimeline()` keyed on `type|timestamp|content` (timestamps carry microsecond precision from `database.py::utcnow()`, sufficient for practical uniqueness here). Verified live: duplicate count returned to 1 after the fix.

Console/network: zero browser console errors or warnings throughout (one harmless pre-existing `favicon.ico` 404, unrelated); no failed non-static network requests.

#### Bugs found and fixed this milestone (summary)

1. SSE event schema was never real (M4-era, silently dropped 100% of real SSE events) — **fixed**, Phase 10.
2. `opencode serve` process ownership/shutdown (orphaned child survives Jarvis exit) — **fixed**, Phase 5.
3. `opencode_tasks.status` never reached `waiting_for_user`, and resolving a question/permission never resumed the task — **fixed**, Phase 8.
4. `mark_running_opencode_tasks_interrupted()` falsely reported `failed` on restart instead of `degraded`/unknown — **fixed**, Phase 4.
5. `approve_permission()` dead code (`db.get_opencode_task_by_session("")`) — **fixed**, Phase 8.
6. `opencode_task_completed`/`opencode_task_cancelled` never rendered in the timeline (dead status-aware text from the M5 closure) — **fixed**, Phase 11.
7. Reconnect duplicated any event rendered both live and via `history` replay — **fixed**, Phase 11.
8. `test_conversation_continuity.py`'s `app.main` import leaked the real `JARVIS_LLM_API_KEY` into later tests in the same pytest session — **fixed**, Phase 7 (test-isolation bug introduced and fixed within this same milestone).
9. FastAPI `@app.on_event` deprecation surfaced by newly importing `app.main` in tests — **fixed** (migrated to `lifespan` context manager), incidental to Phase 7.

#### Final automated test result

`pytest tests/ -v --tb=short`: **159 collected, 159 passed, 0 failed, 0 skipped, 2 warnings (same 2 pre-existing/known warnings as every prior milestone — Windows asyncio teardown, `websockets.legacy` deprecation).** Run twice after all Milestone 6 changes (including the Phase 11 app.js fixes, which pytest doesn't directly exercise) to confirm stability: 395.91s, then 250.65s (final, definitive run). +44 tests vs. the M5-closure baseline of 115: `test_process_ownership.py` (15, new), `test_opencode_lifecycle.py` (15, new), `test_conversation_continuity.py` (6, new), `test_opencode.py` (+8, real SSE schema), `test_supervisor.py` (+2 net, attention redesign replaced 2 obsolete regression tests with corrected ones).

#### Server lifecycle repetition (Phase 13-C)

Repeated real start/stop cycles (beyond the required 3): each correctly resolved a distinct real owned PID (never the launcher PID), each correctly released the port, zero orphans left behind across the whole session except where a Jarvis process was deliberately force-killed from outside (`taskkill /F`, which bypasses any application's shutdown hook on Windows — not a Jarvis defect) — those orphans were manually identified and cleaned up as part of this session's own test hygiene, not silently left behind.

#### External server preservation (Phase 13-D) and stale/auth-mismatch handling (Phase 13-E)

Both verified for real (not just mocked), see Phase 5/6 above: an externally-started compatible server survived a full Jarvis start+stop cycle untouched; an externally-started server with mismatched credentials caused `OpenCodeServerManager.start()` to fail closed with a diagnostic error, never attaching or killing it.

#### Remaining limitations after Milestone 6

- ~~OpenCode execution is still unproven and currently unprovable~~ — **RESOLVED 2026-07-09, see Milestone 6.1 below.** The Phase 1/3 root cause (shared-storage version skew) was real, but proved fixable from Jarvis's side via storage isolation rather than requiring an upstream fix. `tests/m6_execution_probe.py` now passes for real. This bullet is left struck through rather than deleted — the M6 investigation and its (at-the-time) correct FAIL result remain accurate historical evidence of the state before the fix.
- `OpenCodeSupervisor.reconcile_on_startup()` currently cannot upgrade any `degraded` task to a verified status when reconciling against **pre-isolation, shared-storage** tasks (the ones that predate Milestone 6.1). Newly-created isolated-runtime tasks are unaffected by the original constraint, but reconciliation logic itself was not revisited in 6.1 — see Milestone 6.1 remaining risks.
- Question/permission SSE events (`question.asked`/`permission.asked`) are used only as a "poll now" trigger, not fully parsed to their new nested schema (`questions: [...]` array, different field names than the legacy poll shape) — the poll loop remains the actual source of truth for question/permission content. A future milestone could unify these onto one schema.
- `message.part.updated`/`message.updated` (OpenCode chat content) are not wired into the SSE routing — `normalize_message()`/`_emit_message()` remain defined but unused, since their exact real-server schema was not verified in the time available. OpenCode message content is currently not streamed live to the Jarvis timeline.
- SSE reconnect-mechanics themselves (exponential backoff in `consume_events()`) were not given a dedicated new test; only the event-parsing layer on top was verified as fixed.
- `session.diff` (file-change evidence) is parsed and logged but not otherwise surfaced to the user or used as execution proof beyond the Phase 2 filesystem check.
- The `.jarvis_opencode_owner.json` ownership marker is a single global file (not scoped per-port); running two Jarvis instances against two different ports on the same machine would need this generalized.
- All M4/M5 known limitations not specifically addressed above remain open (no auth/TLS, unbounded table growth, permission/question DB conflation in the `questions` table, etc.).

### Milestone 6.1 — OpenCode Storage Compatibility Recovery — 2026-07-09 13:00 IST

Focused task between Milestones 6 and 7: investigate and, only if safely verifiable, resolve the OpenCode storage-compatibility blocker that left Milestone 6 Goal #1 (proving real OpenCode execution) unmet. **Resolved.** Real, file-verified OpenCode execution now works, with zero mutation of OpenCode Desktop's shared storage, confirmed by byte-identical hashes before and after.

#### Original blocker, restated

`session_message.seq NOT NULL` constraint violation on every API-submitted prompt through the shared `~/.local/share/opencode/opencode.db`, before any model call — see Milestone 6 Phase 1/3 for the original investigation and evidence.

#### Phase 1 — state preservation

Recorded before any experiment: Jarvis-managed CLI `C:\Users\Admin\.bun\bin\opencode.exe` = **1.15.10**. Desktop-bundled CLI `C:\Users\Admin\AppData\Local\OpenCode\opencode-cli.exe` = **1.2.24** (not 1.14.x as the Milestone 6 investigation inferred from the DB's `session.version` field — that field does not reliably identify the writing binary's package version; see below). Desktop's own internal Rust/Tauri log (`%LOCALAPPDATA%\ai.opencode.desktop\logs\...log`) shows it spawns an internal "sidecar" server process on its own dynamic port (observed: 8540) rather than shelling out to the standalone CLI at all ("No CLI installation found, skipping sync"). No OpenCode-related process was listening on any port at the start of this task. Environment variable names recorded (no values): none of `OPENCODE_*`/`XDG_*` were set in the working shell; standard `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA` were present as usual. Baseline hashes/sizes/mtimes of `opencode.db`, `opencode.db-wal`, `opencode.db-shm`, `auth.json`, and `opencode.jsonc` were recorded (see Phase 1 incident below for why this baseline mattered).

#### Phase 1 incident — an unexpected Desktop DB mutation occurred, investigated, and the user was consulted before continuing

While experimentally determining which environment variables OpenCode respects (Phase 2), several `opencode debug paths` invocations tested XDG variables **individually** (e.g. `XDG_CONFIG_HOME` alone) to isolate each one's effect. Each such invocation left `XDG_DATA_HOME` unset, so — as each command's own output confirmed — the resolved `data` path was the real shared `~/.local/share/opencode`, meaning the real `opencode.exe` binary ran against the shared database for a `debug paths` call that was assumed to be pure, side-effect-free introspection. It was not: the shared database's `session_message` table row count dropped from **59** (recorded during the Milestone 6 investigation) to **0**, with the main `.db` file's hash changing while its size stayed byte-identical (consistent with an in-place DELETE, not just a WAL checkpoint). `PRAGMA integrity_check` returned `ok`; `auth.json` and `opencode.jsonc` were confirmed byte-identical (untouched); the `message` table (9,062 rows) was unaffected. The most likely explanation: those 59 rows were exactly the legacy/schema-incompatible rows causing the constraint violations, and some idempotent startup maintenance path in 1.15.10 silently cleaned them up when it opened the shared database — plausible, but not proven, and not investigated further per the explicit instruction not to repair or migrate the Desktop database.

Per the task's own stop condition ("if any Desktop storage file changes unexpectedly: stop, report, do not continue"), work was halted immediately, all further live `opencode.exe` invocations were confined to fully-isolated environments only (all four isolation variables set together, never individually against the default/shared path again), and the user was asked how to proceed. **The user chose to continue**, accepting the analysis that the isolated Phase 4 probe itself (all four variables set together) had already been confirmed — by direct before/after hash comparison around that specific run — not to have touched the shared database; the damage came specifically from the individual-variable diagnostic commands, which were discontinued. This incident is preserved here as historical evidence, not deleted, per the project's documentation rules. **No repair or further investigation of the deleted rows was attempted.**

#### Phase 2 — official storage path-resolution map (Windows)

Investigated via the installed CLI's own `opencode debug paths` introspection command (official, first-party, shipped with the binary) and the public docs at opencode.ai:

| Path | Default (Windows) | Override mechanism | Evidence tier |
|---|---|---|---|
| data (incl. `opencode.db`, `auth.json`) | `%USERPROFILE%\.local\share\opencode` | `XDG_DATA_HOME` → `<value>\opencode` | Experimentally verified (repeatable, deterministic via `debug paths`); **not** in official docs |
| config (`opencode.jsonc`) | `%USERPROFILE%\.config\opencode` | `XDG_CONFIG_HOME` → `<value>\opencode` | Experimentally verified; **not** in official docs (see note below) |
| cache (+ `bin`) | `%USERPROFILE%\.cache\opencode` | `XDG_CACHE_HOME` → `<value>\opencode` | Experimentally verified; not in official docs |
| state | `%USERPROFILE%\.local\state\opencode` | `XDG_STATE_HOME` → `<value>\opencode` | Experimentally verified; not in official docs |
| log, repos | `<data>\log`, `<data>\repos` | inherit from `XDG_DATA_HOME` | Derived from the above |
| tmp | `%LOCALAPPDATA%\Temp\opencode` | none found | N/A |

**Officially documented** (opencode.ai/docs/config/), but a *different, additive* mechanism — not the base directory `debug paths` reports: `OPENCODE_CONFIG` (custom config file path), `OPENCODE_CONFIG_DIR` (an *extra* search path for agents/commands/modes/plugins, layered on top of — not replacing — the base config directory), `OPENCODE_CONFIG_CONTENT`, `OPENCODE_TUI_CONFIG`. Confirmed experimentally: setting `OPENCODE_CONFIG_DIR` alone did not change `debug paths`'s reported `config` row at all. The official docs explicitly state no dedicated env vars exist for data/cache/state directories — "references to directories use standard paths." All four `XDG_*` variables were confirmed to work **independently and together** (all four set simultaneously correctly redirected all four corresponding rows, with `config`/`cache`/`state` untouched when only `XDG_DATA_HOME` was set, and vice versa) — no inferred-only mechanism was used for the final fix.

#### Phase 3 — isolation independence and provider-config strategy

Confirmed: data, config, cache, and state can be isolated **independently or together** (option D, "all XDG directories together," chosen — see Phase 7). `auth.json` (provider credentials) lives under **data**, not config; `opencode.jsonc` (174 bytes, inspected directly) contains no secrets, only an MCP server declaration (`blender-mcp` — left completely untouched, per the explicit instruction not to touch that issue). `auth.json`'s structure was inspected with all secret values redacted (`{"openrouter": {"type": "api", "key": "<redacted>"}}` — only the non-secret `"type": "api"` value and key length/format were read). OpenRouter has no officially-documented direct environment-variable credential path, but OpenCode's documented custom-provider config schema supports `"apiKey": "{env:VARIABLE_NAME}"` — confirmed via opencode.ai/docs/providers/. **Chosen safe strategy**: fully isolated data/config/cache/state (no read access to Desktop's config or credentials at all), with a Jarvis-generated, minimal isolated `auth.json` written at runtime-provisioning time, sourced from `JARVIS_OPENCODE_OPENROUTER_KEY` (new, optional, dedicated) or `JARVIS_LLM_API_KEY` (existing, already-managed) — Jarvis's own credential, never Desktop's, never hardcoded, never logged, never committed.

#### Phase 4 — controlled isolation probe result

A fully-isolated `opencode serve` (all four `XDG_*` variables pointed at a scratch temp directory, dedicated port 4199, safe sandbox project) was started for real. Verified: server started and became healthy; version 1.15.10 confirmed; a **new** `opencode.db` was created at the expected isolated path (triggering a one-time fresh-schema migration, visibly separate from the shared DB); session creation succeeded (200); instruction submission succeeded (204); **54 real SSE events observed** (`session.next.agent.switched`, `session.next.model.switched`, `message.updated` ×3, `message.part.updated`, `session.updated` ×2, `session.status` ×2, `session.diff`, plus `sync` framing events) — dramatically more activity than any shared-DB run ever produced; **no `session_message.seq` error occurred**; the message-history endpoint (`GET /api/session/{id}/message`) **returned real, non-empty data for the first time in the entire M5→M6 investigation**. Desktop files confirmed byte-identical before and after this specific fully-isolated run (the run itself was clean — see the Phase 1 incident above for where the actual mutation came from).

#### Phase 5 — deterministic execution proof (first success)

Ran the real file-creation probe (unique `JARVIS_EXECUTION_PROBE_<id>` marker, safe sandbox only) against the isolated environment, through the actual production `OpenCodeSupervisor`/`OpenCodeServerManager` code path (not a one-off script). **Result: PASS — real execution proven**, for the first time across the entire M5/M6/6.1 investigation:

| Evidence | Result |
|---|---|
| Session created | ✅ |
| Instruction accepted | ✅ |
| Meaningful execution events observed | ✅ (`session.diff` — a real file-change event) |
| Expected file exists | ✅ |
| Exact content match | ✅ |
| Verified terminal state | ✅ `completed` (not `failed`) |
| `session_message.seq` failure | ✅ none |
| Desktop DB/config unchanged | ✅ byte-identical hash/mtime before and after |

Repeated a second time (Phase 11 restart/reuse check, new marker, fresh process) with an identical PASS result, confirming the isolated storage is durable and reusable across restarts, not a one-shot fluke.

#### Phase 6 — session/message storage investigation

With isolation working, the message-history endpoint now genuinely returns data (see Phase 4), resolving the Milestone 6 Phase 3 "empty message-history" finding as a **downstream symptom of the same root cause**, not a separate bug: messages were never persisted because the constraint violation aborted the write before persistence, not because the endpoint or its parsing was broken. Read-only inspection was not extended into a full schema diff between the shared and isolated databases (out of scope once the practical fix was verified) — this is recorded as a remaining limitation below, not a completed investigation.

#### Root cause, with confidence level

**High confidence, though not 100% proven**: the shared `opencode.db` contained rows (`session_message.seq` values) written under different, older assumptions than the installed 1.15.10 binary expects, and this cross-version incompatibility manifested as a hard constraint violation on every new write. Isolating storage per-runtime avoids the problem entirely by giving 1.15.10 a database it created itself, with no legacy rows. The **exact** mechanism (which prior version/writer produced the incompatible rows, and why) remains unconfirmed — the direct evidence trail (those 59 `session_message` rows) was lost in the Phase 1 incident before deeper forensic comparison could be done, and per the task's explicit instruction, no attempt was made to repair, migrate, or forensically reconstruct the Desktop database to find out. This is a genuine evidentiary gap, disclosed rather than papered over.

#### Architecture decision matrix

| Option | Safety | Data-loss risk | Session preservation | Reliability | Maintainability | Upgrade independence | Process ownership | Recovery simplicity | Compat. w/ current arch. |
|---|---|---|---|---|---|---|---|---|---|
| A. Jarvis isolated storage | High | None (Desktop untouched) | Both preserved separately | High (verified) | Medium (one more directory concept) | High (Jarvis version-independent of Desktop) | Clean (M6 ownership model applies unchanged) | High | High |
| B. Upgrade Desktop to match | Low (outside Jarvis's control/scope) | Unknown | Risk to Desktop history | Unknown | Low | None | N/A | Low | Low |
| C. Pin Jarvis to Desktop's version | Medium | None | N/A | Low (older Jarvis-side code, unverified) | Low | None (couples Jarvis to Desktop's version forever) | Unchanged | Medium | Medium |
| D. Supported migration of shared storage | Low (no supported migration path found) | High (any bug destroys shared history) | At risk | Unknown | Low | None | Unchanged | Low | Medium |
| E. Continue shared storage | None (already proven broken) | N/A (already broken) | N/A | None | N/A | N/A | Unchanged | N/A | N/A |

**Chosen: A — Jarvis isolated OpenCode storage.** Verified, safe, maintainable, and does not require touching, understanding, or trusting anything about the Desktop app's internals or upgrade cadence.

#### Files created

- (none — all changes were to existing Milestone 6 files)

#### Files modified

- `app/integrations/opencode_server.py`: added `default_runtime_dir()`, `resolve_runtime_dir()`, `isolated_env_overrides()`, `ensure_isolated_runtime_provisioned()`; wired isolation into `OpenCodeServerManager.start()` for the owned-spawn path only (external-attach path unchanged, never isolated); added `self.runtime_dir` attribute; added a non-secret-leaking startup log line reporting isolation.

#### Jarvis runtime-directory design

- `JARVIS_OPENCODE_RUNTIME_DIR` (optional): explicit override. If unset, defaults to `%LOCALAPPDATA%\JarvisOpenCodeRuntime` — outside the repository, not committed to Git (nothing here is added to `.gitignore` since it's outside the repo tree entirely; if an operator points the override *inside* the repo, no additional guard was added — a documented risk, not a blocker).
- Under the runtime dir: `data\`, `config\`, `cache\`, `state\` subdirectories, mapped 1:1 to `XDG_DATA_HOME`/`XDG_CONFIG_HOME`/`XDG_CACHE_HOME`/`XDG_STATE_HOME` respectively — matching OpenCode's own verified layout exactly (Phase 2).
- Provisioned lazily on first owned-server start (`ensure_isolated_runtime_provisioned`), idempotently: a pre-existing isolated `auth.json` or `opencode.jsonc` is never overwritten, so an operator can hand-customize the isolated runtime (e.g. add MCP servers Jarvis-side) without Jarvis clobbering it on every restart.
- Same directory is reused on every restart (deterministic resolution — env var or fixed default), giving Jarvis-created OpenCode sessions their own persistent history across Jarvis restarts, isolated from and never migrated to/from Desktop's.
- Never deleted on normal shutdown (`OpenCodeServerManager.stop()` only ever terminates the process, never touches storage).

#### Provider/config handling

Isolated `opencode.jsonc` is minimal (`{"$schema": "https://opencode.ai/config.json"}` only) — deliberately excludes Desktop's `blender-mcp` MCP entry (per the explicit instruction not to touch that issue at all; Jarvis's isolated runtime simply never sees it). Isolated `auth.json` is generated only if `JARVIS_OPENCODE_OPENROUTER_KEY` or `JARVIS_LLM_API_KEY` is present in the environment; if neither is set, no `auth.json` is written and a warning is logged (no secrets in the warning). No provider credential is ever read from, or written to, Desktop's `auth.json`.

#### Desktop storage preservation evidence

- Phase 1 baseline hash/size/mtime recorded before any experiment.
- Phase 1 incident: an unexpected mutation **did** occur (documented above, in full, not hidden) — traced to individual-variable diagnostic commands, not the isolation mechanism itself.
- Every fully-isolated run after that point (Phase 4 probe, first Phase 5 execution proof, second Phase 5/11 restart-reuse execution proof, the full pytest regression suite) was bracketed with before/after hash/mtime checks — **all byte-identical, zero further mutation**, across two full real executions plus a 179-test automated suite run.

#### Tests added

`tests/test_opencode_isolation.py` — 20 deterministic tests, no real `opencode.exe` spawned, nothing touches real Desktop paths: runtime-dir resolution (explicit env, default-outside-repo, paths-with-spaces), `isolated_env_overrides` correctness and Desktop-path exclusion, provisioning (config without MCP, auth from env, dedicated-key precedence, no-key-no-write, idempotent non-overwrite of both config and auth, a direct check that provisioning logic never touches real Desktop paths even when pointed at a real key), owned-vs-external isolation behavior (isolation env only applied to owned spawns, never external attachment, never provisioned for external), runtime directory creation on start, repeated-restart directory reuse, shutdown-does-not-delete-storage, auth-mismatch handling unaffected by isolation, and a secret-never-logged check using `caplog`.

The real deterministic execution probe (`tests/m6_execution_probe.py`, unchanged — it now passes automatically because the production code path it exercises is isolated by default) remains explicitly gated, not part of `pytest tests/`.

#### Final automated test result

`pytest tests/ -v --tb=short`: **179 collected, 179 passed, 0 failed, 0 skipped, 2 warnings (same 2 pre-existing/known warnings), 270.41s.** +20 vs. the Milestone 6 baseline of 159 (`test_opencode_isolation.py`, new).

#### Real acceptance result (Phase 11)

1. Desktop DB/config hashes/mtimes recorded before.
2. Jarvis's `OpenCodeSupervisor`/`OpenCodeServerManager` started for real (via `tests/m6_execution_probe.py`, exercising the actual production code).
3. Confirmed isolated storage in use (`JarvisOpenCodeRuntime\data\opencode\opencode.db` created, populated).
4. Execution probe run: **PASS** (file created, exact content match, `completed` terminal state via `session.diff` evidence).
5. Message-history endpoint returned real data (first time ever observed working).
6. Server stopped cleanly.
7. No orphaned OpenCode process (port verified released); isolated storage confirmed still present on disk; Desktop DB/config confirmed byte-identical to before.
8. Jarvis "restarted" (probe run again, fresh process).
9. Isolated storage confirmed reused (same directory, pre-existing `auth.json`/`opencode.db` reused rather than recreated) and healthy.
10. Second execution probe, new marker: **PASS**, identical evidence profile to the first run. Desktop DB confirmed unchanged after this run too.

No unexpected Desktop storage change occurred during any step of this acceptance pass (the one unexpected change, during Phase 2 diagnostics, is documented separately above and was resolved before acceptance testing began).

#### Remaining risks

- **The exact mechanism of the Phase 1 incident (why an individual-XDG-var `debug paths` call deleted 59 `session_message` rows from the shared DB) is not fully understood** — plausible-but-unproven hypothesis only (see "Root cause, with confidence level" above). Running the real `opencode.exe` binary against the shared path, for any reason, is now known to carry a mutation risk and should be avoided in future work on this project unless deliberately intended.
- `reconcile_on_startup()` (Milestone 6) was not revisited — any `degraded` tasks left over from **pre-6.1** shared-storage usage remain permanently unreconcilable (the shared DB they'd reconcile against is now confirmed to have lost the relevant historical rows). This does not affect new, isolated-runtime tasks going forward.
- No schema-level diff between the shared and isolated databases was performed (Phase 6 was cut short once the practical fix was confirmed working) — the root cause is inferred from behavior, not from a line-by-line schema/trigger comparison.
- `JARVIS_OPENCODE_RUNTIME_DIR` pointing inside the repository is not guarded against (would risk accidentally committing runtime state) — no operator has done this, but there's no code-level protection.
- The isolated runtime's own OpenCode instance will independently accumulate its own database over time — Milestone 6's "unbounded table growth" limitation now applies to this second database too.
- Provider model selection inside the isolated OpenCode instance is OpenCode's own default logic (observed choosing `gpt-5.3-chat-latest`/`openai` in one run) — Jarvis does not currently pin or constrain which model the isolated OpenCode agent uses, only which *credentials* it has access to (OpenRouter). This is a different concern from the `JARVIS_LLM_FREE_ONLY` guard, which governs only Jarvis's own supervisor LLM, not OpenCode's internal model choice.

### Milestone 7 — Voice and Proactive Contact — 2026-07-09 15:10 IST

Voice input, spoken responses, a persisted notification model, a deterministic AttentionPolicy, Web Push infrastructure, PWA support, and deep linking — a voice/proactive-contact layer over the existing trusted supervisor architecture, not a general voice assistant. Voice enters the exact same WebSocket `user_message` pipeline as typed text; no new task logic, no new tool boundaries, no arbitrary shell execution, no new application adapters, no paid model fallback. **Milestone 8 has not begun.**

#### Baseline

`pytest tests/ -v --tb=short` before any change: **179 collected, 179 passed, 0 failed, 0 skipped**, 2–4 warnings (the known flaky Windows asyncio teardown warning count fluctuates run to run — 2 fixed websockets-legacy warnings plus 0–2 occasional `PytestUnraisableExceptionWarning`s depending on GC timing; never a new failure). Matched expectation; proceeded.

#### Phase 1 — capability reconnaissance

No dedicated capture-and-report probe page was built; capability facts below come from (a) reading the relevant Web platform specs' well-established behavior, (b) directly exercising each API against the real running server during Phase 17 browser validation, and (c) `cryptography`/`py_vapid` library behavior confirmed by running `scripts/generate_vapid_keys.py` and round-tripping the result through `pywebpush`. Marked **(real)** where confirmed via an actual browser session in this environment, **(known)** where taken from established platform behavior not independently re-derived here.

| Capability | localhost/127.0.0.1 HTTP | LAN IP HTTP | HTTPS (self-signed, accepted) | Notes |
|---|---|---|---|---|
| A. SpeechRecognition | Available (real, but non-functional without mic/network in headless — **real**) | Not a secure context → unavailable (known) | Available (known) | Chromium exposes both `SpeechRecognition` and `webkitSpeechRecognition`; discovered during Phase 17 that headless Chromium defines these natively even without a working mic, which affected how the mock harness had to be built (see Phase 17 below) |
| B. MediaRecorder | Available | Unavailable off secure context (known) | Available (known) | Not used in Milestone 7 — voice input uses SpeechRecognition directly, not raw audio capture (principle 2: no raw audio persistence) |
| C. speechSynthesis | Available, but **`window.speechSynthesis` is a non-configurable getter-backed property** — cannot be reassigned with a plain `=`, only `Object.defineProperty` (**real**, discovered debugging Phase 17) | Unaffected by secure-context (speechSynthesis does not require one) | Available | Real headless Chromium has no installed TTS voices, so it accepts `.speak()` calls without audible output or reliable `onend` timing — irrelevant to Jarvis, which only needs the API surface, not audio hardware |
| D. Web Push | N/A (requires HTTPS) | Unavailable (known) | Available (known) | Requires a service worker, which requires a secure context |
| E. Service workers | Available (localhost is a spec-cased secure context) | Unavailable (known) | Available (known) | `navigator.serviceWorker.register("/sw.js")` succeeded for real against `http://127.0.0.1:8000` during Phase 17 (**real**) — confirms the spec's localhost exception |
| F. PWA installability | Manifest served, but full installability (Chrome's "Add to Home Screen" heuristics) was not independently verified on a real device in this environment | Requires HTTPS in practice on most mobile browsers | Available (known) | Manifest + service worker + icons are all present; the "does Chrome actually offer to install it" check requires the real-phone step in Phase 18 |
| G. Background notification (SW alive after tab close) | N/A without push | N/A | Requires push, which requires HTTPS | Not independently verified — see Phase 18 limitations |
| H. Secure-context requirement | Special-cased: localhost counts as secure even over plain HTTP (**known**, confirmed indirectly by E above working) | Does not count as secure (known) | N/A — is secure by definition | This is why a LAN-IP phone connection over plain HTTP cannot get mic/SW/push at all |
| I. LAN HTTP limitations | — | Mic/SW/push all unavailable; text chat still fully functional (WebSocket has no secure-context requirement) | — | Confirmed by design: `startVoiceInput()`'s `voiceSupported()` check and the disabled-mic-button fallback (Phase 4) degrade gracefully rather than breaking the chat interface |
| J. HTTPS requirement for mic/SW/push | Required off localhost | Required | Satisfied | Addressed via `scripts/generate_dev_cert.py` (self-signed) or mkcert (preferred, no browser warning) — see Phase 3 |
| K. Android Chrome behavior | Not independently tested on a real Android device in this session | — | — | See Phase 18 — real-phone acceptance was not performed in this session; see Final Report limitation |
| L. iOS Safari limitations | Not independently tested; known platform constraints (SpeechRecognition support historically limited/absent on iOS Safari, Web Push required iOS 16.4+ and an installed-to-homescreen PWA) are **assumed from general platform knowledge, not re-verified here** | — | — | Explicitly flagged as unverified — see Final Report |

No native application was built or considered necessary — nothing in this reconnaissance demonstrated a browser/PWA limitation that would justify one (the explicit stop condition "native Android becomes necessary solely because of an unverified browser assumption" did not trigger, since no such assumption was made the basis for a scope expansion).

#### Phase 2 — voice architecture decision

**Voice input — chosen: A, browser-native SpeechRecognition/Web Speech API.** Zero cost, zero new backend dependency, works on Android Chrome, requires only a secure context (already being solved for push/SW anyway), acceptable latency (on-device or Google's own recognition service depending on browser, not a Jarvis-operated pipeline), the simplest implementation complexity by a wide margin. Explicit capability detection (`voiceSupported()`) with a typed-input fallback that is always present regardless of voice support (Phase 4 requirement 9). Local/offline speech-to-text (option C) was considered and rejected for Milestone 7: it would require inspecting available hardware, selecting/bundling a model, and building a new transcription service — disproportionate to a milestone explicitly scoped as "voice and proactive-contact layer," not a transcription-infrastructure milestone. No paid transcription API (option D) was used or needed.

**Text-to-speech — chosen: A, browser `speechSynthesis`.** Zero cost, zero new dependency, acceptable for concise supervisor replies and notification summaries (the only things Jarvis speaks — see Phase 5). No paid TTS was used or considered necessary.

#### Phase 3 — secure phone access strategy

**Chosen: A, LAN HTTPS with a locally trusted (self-signed or mkcert) certificate.** `scripts/generate_dev_cert.py` generates a self-signed cert covering `localhost`, `127.0.0.1`, and the machine's detected LAN IP (auto-detected via a UDP-socket trick, confirmed working: detected `192.168.1.27` on this machine); mkcert is documented as the preferred zero-warning alternative. No public deployment was made or is recommended; Jarvis remains LAN-only, matching the existing (pre-M7) trust model. OpenCode is not additionally exposed (it was never directly reachable from the browser — Jarvis's REST/WS layer is the only client-facing surface, unchanged). Jarvis's SQLite files and OpenRouter/OpenCode credentials are not exposed by any new endpoint added in this milestone (see Phase 15). An optional shared-secret gate (`JARVIS_API_TOKEN`, checked by `_require_api_token` in `app/main.py`) was added for the push-subscription endpoints specifically, off by default (consistent with the existing no-auth-by-default LAN prototype posture, Known Limitation #1) but ready if external access is ever introduced.

#### Phase 4 — voice input UI

Mic button (`#mic-btn`) added to `app/static/index.html`, next to the text input. States implemented in `app/static/app.js`: `idle` / `listening` / `processing` / `error`, each with a distinct CSS class and, where relevant, a status line (`#voice-status`, `aria-live="polite"`). Cancel-while-listening (clicking the mic again) aborts the recognition session and submits nothing. Exactly one concurrent `SpeechRecognition` instance is enforced (`recognition.abort()` before creating a new one). A `voiceSubmitInFlight` guard and cancellation flag prevent duplicate/late submission. Transcript enters `sendUserMessage()` — the exact same function the Send button uses — so voice and typed messages are indistinguishable to the supervisor (principle 1/6). No separate voice conversation store; no raw audio ever leaves the browser, only the recognized text (principle 2). Unsupported browsers get a disabled mic button with an explanatory `title`; microphone permission denial surfaces a clear inline message and never disables the text input.

#### Phase 5 — spoken responses (TTS)

Implemented in `app/static/app.js`. Speech is off by default, toggled via `#speech-toggle-btn`, preference persisted to `localStorage` (`jarvis_speech_enabled`). Exactly two call sites speak, both already concise and both already server-side-filtered before they reach the browser: the direct `supervisor_message` reply, and a live `notification` event's title+body (question/permission/failure/completion — see Phase 6/7). Raw task stdout and routine SSE progress events are never spoken because nothing else calls `speak()` — this is a structural guarantee, not a per-event filter. A bounded (3-deep) queue prevents overlapping utterances; `#speech-stop-btn` cancels immediately; starting the microphone (`startVoiceInput()`) always calls `stopSpeaking()` first. Reconnect/replay does not re-trigger speech: the reconnect-delivered `pending_notifications` batch renders notifications but deliberately never calls `speak()` — only the live `notification` event does.

#### Phase 6 — notification model

New tables in `app/database.py` (`init_db()`): `notifications` (notification_id, conversation_id, task_id, source_type, source_id, notification_type, title, body, priority, dedup_key, status, created_at, delivered_at, read_at — `dedup_key` UNIQUE), `push_subscriptions` (endpoint UNIQUE, p256dh, auth, conversation_id), `settings` (key/value, currently used only for `notify_on_completion`). `notification_type` values: `QUESTION_REQUIRED`, `PERMISSION_REQUIRED`, `TASK_FAILED`, `TASK_COMPLETED`, `SUPERVISOR_ALERT` (defined but not yet produced by any code path — reserved for a future explicit-escalation use). Priority: `LOW`/`NORMAL`/`HIGH`. `create_notification()` is an idempotent upsert-by-`dedup_key` (`INSERT OR IGNORE` + re-select), returning a `created` flag — this single DB-layer guarantee is what makes Phase 14 deduplication possible regardless of how many times a producer is (re-)entered.

#### Phase 7 — AttentionPolicy

`app/attention_policy.py`: a pure `decide(kind) -> {"actions": [...], "notification_type": ..., "priority": ...}` function, no LLM involved. ALWAYS NOTIFY: `question_created`, `permission_created`, `task_failed`, `supervisor_alert`. CONFIGURABLE: `task_completed`, gated by `notify_on_completion()` (settings-table override, else `JARVIS_NOTIFY_ON_COMPLETION` env var, defaulting **on**). NOT NOTIFIED BY DEFAULT: `task_started`, `routine_output`, and anything unrecognized — timeline-only. No LLM-based importance scoring was implemented (explicitly deferred, per the task spec, unless the deterministic policy proves insufficient — it has not been shown to be insufficient).

#### Phase 8 — push delivery architecture

`app/push.py` (uses `pywebpush`, new dependency, open-source/free) + `app/notifications.py` (the single `notify()` entry point every producer calls). VAPID keys via `scripts/generate_vapid_keys.py` (uses `py_vapid`, round-tripped and confirmed against `pywebpush`'s `Vapid.from_string`). REST endpoints in `app/main.py`: `GET /api/vapid-public-key`, `POST /api/push/subscribe`, `POST /api/push/unsubscribe` (both subscribe/unsubscribe gated by the optional `JARVIS_API_TOKEN` check). Subscription is associated with a `conversation_id` (nullable). Push payloads carry only a short title/truncated body/type/ids — never task stdout, never full instruction text (Phase 15). Expired/invalid subscriptions (push-service HTTP 404/410) are deleted automatically; any other push failure is logged and otherwise ignored — **push delivery never alters task/question/notification state** (verified structurally: `push.send_push_to_all()` is called only after the DB write and WS broadcast already succeeded, and its own exceptions are caught in `notifications.notify()`). **Real background push delivery to an actual device was not performed in this session** — no VAPID keys were configured against a real deployed HTTPS endpoint with a real phone subscribed; this is disclosed explicitly rather than claimed. What *was* verified for real: the graceful "not configured" path end-to-end (Phase 17 Scenario D), real service-worker registration, and the full push/notification code path via unit + fake-integration tests.

#### Phase 9 — deep-link routing

Client-side, in `app/static/app.js`: `?conversation=`, `?task=`, `?question=`, `?permission=`, `?notification=` query parameters. A `?notification=` link resolves via `GET /api/notification/{id}` (marks it read, returns routing ids) before falling back to scrolling/highlighting the target element (`.deep-link-highlight`, a CSS flash animation). Already-resolved questions/permissions are detected via `GET /api/question/{id}` and reported as "already answered/rejected" instead of showing stale controls. IDs used in URLs are the existing opaque UUIDs already used throughout the app (question_id, task_id, notification_id, `conv_`-prefixed conversation_id) — no new ID scheme, nothing secret in the URL.

#### Phase 10 — deterministic voice fast paths

New `_resolve_deterministic_command()` in `app/supervisor/supervisor.py`, invoked from `process_message()` after the existing status fast-path and before the LLM tool loop — deliberately separate from `_fast_path()` (which many existing tests depend on as a synchronous pure function) since this needs to call tools asynchronously. Handles `"Answer <X>"`, `"Approve/Reject/Deny it"`, `"Stop/Cancel it"` (and close variants): exactly one matching pending item → resolves it and confirms only after the underlying tool call reports success; more than one → asks which and calls no tool at all; zero → defers entirely (returns `None`), which is what preserves prior behavior for phrases like "answer the question" with nothing pending. This is the **same code path for voice and typed text** — a voice transcript is just a string handed to `sendUserMessage()`, indistinguishable from typed input by the time it reaches the supervisor.

#### Phase 11/12 — proactive question/permission loop

Real, non-fixture-assisted verification: `/mock-agent` (a real local subprocess using the existing question protocol) was used as the deterministic-question trigger during Phase 17 Scenario F, since it reliably reaches a native question state without depending on real OpenCode/LLM timing. The full loop — question created → one notification (DB dedup-key verified) → phone-side deep link → answer → task resumes → notification not duplicated across reconnect — was verified end-to-end in Phase 17 Scenario E/F against a real running server. **The specific combination of "OpenCode native question" (rather than the local mock-agent protocol) + "real phone, backgrounded, real push receipt" was not exercised as a single real end-to-end run in this session** — seeing a real OpenCode question requires a real free-tier LLM call that decides to ask one, which is non-deterministic to trigger; the local mock-agent protocol and OpenCode's question protocol are handled by the *same* downstream code (both funnel through `create_question_record` + `AttentionPolicy` + `notifications.notify()`), so this is a fixture-assisted substitution for the trigger only, not for the notification/attention machinery itself. Permission-loop verification (Phase 12) is analogous and was exercised via the fake-integration tests in `tests/test_notification_integration.py` (`_emit_permission`) rather than a live OpenCode permission prompt, for the same non-determinism reason; no destructive permission was approved anywhere in this session.

#### Phase 13 — failure and completion contact

Failure: `tests/test_notification_integration.py::test_verified_failure_produces_one_notification` (fake, via `_handle_session_failed`) plus real local-task failure paths covered by the pre-existing `task_manager.py` tests, now also emitting a `TASK_FAILED` notification (wired in this milestone — see Phase 6/7). Completion: `tests/m6_execution_probe.py` (real, unchanged, still exercises the real isolated OpenCode execution path — see the regression proof below) combined with `test_verified_completion_produces_notification_by_default`. No false-success language is possible structurally: notifications are only ever created from already-verified terminal DB state (principle 3), never from LLM prose.

#### Phase 14 — notification deduplication

Guaranteed at the DB layer (`dedup_key` UNIQUE + `INSERT OR IGNORE`), not by caller discipline. Directly tested: duplicate `_handle_session_failed`/`_handle_session_idle` calls against an already-terminal task (simulating SSE replay and a post-restart re-delivery) produce exactly one notification row and exactly one broadcast (`test_duplicate_failure_evidence_does_not_duplicate_notification`, `test_restart_reprocessing_does_not_duplicate_completion_notification`); a direct `notifications.notify()` idempotency test (`test_notify_idempotent_call_does_not_reduplicate_or_rebroadcast`) covers the "push retry"/"multiple tabs" style re-entry independent of any upstream guard. Reconnect-level dedup (client never re-renders or re-speaks an already-seen notification) verified in both `tests/test_notification_api.py` (WebSocket-level: pending-vs-read resend behavior) and `tests/m7_browser_validate.py` Scenario F (real browser, real reload).

#### Phase 15 — privacy and secret handling

Reviewed every new code path. Never logged: raw audio (never captured to begin with), API keys, VAPID private key (only ever read from `os.environ`, never printed — `scripts/generate_vapid_keys.py`'s own docstring says so explicitly), push subscription auth secrets, full authorization headers. Raw microphone audio is never persisted (structurally — only recognized text ever leaves the browser). Notification bodies are deliberately generic (`"{task_name} needs your permission to continue."` rather than the actual action/path — verified by a direct test asserting the raw filesystem path never appears in a permission notification's body, `test_permission_created_produces_one_notification`). Push payloads carry the same minimal text, truncated further (120 chars).

#### Phase 16 — PWA support

`app/static/manifest.json` (name, icons via a generated SVG monogram — `app/static/icons/icon.svg`, no PNG since no image-generation dependency was added — see Known Limitation below), `app/static/sw.js` (app-shell caching for `/`, `/static/style.css`, `/static/app.js`, `/manifest.json`; explicitly bypasses `/api/*` and `/ws`; push + notificationclick handlers), served at `/manifest.json` and `/sw.js` (root scope) from dedicated `app/main.py` routes. `index.html` updated with manifest link, theme-color, apple/mobile web-app meta tags. Real service-worker registration confirmed working against `http://127.0.0.1:8000` (Phase 17). Full "Add to Home Screen" installability heuristics and standalone-mode launch were not independently verified on a real device in this session (see Final Report limitations) — this is a real, disclosed gap, not implied to be verified.

#### Phase 17 — browser (Playwright) DOM validation

`tests/m7_browser_validate.py`, run against a real Jarvis server (`JARVIS_TEST_MODE=1`, `FakeLLMProvider`, real isolated OpenCode server for the mock-agent/task machinery underneath). SpeechRecognition/speechSynthesis are replaced with deterministic fakes via `page.add_init_script()` for the scenarios that need them, per the task spec's own guidance to use controlled mocks where real hardware/voices aren't available. **Two real, non-obvious browser bugs were found and fixed in the test harness itself** (not app bugs — see "Bugs found and fixed" below) before the suite was trustworthy. **Result (final, run twice for stability): 24/24 PASS, 0 console errors, 0 failed network requests.**

Scenarios: A (voice capability states: supported/mocked, listening, cancel-while-listening submits nothing, unsupported-browser fallback with SpeechRecognition explicitly removed via `Object.defineProperty`), B (transcript submission: enters the pipeline, exactly one message, mic returns to idle, supervisor responds), C (speech controls: enable/disable, a real reply is spoken, no overlapping utterances, queued utterance plays after the first ends, stop button cancels in-progress speech), D (notification settings: completion-policy toggle round-trips through `/api/settings`, push opt-in gracefully reports "not configured" with real `Notification.requestPermission()` — Playwright's `permissions=["notifications"]` context grant used instead of mocking, since that part of the flow works for real even without VAPID keys), E (deep links: real mock-agent question, deep link scrolls+highlights it, answering it then deep-linking again correctly reports "already answered" with no stale controls), F (reconnect dedup: real page reload, conversation identity preserved, notification count unchanged, no repeated `speak()` call — filtered from the harmless per-page-load `cancel()` call, which is not what Phase 14 is about).

Not automated (per the task spec's own instruction — requires real microphone hardware and a real phone): actual spoken audio, actual recognized speech from a real voice, real backgrounded push delivery. See Phase 18 / Final Report for the honest disposition of these.

#### Phase 18 — real phone acceptance test

**Performed 2026-07-10, see Milestone 7.1 below.** (Superseded — at the time this paragraph was first written, no physical phone was available; that gap has since been closed. Preserved as historical record per the project's documentation rules, not deleted.)

#### Bugs found and fixed during this milestone

1. **`"Permission rejectd."` typo**: `f"Permission {decision}d."` produced "rejectd" instead of "rejected" for the reject path (`decision + "d"` only works for "approve" → "approved"). Found by `tests/test_voice_fast_paths.py::test_reject_single_pending_permission_resolves`. Fixed with an explicit approve/reject string branch.
2. **Local (non-OpenCode) question protocol never produced a notification**: `task_manager.py::_handle_question` (the `/mock-agent`/JARVIS_QUESTION: path) was not wired into `notifications.notify()` — only the OpenCode-originated question/permission/failure/completion paths were. Found by `tests/m7_browser_validate.py` Scenario F ("No notification rendered for a pending question"). Fixed by adding the same `notifications.notify(..., KIND_QUESTION_CREATED, ...)` call used elsewhere.
3. **Playwright `add_init_script` with a bare arrow-function string never executes** (test-harness bug, not an app bug): `() => { ... }` as a full init-script body is a dead expression statement in JavaScript — it must be wrapped as an IIFE, `(() => { ... })();`, or Playwright silently registers a script that does nothing, and `app.js` picks up real (headless-Chromium-native, non-functional) `SpeechRecognition` instead of the intended fake. This caused nearly every early speech-related scenario to fail with misleading symptoms ("mic never enters listening state," `undefined` errors) until diagnosed.
4. **`window.speechSynthesis` cannot be overridden with a plain assignment** (test-harness bug): it is a getter-backed, non-configurable-by-default property on `Window` in real Chromium; a bare `window.speechSynthesis = {...}` silently no-ops, leaving the real (voiceless-in-headless) implementation in place. Fixed with `Object.defineProperty(window, "speechSynthesis", {value: fake, configurable: true, writable: true})`.

#### Real OpenCode isolation regression proof

`tests/m6_execution_probe.py` re-run after all Milestone 7 code changes: **PASS** — real execution proven again through the unmodified production code path (session created, instruction submitted, verified `completed` terminal state via `session.diff` evidence, expected file created with exact content match). Confirms Milestone 7 did not regress Milestone 6.1's isolated-storage architecture. No test in this milestone invokes OpenCode against the legacy shared/default Desktop storage.

#### Files created

`app/attention_policy.py`, `app/notifications.py`, `app/push.py`, `scripts/generate_vapid_keys.py`, `scripts/generate_dev_cert.py`, `app/static/manifest.json`, `app/static/sw.js`, `app/static/icons/icon.svg`, `tests/test_notifications.py`, `tests/test_attention_policy.py`, `tests/test_voice_fast_paths.py`, `tests/test_notification_integration.py`, `tests/test_notification_api.py`, `tests/m7_browser_validate.py`.

#### Files modified

`app/database.py` (notifications/push_subscriptions/settings tables + CRUD), `app/models.py` (`NotificationType`/`NotificationPriority`), `app/supervisor/supervisor.py` (`_resolve_deterministic_command`, wired into `process_message`), `app/integrations/opencode_supervisor.py` (notification wiring in `_emit_question`/`_emit_permission`/`_handle_session_failed`/`_handle_session_idle`), `app/task_manager.py` (notification wiring in `_handle_question` and `_monitor_exit`), `app/main.py` (new REST endpoints, `pending_notifications` on WS connect, `/manifest.json`/`/sw.js` routes), `app/static/index.html`, `app/static/app.js`, `app/static/style.css`, `requirements.txt` (+`pywebpush`), `.gitignore` (+`cert.pem`/`key.pem`), `README.md` (HTTPS/push setup instructions).

#### Remaining limitations (disclosed, not hidden)

- Real phone/real microphone/real background push delivery were not tested in this session (Phase 18) — no physical device was available. This is the single largest gap between "implemented and unit/DOM-tested" and "proven in the field."
- iOS Safari behavior (SpeechRecognition support, Web Push's iOS 16.4+/installed-PWA requirement) is assumed from general platform knowledge, not independently re-verified.
- PWA "Add to Home Screen" installability heuristics were not independently confirmed on a real device.
- No PNG icons were generated (SVG-only manifest icons) — most modern Android Chrome versions accept SVG manifest icons, but this was not cross-checked against older/other engines.
- `NotificationType.SUPERVISOR_ALERT` is defined but no code path currently produces it — reserved for a future explicit-escalation mechanism.
- The `m7_browser_validate.py` test harness (a scratch script using plain subprocess management, not the production `OpenCodeServerManager`) occasionally required a `SIGKILL` fallback to terminate uvicorn after repeated rapid start/stop debugging cycles, once leaving an orphaned isolated `opencode.exe` process on port 4097 that required manual `taskkill /T /F` cleanup. This is a test-script hygiene issue (the same category as a similar M6.1 finding), not a production-code regression — the production shutdown path's M6 port-verified termination logic was separately confirmed still correct via `m6_execution_probe.py`'s own clean start/stop cycle.
- Notifications originating from OpenCode-backed tasks are created with `conversation_id=None` (OpenCode tasks aren't currently linked back to the conversation that started them) — deep links to these route by `task_id`/`question_id`, not by conversation, which is sufficient for routing but means a notification can't yet reopen "the conversation that asked for this."
- `JARVIS_API_TOKEN` (the optional push-endpoint auth gate) is off by default, consistent with the project's existing LAN-only no-auth posture (Known Limitation #1, unchanged since Milestone 1) — must be explicitly set before any deployment beyond a trusted LAN.

### Milestone 7.1 — Real-Phone Acceptance Validation — 2026-07-10

Focused task: complete Milestone 7's real-phone acceptance validation only (Phase 18), which the original Milestone 7 session explicitly could not perform (no physical device was available then). **Performed for real this time, against an actual Android phone, with several real bugs found and fixed along the way.** Milestone 8 was not started.

#### Infrastructure setup

- **mkcert**: not previously installed; installed via `scoop` (already configured on this machine, no admin rights required) — v1.4.4. Local CA created and installed into the Windows trust store (`mkcert -install`).
- **LAN IPv4**: `192.168.1.27` (Wi-Fi interface; a Hyper-V virtual-switch interface was also present and correctly ignored).
- **Certificate**: `certs/jarvis-lan-cert.pem` / `certs/jarvis-lan-key.pem`, covering `localhost`, `127.0.0.1`, `192.168.1.27`; stored in `certs/`, added to `.gitignore`. Verified with `openssl verify -CAfile <mkcert rootCA.pem> certs/jarvis-lan-cert.pem` → OK, and with a real (non-`ignore_https_errors`) Playwright Chromium navigation succeeding — confirms the chain is genuinely trusted by a real browser engine on this machine, not just accepted by curl's `-k` flag.
- **Root CA distribution to the phone**: the CA's *public* certificate (`C:\Users\Admin\AppData\Local\mkcert\rootCA.pem`) was served over a temporary plain-HTTP one-file server on the LAN (`http://192.168.1.27:8090/jarvis-dev-ca.pem`) so the phone could download and install it — safe to serve over plain HTTP since a CA's public certificate is not sensitive. The CA's *private* key (`rootCA-key.pem`) never left this machine, was never printed, and was never referenced by anything served to the phone.
- **HTTPS server**: `uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile certs/jarvis-lan-key.pem --ssl-certfile certs/jarvis-lan-cert.pem`. Verified from the laptop before any phone involvement: HTTPS loads, WSS connects and stays open, service worker registers for real, manifest loads, push endpoints reachable, isolated OpenCode runtime healthy, Desktop OpenCode storage hash byte-identical to the M6.1/M7 baseline (untouched).
- **VAPID**: generated via `scripts/generate_vapid_keys.py`, stored only in `.env` (never printed a second time, never committed, never put in this file). *A real bug was hit and fixed while doing this* — see Bugs Found #5 below.

#### Real device

- **Device**: Samsung Galaxy S24 FE
- **Browser**: Chrome (mobile)
- **Deployment mode**: installed as a PWA (via Chrome's "Install app"), running full-screen/standalone
- **Network**: same LAN as the laptop, HTTPS via the mkcert-signed cert above
- **Certificate trust**: confirmed — no certificate warning after installing the root CA

#### Real acceptance checklist result (all 22 steps, per the original Milestone 7 task's Phase 6 checklist)

All 22 steps were run for real and directly observed (not fabricated, not inferred from mocks), several requiring a real bug fix before they passed:

| # | Step | Result |
|---|---|---|
| 1-4 | Connect to LAN, install/trust CA, open HTTPS URL, no cert warning | PASS |
| 5 | Typed message | PASS |
| 6-8 | Mic permission, spoken query, transcript submission | PASS (see Bugs Found #1 for what blocked this initially) |
| 9-10 | Enable spoken responses, verify TTS | PASS — "decent, not bad" per direct user feedback; some speech-to-text transcription accuracy issues noted as a disclosed limitation (see below) |
| 11-13 | Enable notifications, confirm permission, install as PWA | PASS |
| 14-16 | Background app, trigger attention event, notification arrives | **Foreground/recently-active: PASS. True backgrounded (Doze-idle) system push: not reliably achieved** — see "Background push: real, disclosed, unresolved limitation" below |
| 17-18 | Tap notification, correct question/task opens | PASS — deep-link routing confirmed for real on a real system-tray notification tap |
| 19-20 | Answer by voice, workflow resumes | PASS (see Bugs Found #2/#6 — required two real fixes before this worked correctly) |
| 21 | Completion notification per policy | PASS |
| 22 | No duplicate notification | PASS — confirmed exactly one, directly observed by the user |

#### Bugs found and fixed (real-device-driven, in the order discovered)

1. **`#input-area` (mic + text box) invisible entirely while a question was pending** — the most severe finding. `#needs-attention` and `#active-tasks` both had `flex-shrink: 0` in `style.css`, so on a real phone screen they refused to shrink even when there wasn't enough visible height left for `#input-area` below them — the user could see the question and its A/B buttons, but the mic button and text box were pushed off the bottom of the screen entirely, with no way to type or answer by voice, until the app was fully closed and reopened. Root cause distinct from an earlier, narrower `100vh`-vs-`100dvh` viewport fix made during initial deployment (kept, still correct, but insufficient on its own). Fixed by changing both panels to `flex-shrink: 3; min-height: 0;` — they already scroll internally (`overflow-y: auto`), so they're the right ones to compress first; `#input-area` (`flex-shrink: 0`, unchanged) always wins the space. Verified via real Playwright reproduction at a deliberately short viewport (550px) with a real pending question showing, both before (input area cut off) and after (input area fully visible) the fix. Regression-guarded by a new `tests/m7_browser_validate.py` Scenario G.
2. **Voice answers via the main chat mic didn't connect to the pending question**: a real spoken transcript ("approach a") doesn't match the literal `_ANSWER_RE` pattern (`"answer ..."`/`"the answer is ..."`), so it silently fell through to the LLM, which had no way to know a question was even pending and asked for more context. Fixed with `_match_pending_option()`: when exactly one non-permission question is pending and exactly one of its own options appears as a distinct word in the message, resolve it as that answer — still fully deterministic (no LLM call), zero or multiple matching options still defers rather than guessing. Regression-guarded by 6 new tests in `tests/test_voice_fast_paths.py`.
3. **Bell icon's on/off state was nearly indistinguishable** (direct user report: "the background color... is almost similar... just two shades apart"): root cause is that emoji glyphs (🔔🔊✅) don't respond to CSS `color` — they're pre-colored glyphs, not text that inherits `currentColor` — so the intended green tint on the "on" state had no visible effect, leaving only a subtle background shade as the sole differentiator. Fixed with a strong translucent-green background + solid colored border ring + a small corner indicator dot, independent of the icon glyph itself.
4. **The bell icon never reflected an already-existing push subscription**: every page load started the toggle in the "off" visual state regardless of real subscription status, contributing directly to user confusion about whether push was actually enabled. Fixed with `reflectExistingPushSubscription()`, checked on page load via `pushManager.getSubscription()`.
5. **`pywebpush` was only installed in one of two Python environments on this machine**: `pytest` (bare command) resolves to a Python 3.10 install; `pip`/`python` resolve to Python 3.14. `pip install pywebpush` (done during initial Milestone 7 setup) only reached the 3.14 environment. This went unnoticed until `tests/test_push.py` (new in this session) became the first test to actually exercise the `pywebpush` import path — the full suite failed with `ModuleNotFoundError` under the real `pytest` environment despite passing when manually run via `python -m pytest`. Fixed by installing `pywebpush` into the Python 3.10 environment directly.
6. **Real backgrounded push delivery only displayed after the app was foregrounded, never while actually backgrounded** — investigated in depth (see next section). Two real, standard Web Push protocol fixes were applied and verified not to be sufficient alone: `pywebpush`'s default `ttl=0` (fixed to a 12h explicit TTL) and a missing `Urgency` header (fixed to `Urgency: high`, which FCM maps to a Doze-bypassing priority level). Neither fix, together with confirming the installed PWA was explicitly battery-unrestricted and not on Samsung's "sleeping apps" list, resolved the underlying symptom on this specific device — see disposition below.
7. **`.env` corruption from a missing trailing newline**: appending the VAPID keys via a heredoc `cat >>` concatenated onto the end of the existing `JARVIS_LLM_API_KEY` line (which had no trailing newline), silently corrupting that key and making `JARVIS_VAPID_PUBLIC_KEY` invisible to `dotenv` (its value became part of the API key's value instead of its own line). Caught immediately via `push_configured` unexpectedly returning `false`; fixed by inserting the missing newline and confirming both the VAPID keys and the original LLM key round-tripped correctly afterward.
8. **A leftover orphaned isolated `opencode.exe`** was found listening on port 4097 during a routine housekeeping check mid-session (unrelated to any of the above — likely from an earlier rapid restart cycle during debugging). Identified via `netstat`, verified it was not the live phone-serving instance, and cleaned up with `taskkill /T /F`, consistent with the same category of test-harness hygiene issue documented in Milestone 6.1 and the earlier Milestone 7 session.

#### New capability added: per-question voice answering

Directly requested by the user after finding Bug #2 above ("otherwise, add the mic to the needs your attention tab as well so I can use it at both places"): a second mic button now sits next to each pending question's custom-answer box, answering *that specific question* directly via the same `/answer <question_id> <text>` path its adjacent Send button already uses — bypassing the supervisor/LLM ambiguity in Bug #2 entirely rather than only mitigating it. Implemented as a fully separate function (`startVoiceAnswerForQuestion`) rather than a refactor of the already-tested main-mic code path, to avoid any regression risk to that flow. Regression-guarded by a new `tests/m7_browser_validate.py` Scenario H.

#### Background push: real, disclosed, unresolved limitation

Investigated thoroughly, in order, all directly on the real device:

1. Confirmed the server-side push pipeline works for real: a direct `push.send_push_to_all()` call (bypassing the app entirely) returned `delivered=1`, meaning Google's FCM genuinely accepted the message for delivery to the real subscribed endpoint.
2. First real trigger: notification did not appear in the system tray while backgrounded; appeared immediately, both in-app and in the system tray, the moment the user foregrounded the app. WebSocket connect/disconnect log timestamps confirmed the pattern the user suspected — the WebSocket reliably disconnects within seconds to a few minutes of backgrounding, and this correlates with (but does not, by itself, explain) the delayed push processing.
3. Fixed `ttl=0` → non-zero (Web Push spec: `ttl=0` means "drop rather than hold for retry if not immediately deliverable"). Retested: same symptom, unchanged.
4. Fixed missing `Urgency: high` header (RFC 8030; FCM maps this to a Doze-bypassing priority level). Retested: same symptom, unchanged.
5. Confirmed with the user that the installed PWA (not just the browser) was set to "Unrestricted" in Samsung's battery settings specifically (One UI has app-level battery management independent of, and in addition to, stock Android's setting). Retested: same symptom, unchanged.
6. Confirmed with the user the app was not present in Samsung's "sleeping apps"/"deep sleeping apps" list (a further OneUI-specific restriction layer beyond the per-app toggle). Retested: same symptom, unchanged.
7. Performed a full clean reinstall of the PWA (picking up the latest service worker) and retested once more: same symptom, unchanged.

**Disposition**: all standard, documented Web Push reliability mitigations were applied and verified not to close the gap on this specific device (Samsung Galaxy S24 FE, One UI, Chrome). The server-side pipeline is demonstrably correct (FCM accepts every attempt); the remaining gap is in whether/when this specific device's OS+browser combination wakes the service worker while genuinely backgrounded, which is outside what a Jarvis-side code change can control. This is recorded as a real, disclosed limitation (Known Limitation #29, updated) rather than continuing to chase further fixes with diminishing returns, per the user's own explicit direction to move on and validate the rest of the flow. Foreground and recently-active-to-backgrounded delivery both work reliably; true Doze-idle backgrounded delivery does not, on this device.

#### Final automated regression result

`pytest tests/ -v --tb=short`: **252 passed, 0 failed, 0 skipped, 6 warnings** (246 M7-baseline + 6 new natural-phrasing tests in `test_voice_fast_paths.py`). `tests/m7_browser_validate.py`: **31/31 PASS** (25 M7-baseline + Scenario G input-area-visibility + Scenario H per-question-mic, both new). `tests/m6_execution_probe.py`: re-run twice after all fixes — first run reported a false-negative `FAIL` on `terminal_state_reached` under heavy concurrent system load (the live phone server, multiple background test processes, and this probe all running at once) despite the underlying file evidence proving real execution had genuinely succeeded (file created, exact content match); the second immediate re-run **PASS**ed cleanly with identical evidence quality, confirming the first result was a timing flake, not a regression — none of this session's code changes touch the code path `m6_execution_probe.py` exercises (it explicitly bypasses the Supervisor/LLM layer). No Desktop OpenCode storage mutation at any point in this session (hash confirmed byte-identical to the M6.1/M7 baseline).

#### Files created (Milestone 7.1)

`certs/` (gitignored, mkcert-generated dev cert/key), no new application source files — all changes were fixes to existing Milestone 7 files.

#### Files modified (Milestone 7.1)

`app/static/style.css` (flex-shrink layout fix, icon-btn pressed-state visibility, `.question-mic-btn`), `app/static/app.js` (`_match_pending_option` client-side equivalent not needed — natural-phrasing fix is server-side only; `startVoiceAnswerForQuestion`, `reflectExistingPushSubscription`, `spokenTextForNotification` question-content lookup — this last one predates the real-phone session but is listed for completeness), `app/static/sw.js` (cache version bumped v1→v4 across the session's fixes), `app/supervisor/supervisor.py` (`_match_pending_option`), `app/push.py` (`ttl`, `Urgency: high` header), `.env` (VAPID keys added, corruption fixed), `.gitignore` (+`certs/`), `tests/test_voice_fast_paths.py` (+6), `tests/test_push.py` (new, +3), `tests/m7_browser_validate.py` (+Scenario G, +Scenario H, plus fixes to the gated test server's env isolation from real `.env` secrets).

## Current System Architecture

As of Milestone 5, free-text (non-`/`) WebSocket messages are routed to the `Supervisor` (`app/supervisor/supervisor.py`) instead of being ignored. The `Supervisor` sits alongside `Executor` in the request path — both are driven from `app/main.py`'s WebSocket handler, and both call into the same `TaskManager`/`OpenCodeSupervisor` service layer shown below. The `Supervisor` owns its own `ToolRegistry` (`app/supervisor/tools.py`), `LLMProvider`/`FakeLLMProvider` (`app/supervisor/llm.py`), context builder (`app/supervisor/context.py`), and safe-project resolver (`app/supervisor/projects.py`); it does not execute file/shell work itself — every tool ultimately delegates back to `TaskManager` or `OpenCodeSupervisor`.

```
┌─────────────────────────────────────────────────────────────┐
│                    Jarvis FastAPI Server                      │
│                        (app/main.py)                          │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │ Connection   │  │ TaskManager  │  │ OpenCodeSupervisor  │  │
│  │ Manager      │  │ (app/task_   │  │ (app/integrations/  │  │
│  │ (app/conn_   │  │  manager.py) │  │  opencode_          │  │
│  │  manager.py) │  │              │  │  supervisor.py)     │  │
│  │              │  │ - subprocess │  │                     │  │
│  │ - WebSocket  │  │   lifecycle  │  │ - OpenCodeServer    │  │
│  │   broadcast  │  │ - question   │  │   Manager           │  │
│  │   to all     │  │   detection  │  │ - OpenCodeAdapter   │  │
│  │   clients    │  │ - stdin      │  │ - SSE consumer      │  │
│  └──────┬───────┘  │   delivery   │  │ - Poll loop (3s)    │  │
│         │          │ - cancel     │  │ - Event routing     │  │
│         │          │ - shutdown   │  └───────┬─────────────┘  │
│         │          └──────────────┘          │               │
│         │                  │                 │               │
│         ▼                  ▼                 ▼               │
│  ┌───────────────────────────────────────────────────────┐   │
│  │                     Executor                           │   │
│  │              (app/executor.py)                         │   │
│  │  /status /processes /pwd /demo-task /tasks /cancel    │   │
│  │  /mock-agent /answer /attention                       │   │
│  │  /opencode-start /opencode-cancel /opencode-answer    │   │
│  │  /opencode-reject /opencode-permit /opencode-status   │   │
│  └───────────────────────────────────────────────────────┘   │
│                          │                                     │
│                          ▼                                     │
│  ┌───────────────────────────────────────────────────────┐   │
│  │                   SQLite Database                       │   │
│  │              (app/database.py, jarvis.db)              │   │
│  │  ┌──────────┐  ┌───────┐  ┌───────────┐  ┌──────────┐ │   │
│  │  │  events  │  │ tasks │  │ questions │  │ opencode │ │   │
│  │  │          │  │       │  │           │  │ _tasks   │ │   │
│  │  └──────────┘  └───────┘  └───────────┘  └──────────┘ │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐   │
│  │              Frontend (app/static/)                    │   │
│  │  index.html + app.js + style.css                      │   │
│  │  - Timeline (chat-like event log)                     │   │
│  │  - Active Tasks panel (name, elapsed, latest, cancel) │   │
│  │  - Needs Your Attention (questions + permissions)     │   │
│  │  - Permission Approve/Deny buttons                    │   │
│  │  - OpenCode connection status indicator               │   │
│  │  - Custom answer input                                │   │
│  └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│              External Systems                                 │
│                                                               │
│  ┌──────────────────┐    ┌────────────────────────────────┐   │
│  │ Mock Worker      │    │ OpenCode v1.15.10              │   │
│  │ (app/workers/    │    │ (opencode serve)               │   │
│  │  mock_worker.py) │    │                                │   │
│  │                  │    │ REST API + SSE events          │   │
│  │ JARVIS_QUESTION: │    │                                │   │
│  │ protocol on      │    │ Session management             │   │
│  │ stdout, stdin    │    │ Question/permission endpoints  │   │
│  │ for answers      │    │ OpenRouter LLM provider        │   │
│  └──────────────────┘    └────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Implemented vs Verified vs Planned

- **Implemented**: All components in the diagram above, plus the M5 conversational supervisor layer (`app/supervisor/`), exist in code
- **Verified**: Full suite passes (115/115); OpenCode session/prompt/abort lifecycle verified against real v1.15.10 server; browser smoke tests pass (19/19, M3-level); M5 conversational flow validated end-to-end against a real OpenCode server + real free-tier OpenRouter model (7/7 scenarios)
- **Verified but with an open question**: whether OpenCode's own agent actually executes work for API-created sessions — Jarvis's REST calls succeed, but OpenCode's message log is unverifiable in this environment (see Known Limitation #13)
- **Not verified**: M5 conversational flow through an actual browser DOM (Playwright) — only WebSocket-protocol-level validation exists
- **Planned**: voice interface, browser/GUI control, push notifications, additional application adapters (all Milestone 6+, not yet designed)

## Event Model

### Persisted Events (stored in `events` table)

| Event Type | Content JSON | Notes |
|-----------|-------------|-------|
| `user_message` | plain text | Every user message |
| `command_started` | plain text or null | Command execution started |
| `command_output` | plain text | Command output |
| `command_completed` | plain text or null | Command completed |
| `command_failed` | plain text | Command failed |
| `task_started` | `{"task_id","name"}` | Task started |
| `task_stdout` | `{"task_id","name","line"}` | stdout line (question lines filtered) |
| `task_stderr` | `{"task_id","name","line"}` | stderr line |
| `task_completed` | `{"task_id","name","exit_code"}` | Task completed normally |
| `task_failed` | `{"task_id","name","exit_code"}` | Task failed |
| `task_cancelled` | `{"task_id","name","exit_code"}` | Task cancelled |
| `question_asked` | `{"question_id","task_id","task_name","question","context","options"}` | Question detected |
| `question_answered` | `{"question_id","task_id","answer"}` | Question answered |
| `question_cancelled` | `{"question_id","task_id"}` | Question cancelled |

### Broadcast-Only Events (not persisted)

| Event Type | Purpose |
|-----------|---------|
| `history` | Sent on reconnect (batched events) |
| `running_tasks` | Sent on reconnect |
| `pending_questions` | Sent on reconnect |
| `opencode_status` | Sent on reconnect (server alive + OC running tasks + OC pending questions) |
| `opencode_task_created` | Notified when OC session starts |
| `opencode_task_completed` | OC session completed/failed |
| `opencode_task_cancelled` | OC session cancelled |
| `opencode_question_answered` | OC question answered |
| `opencode_question_rejected` | OC question rejected |
| `opencode_permission_handled` | OC permission approved/denied |
| `opencode_message` | OC chat message (role + content) — defined but not currently emitted; see M6 Known Limitations |
| `opencode_error` | OC error event (M6: now actually emitted, from a verified `session.error` SSE event; paired with `opencode_task_completed` status="failed") |
| `task_question` | OC question broadcast (with source="opencode") |
| `task_permission` | OC permission broadcast (with source="opencode") |
| `conversation_init` | (M6, client→server) handshake: browser sends its stored `conversation_id` (or none) |
| `conversation_ready` | (M6, server→client) handshake reply: validated/minted `conversation_id` + bounded prior assistant-message history |
| `notification` | (M7) A newly-created notification, live — the only broadcast type that triggers client-side speech |
| `pending_notifications` | (M7) Sent on reconnect: all notifications with `status='pending'` — never triggers speech (Phase 5/14) |

### Event JSON Structure

All broadcast events share:
```json
{
  "type": "<event_type>",
  "timestamp": "2026-07-08T00:00:00Z",
  "content": "<JSON string or plain text>"
}
```

Task events embed structured JSON in `content`:
```json
{
  "type": "task_stdout",
  "timestamp": "2026-07-08T00:00:00Z",
  "content": "{\"task_id\": \"<uuid>\", \"name\": \"Demo Task\", \"line\": \"Progress 5/15\"}"
}
```

### Reconnect Restoration

On WebSocket connect, the server sends:
1. `history` — last 100 persisted events
2. `running_tasks` — current state of all in-process tasks
3. `pending_questions` — questions with status `pending` only
4. `opencode_status` — OpenCode server alive status + running OC tasks + OC pending questions

## Task State Machine

### States

```
                    ┌──────────┐
                    │  running │ ◄── initial state
                    └─────┬────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │waiting_  │ │completed │ │ failed   │
        │for_user  │ │ (exit=0) │ │ (exit≠0) │
        └─────┬────┘ └──────────┘ └──────────┘
              │
              ▼
        ┌──────────┐
        │ cancelled│
        └──────────┘
```

### Valid Transitions

- `running` → `waiting_for_user` (question detected)
- `running` → `completed` (process exited with code 0)
- `running` → `failed` (process exited with non-zero code, or marked interrupted on startup)
- `running` → `cancelled` (user cancel)
- `waiting_for_user` → `running` (answer delivered to stdin)
- `waiting_for_user` → `cancelled` (user cancel while waiting)
- `waiting_for_user` → `failed` (process crashed while waiting; server restart marks interrupted)
- All terminal states: `completed`, `failed`, `cancelled` — no further transitions

### OpenCode Task State Machine (Milestone 6 — verified, evidence-based)

Distinct from the local-subprocess state machine above: an OpenCode task's state is never inferred from silence, timeout, or absence of activity — every transition requires specific native evidence (see Milestone 6 Phase 4 for the full mapping of state → required evidence).

```
                    ┌──────────┐
                    │  running │ ◄── initial state; also re-entered via
                    └─────┬────┘     activity evidence from waiting/degraded
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │waiting_  │ │completed │ │ failed   │
        │for_user  │ │(idle, no │ │(session. │
        │(question/│ │error,no  │ │ error)   │
        │permission)│ pending) │ └──────────┘
        └─────┬────┘ └──────────┘
              │
              ▼
        ┌──────────┐        ┌──────────┐
        │ cancelled│        │ degraded │ ◄── Jarvis restart; state
        │ (abort   │        │(unknown, │     unknown until reconciled
        │  200 OK) │        │ never    │     (reconcile_on_startup)
        └──────────┘        │ falsely  │
                             │ reported)│
                             └──────────┘
```

- `degraded` is reachable from any non-terminal state (`running`, `waiting_for_user`) on a Jarvis restart — see `db.mark_running_opencode_tasks_interrupted()`. It is not itself terminal: `OpenCodeSupervisor.reconcile_on_startup()` may upgrade it to a verified status once evidence is available (currently never succeeds, given the Milestone 6 Phase 1/3 OpenCode execution blocker — see Known Limitations).
- `session.error` is definitive and takes precedence: a `session.idle` arriving after an error for the same turn does not override `failed`.
- `waiting_for_user` → `running`: only once nothing remains pending for that task (`_clear_waiting_state`); a task with multiple simultaneous pending items stays `waiting_for_user` until all are resolved.

### Cancellation Behavior

1. User sends `/cancel <task_id>` or clicks Cancel button
2. TaskManager cancels any pending questions for this task (broadcasts `question_cancelled`)
3. Adds task_id to `_cancelled` set (prevents `_monitor_exit` from emitting completion)
4. Calls `proc.terminate()` → waits 3s → `proc.kill()` if still alive
5. Updates task status to `cancelled` with exit_code `-1`
6. Broadcasts `task_cancelled`
7. Cleans up process tracking, cancels reader tasks

### OpenCode Cancellation

Separate path via `/opencode-cancel <task_id>`:
1. Looks up OpenCode session mapping
2. Calls `POST /session/{sessionID}/abort` (graceful OpenCode abort)
3. Updates both `tasks` and `opencode_tasks` status to `cancelled`
4. Broadcasts `opencode_task_cancelled`

## Question State Machine

### States

```
pending ──► answered
    │
    └─────► cancelled
```

### States (same for both Jarvis protocol and OpenCode questions)

| State | Meaning |
|-------|---------|
| `pending` | Question detected, waiting for user answer |
| `answered` | User provided answer, delivered to worker |
| `cancelled` | Task cancelled while waiting, or question rejected |

### Detection

- **Jarvis protocol**: `_read_stream()` parses each stdout line via `parse_line()` looking for `JARVIS_QUESTION:` prefix
- **OpenCode**: SSE event `question`/`question_created` or poll loop via `GET /question`

### Persistence

- Stored in `questions` table with: `question_id` (unique), `task_id`, `question`, `context`, `options_json`, `status`, `asked_at`, `answered_at`, `answer`
- Only `pending` questions restored on reconnect

### Answer Routing

- **Jarvis protocol (mock worker)**: Answer written to `_stdins[task_id]` (same-process stdin of the subprocess)
- **OpenCode**: Answer sent via `POST /question/{requestID}/reply` (REST API, not stdin)

### Reconnect Restoration

- On WebSocket connect, `get_pending_questions()` returns only `pending` questions
- These are sent as `pending_questions` event
- OpenCode status also includes OC-specific pending questions

### Cancellation Cleanup

- When a task is cancelled while `waiting_for_user`, all pending questions for that task are marked `cancelled`
- `mark_running_tasks_interrupted()` also cancels any remaining pending questions on startup

## Commands

### Milestone 1/2 Commands

| Command | Syntax | Purpose | Restrictions |
|---------|--------|---------|-------------|
| `/status` | `/status` | Show hostname, OS, time, Python version | None |
| `/processes` | `/processes` | List top 15 CPU-consuming processes | Windows-only (PowerShell) |
| `/pwd` | `/pwd` | Show current working directory | None |
| `/demo-task` | `/demo-task` | Start 15-second demo subprocess | Predefined task only |
| `/tasks` | `/tasks` | Show running and recent tasks with status | None |
| `/cancel` | `/cancel <task_id>` | Cancel a running task | Requires valid task_id |

### Milestone 3 Commands

| Command | Syntax | Purpose | Restrictions |
|---------|--------|---------|-------------|
| `/mock-agent` | `/mock-agent` | Start mock interactive worker (asks question, reads stdin) | Predefined worker only |
| `/answer` | `/answer <question_id> <answer>` | Answer a pending question | Question must be `pending`; requires stdin-wired task |
| `/attention` | `/attention` | Summary of all tasks waiting for user input | None |

### Milestone 4 Commands

| Command | Syntax | Purpose | Restrictions |
|---------|--------|---------|-------------|
| `/opencode-status` | `/opencode-status` | Show OpenCode server health, running tasks, pending questions | OpenCode server must be running |
| `/opencode-start` | `/opencode-start <project_dir> <instruction>` | Start a new OpenCode session | Requires `OPENCODE_SERVER_PASSWORD` env; project_dir must exist |
| `/opencode-cancel` | `/opencode-cancel <task_id>` | Cancel an OpenCode session by Jarvis task_id | Must be an OpenCode task |
| `/opencode-answer` | `/opencode-answer <question_id> <answer>` | Answer an OpenCode question | Question must be pending |
| `/opencode-reject` | `/opencode-reject <question_id>` | Reject an OpenCode question | Question must be pending |
| `/opencode-permit` | `/opencode-permit <permission_id> [approve\|deny]` | Approve or deny an OpenCode permission request | Default: approve if second arg omitted |

### Milestone 5 — Conversational (Non-Slash) Interface

Any WebSocket `user_message` that does **not** start with `/` is routed to the `Supervisor` instead of `Executor`. There is no fixed command syntax — the LLM interprets free text and calls bounded tools (`get_attention`, `list_tasks`, `get_task_status`, `start_opencode_task`, `send_opencode_instruction`, `answer_question`, `resolve_permission`, `cancel_task`, `recent_activity`, `get_projects`) on the user's behalf, or a deterministic fast path answers simple status questions (e.g. "what needs my attention", "what tasks are running", "recent activity") without an LLM call. See Milestone 5 section above for full detail.

### Milestone 7 — Deterministic Voice/Text Commands

Also handled without an LLM call, ahead of the tool-calling loop: `"Answer <X>"`, `"Approve/Reject/Deny it"`, `"Stop/Cancel it"` — see Milestone 7 Phase 10 above. Voice transcripts and typed text both enter here identically.

## Database Schema

### Tables

#### `events`
```sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    content TEXT
);
```

#### `tasks`
```sql
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    command TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    exit_code INTEGER
);
```

#### `questions`
```sql
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT UNIQUE NOT NULL,
    task_id TEXT NOT NULL,
    question TEXT NOT NULL,
    context TEXT,
    options_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    asked_at TEXT NOT NULL,
    answered_at TEXT,
    answer TEXT
);
```

#### `opencode_tasks`
```sql
CREATE TABLE IF NOT EXISTS opencode_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL REFERENCES tasks(task_id),
    session_id TEXT NOT NULL,
    project_dir TEXT NOT NULL,
    instruction TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_evidence_type TEXT,  -- M6: e.g. 'session.error', 'session.idle', 'message.part.delta'
    last_evidence_at TEXT     -- M6: timestamp of last_evidence_type
);
```
`last_evidence_type`/`last_evidence_at` added in Milestone 6 (`database.py::_ensure_column`, an `ALTER TABLE ... ADD COLUMN` migration guarded by `PRAGMA table_info` so it's safe against a pre-existing `jarvis.db`). `status` now also accepts `'degraded'` (Milestone 6) — see Task State Machine below.

#### `conversations`
```sql
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);
```
Milestone 6. Stable per-conversation message history (user/assistant/tool roles).

#### `notifications` (Milestone 7)
```sql
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id TEXT UNIQUE NOT NULL,
    conversation_id TEXT,
    task_id TEXT,
    source_type TEXT NOT NULL,
    source_id TEXT,
    notification_type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'NORMAL',
    dedup_key TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    read_at TEXT
);
```
`dedup_key` UNIQUE is the mechanism that makes `create_notification()` idempotent — see Milestone 7 Phase 6/14. `status` is `'pending'` until `mark_notification_read()` sets it to `'read'`.

#### `push_subscriptions` (Milestone 7)
```sql
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT UNIQUE NOT NULL,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    conversation_id TEXT,
    created_at TEXT NOT NULL
);
```
`p256dh`/`auth` are the browser-issued subscription encryption keys (not secrets Jarvis generates — treat as sensitive per Milestone 7 Phase 15, never logged).

#### `settings` (Milestone 7)
```sql
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```
Currently only `notify_on_completion` (`"true"`/`"false"`). Not for secrets.

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
CREATE INDEX IF NOT EXISTS idx_tasks_task_id ON tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_questions_question_id ON questions(question_id);
CREATE INDEX IF NOT EXISTS idx_questions_task_id ON questions(task_id);
CREATE INDEX IF NOT EXISTS idx_oc_tasks_task_id ON opencode_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_oc_tasks_session_id ON opencode_tasks(session_id);
```

### Configuration

- **WAL mode**: Enabled via `PRAGMA journal_mode=WAL` in `get_conn()`
- **Database file**: `jarvis.db` in project root (configurable via `DB_PATH` variable)

## Testing Status

### Automated Test History

| Milestone | Test File | Tests | Result | Date |
|-----------|-----------|-------|--------|------|
| M2 | `tests/test_tasks.py` | 8 | 8/8 PASS | Not recorded |
| M3 | `tests/test_questions.py` | 16 | 16/16 PASS | Not recorded |
| M3 (combined) | tasks + questions | 24 | 24/24 PASS | Not recorded |
| M4 (original) | `tests/test_opencode.py` | 13 | 13/13 PASS | 2026-07-08 |
| M5 | `tests/test_supervisor.py` | 70 (65 original + 5 closure regression) | 70/70 PASS | 2026-07-09 |
| M5 | `tests/test_env.py` | 6 | 6/6 PASS | 2026-07-09 |
| M5 closure | `tests/test_opencode.py` (+2 double-encoding regression tests) | 15 | 15/15 PASS | 2026-07-09 |
| **M5 total (full suite)** | `pytest tests/ -v --tb=short` | **115** | **115/115 PASS, 0 failed, 0 skipped, 2 warnings, 175.14s** | **2026-07-09** |
| M6 | `tests/test_opencode.py` (+7 real-SSE-schema tests, replacing 1 obsolete test) | 21 | 21/21 PASS | 2026-07-09 |
| M6 | `tests/test_supervisor.py` (+2 net; attention redesign) | 72 | 72/72 PASS | 2026-07-09 |
| M6 | `tests/test_process_ownership.py` (new) | 15 | 15/15 PASS | 2026-07-09 |
| M6 | `tests/test_opencode_lifecycle.py` (new) | 15 | 15/15 PASS | 2026-07-09 |
| M6 | `tests/test_conversation_continuity.py` (new) | 6 | 6/6 PASS | 2026-07-09 |
| **M6 total (full suite)** | `pytest tests/ -v --tb=short` | **159** | **159/159 PASS, 0 failed, 0 skipped, 2 warnings (same 2 as always), 250.65s (final run)** | **2026-07-09** |
| M6.1 | `tests/test_opencode_isolation.py` (new) | 20 | 20/20 PASS | 2026-07-09 |
| **M6.1 total (full suite)** | `pytest tests/ -v --tb=short` | **179** | **179/179 PASS, 0 failed, 0 skipped, 2 warnings (same 2 as always), 270.41s** | **2026-07-09** |
| M7 | `tests/test_notifications.py` (new) | 15 | 15/15 PASS | 2026-07-09 |
| M7 | `tests/test_attention_policy.py` (new) | 13 | 13/13 PASS | 2026-07-09 |
| M7 | `tests/test_voice_fast_paths.py` (new) | 15 | 15/15 PASS | 2026-07-09 |
| M7 | `tests/test_notification_integration.py` (new) | 8 | 8/8 PASS | 2026-07-09 |
| M7 | `tests/test_notification_api.py` (new) | 13 | 13/13 PASS | 2026-07-09 |
| **M7 total (full suite)** | `pytest tests/ -v --tb=short` | **243** | **243/243 PASS, 0 failed, 0 skipped, 6 warnings (2 known + up to 4 flaky Windows teardown warnings), 377.35s** | **2026-07-09** |
| M7.1 | `tests/test_voice_fast_paths.py` (+6 natural-phrasing tests) | 21 | 21/21 PASS | 2026-07-10 |
| M7.1 | `tests/test_push.py` (new) | 3 | 3/3 PASS | 2026-07-10 |
| **M7.1 total (full suite)** | `pytest tests/ -v --tb=short` | **252** | **252/252 PASS, 0 failed, 0 skipped, 6 warnings, 371.38s** | **2026-07-10** |

### Browser Smoke Test History

| Scenario | Title | Initial Result | Final Result |
|----------|-------|---------------|-------------|
| A | Single worker and reconnect | PASS | PASS |
| B | Two simultaneous workers | FAIL (`.first` targeting bug) | PASS |
| C | Cancellation while waiting | FAIL (async predicate bug + state leak) | PASS |

**Three issues discovered during browser smoke testing:**

1. **Async predicate bug**: `wait_until()` checked truthiness of async generator instead of boolean result. Fixed by adding explicit `await` in predicates.

2. **`.first` answer targeting bug**: Both workers had identical "Which approach?" question text; using `.first` on option buttons always answered worker 1's question twice. Fixed by using `items.nth(0)` and `items.nth(1)`.

3. **Cross-scenario question state leak**: Scenario A left a pending question; scenario B inherited it. Fixed by adding `cleanup_tasks()` to cancel all tasks between scenarios.

**Final browser result: 19/19 PASS, 0 failures**

### Real Integration Smoke Test

**Test**: `C:\Users\Admin\AppData\Local\Temp\jarvis-m4-recon\smoke_test_opencode.py`
**Result**: PASS
**Verified**: Server start, health check, session creation, prompt send (204), session info retrieval, abort, server stop — all against real `opencode v1.15.10` on port 4101
**Date**: 2026-07-08

### M5 Real End-to-End Validation

**Test**: `tests/m5_validate2.py` (7 scenarios: A/B/C/D/H/I/J, over WebSocket against a real Jarvis server + real `opencode serve` v1.15.10 + real OpenRouter `openrouter/free` model)
**Result**: 7/7 PASS (final run, 2026-07-09, after Bugs 1–3 fixes and Scenario D/J assertion strengthening — see Milestone 5 Validation Closure above for full detail, per-scenario results, and the discovered OpenCode message-log limitation)
**Not covered**: no Playwright/browser-DOM-level validation exists for M5 (see Known Limitations)

### M6 Real Execution Proof

**Test**: `tests/m6_execution_probe.py` (gated, not part of `pytest tests/`)
**Result (2026-07-09, pre-6.1)**: FAIL — real execution not proven, exactly as predicted by the Phase 1/3 OpenCode DB bug investigation. Full itemized evidence (A–G) in Milestone 6 Phase 2 above.
**Result (2026-07-09, post-6.1, same script, unchanged)**: **PASS** — real execution proven, twice (fresh + restarted/reused isolated storage). See Milestone 6.1 Phase 5/11. The pre-6.1 FAIL is preserved above as historical evidence, not deleted, per the project's documentation rules — it was the correct, verified result at the time.
**Date**: 2026-07-09

### M6 Server Ownership/Shutdown — Real Verification

3+ real start/stop cycles (`OpenCodeServerManager`, real `opencode.exe`): each resolved a distinct real owned PID, released the port every time. Real external-server preservation verified (untouched after a full Jarvis cycle). Real auth-mismatch detection verified (`OpenCodeServerError` raised, mismatched server left alive, manually cleaned up by the session). See Milestone 6 Phases 5/6/13.
**Date**: 2026-07-09

### M6 Real Playwright DOM Validation

**Tool**: `playwright-cli` against a real running Jarvis server (real OpenRouter free-tier LLM, real `opencode serve`)
**Result**: Scenarios A–F all PASS (see Milestone 6 Phase 11 for full detail and the two live-discovered bugs fixed along the way: `opencode_task_completed` never reaching the timeline, and duplicate timeline entries on reconnect)
**Console/network**: zero errors beyond one harmless pre-existing `favicon.ico` 404
**Date**: 2026-07-09

### M6.1 OpenCode Storage Isolation — Real Verification

**Isolation probe**: real, fully-isolated `opencode serve` (all 4 XDG vars), safe sandbox — server start/health/version verified, fresh isolated DB created, 54 real SSE events observed, zero `session_message.seq` errors, message-history endpoint returned real data for the first time in the entire investigation.
**Execution proof**: `tests/m6_execution_probe.py` run twice through the real production code path (fresh + restarted/reused isolated storage) — **PASS** both times: file created, exact content match, verified `completed` terminal state.
**Desktop preservation**: byte-identical hash/mtime confirmed before/after both execution-proof runs and the full 179-test pytest suite. (One unexpected mutation *did* occur earlier, during individual-variable path-resolution diagnostics — documented in full in Milestone 6.1 above, not during any of the isolated/production-path runs.)
**Date**: 2026-07-09

### M7 Real Playwright DOM Validation

**Test**: `tests/m7_browser_validate.py` (gated, not part of `pytest tests/`) against a real running Jarvis server, real isolated OpenCode server underneath, controlled fakes for SpeechRecognition/speechSynthesis (see Milestone 7 Phase 17 for why and how).
**Result**: 24/24 PASS (run twice consecutively for stability), 0 console errors, 0 failed network requests. Two real test-harness bugs found and fixed along the way (arrow-function init-script not self-invoking; `window.speechSynthesis` requiring `Object.defineProperty`) plus one real app bug (local/mock-agent questions weren't wired into the notification system) and one real string-formatting bug ("Permission rejectd.") — see Milestone 7 "Bugs found and fixed."
**Date**: 2026-07-09

### M7 Real OpenCode Isolation Regression Proof

`tests/m6_execution_probe.py` re-run, unchanged, after all Milestone 7 code changes: **PASS** — confirms Milestone 7 did not regress Milestone 6.1's isolated-storage architecture. See Milestone 7 section for full detail.
**Date**: 2026-07-09

### M7.1 Real Phone Acceptance — Samsung Galaxy S24 FE

**Device/browser/deployment**: Samsung Galaxy S24 FE, Chrome (mobile), installed as a standalone PWA, real HTTPS via mkcert over LAN.
**Result**: 22/22 checklist steps PASS, with 6 real bugs found and fixed along the way (layout, voice-answer routing, toggle visibility, subscription-state reflection, `pywebpush` environment, `.env` corruption) — see Milestone 7.1 for full detail and the exact fixes.
**Voice**: works, "decent, not bad" per direct user feedback; transcription accuracy has minor real-world errors (browser STT limitation, not a Jarvis bug, no fix available without a paid API — disclosed, not fixed).
**TTS**: works, decent quality.
**Background push**: does not reliably deliver while the device is truly Doze-idle-backgrounded on this device, despite all standard mitigations applied (TTL, Urgency, battery-unrestricted, deep-sleep exclusion, fresh reinstall) — real, disclosed, unresolved platform limitation, not a code bug. Foreground and recently-active-to-backgrounded delivery both work.
**Deep link**: confirmed — tapping a real system-tray notification opened the correct pending question.
**Regression**: `pytest tests/` 252/252 PASS; `tests/m7_browser_validate.py` 31/31 PASS; `tests/m6_execution_probe.py` PASS (after one timing-flake false-negative under heavy concurrent system load, immediately re-confirmed).
**Date**: 2026-07-10

### Known Windows Asyncio Warning

A `PytestUnraisableExceptionWarning` involving `_ProactorBasePipeTransport.__del__` is observed during test teardown on Windows. This is a known Python 3.14 asyncio cleanup issue on Windows with proactor event loop and subprocess pipes. It is unrelated to runtime behavior and does not affect test validity.

## OpenCode Integration Decision

### Decision (Milestone 6.1 addendum): isolated storage for the Jarvis-owned server

Jarvis's `opencode serve` instance runs against an isolated `XDG_DATA_HOME`/`XDG_CONFIG_HOME`/`XDG_CACHE_HOME`/`XDG_STATE_HOME` (default `%LOCALAPPDATA%\JarvisOpenCodeRuntime`, override via `JARVIS_OPENCODE_RUNTIME_DIR`), never OpenCode Desktop's shared `~/.local/share/opencode`/`~/.config/opencode`. Chosen over upgrading/pinning to Desktop's version, a supported-migration path (none exists), or continuing shared storage (already proven broken — see Milestone 6 Phase 1/3 and Milestone 6.1). Scored against 4 alternatives in Milestone 6.1's architecture decision matrix; isolation won on every axis except "no new directory concept to reason about." **This does not change the `opencode serve` + REST + SSE decision below** — it only isolates *storage*, not the integration surface.

### Decision: `opencode serve` + REST + SSE

**Chosen over:**

| Alternative | Score | Reason for Rejection |
|-------------|-------|---------------------|
| Terminal scraping / PTY | 13/50 | Fragile, Windows-incompatible, no structured data |
| `opencode run` (default output) | 20/50 | Single-shot, requires existing session, no question/permission handling |
| `opencode run --format json` | 25/50 | Single-shot, fire-and-forget, no question/permission API |
| ACP Protocol (`opencode acp`) | 40/50 | Structured but requires implementing ACP client handlers; more complex than HTTP API |
| **HTTP Server API (`opencode serve`)** | **47/50** | **Best across all criteria — dedicated endpoints, SSE, session management** |

### Scoring Criteria (1-5, 5 best)

| Criterion | serve | ACP | run --json | run | stdio | PTY |
|-----------|:-----:|:---:|:----------:|:---:|:-----:|:---:|
| Reliability | 5 | 4 | 3 | 2 | 2 | 1 |
| Structured observability | 5 | 5 | 3 | 1 | 1 | 1 |
| Question detection | 5 | 5 | 2 | 1 | 1 | 1 |
| Answer delivery | 5 | 5 | 2 | 1 | 2 | 2 |
| Session persistence | 5 | 4 | 3 | 3 | 1 | 1 |
| Concurrent sessions | 5 | 3 | 1 | 1 | 2 | 1 |
| Cancellation | 5 | 4 | 2 | 1 | 3 | 2 |
| Implementation complexity | 2 | 2 | 3 | 4 | 3 | 1 |
| Compatibility | 5 | 4 | 3 | 4 | 3 | 2 |
| Maintainability | 5 | 4 | 3 | 2 | 2 | 1 |
| **Total** | **47** | **40** | **25** | **20** | **20** | **13** |

This decision record should prevent a future agent from accidentally replacing REST+SSE with terminal automation without strong evidence.

## Browser Testing Capability

- **Playwright CLI package**: `@playwright/cli@latest`
- **Observed version**: `0.1.15`
- **Installed globally** (detected via `npx`)
- **Skills path**: `.claude/skills/playwright-cli/`
- **Configuration**: `opencode.json` references Playwright CLI capability:

```json
{
  "playwright-cli": {
    "type": "local",
    "command": ["npx", "-y", "@playwright/cli@latest"]
  }
}
```

**Available commands**: `open`, `click`, `type`, `snapshot`, `screenshot`, `eval`, `close`

Used for browser smoke testing of Milestone 3. May later be useful for Jarvis browser-control work, but general browser automation is not yet part of the Jarvis runtime architecture.

## Known Bugs, Limitations, and Technical Debt

### Active Issues

1. **No authentication**: Anyone on the network can connect to the WebSocket. No user identity or access control.
2. **No TLS**: WebSocket uses plain WS, not WSS. All traffic is unencrypted.
3. **LAN-only assumptions**: No secure remote connectivity. Deployment assumes local network.
4. **Unbounded event/task table growth**: No rotation or archiving of `events`, `tasks`, `questions`, or `opencode_tasks` tables. Will grow indefinitely.
5. **Windows-specific process behavior for local subprocesses**: `TaskManager`'s local-task termination is best-effort on Windows (some processes ignore SIGTERM-equivalent). Note: this is now fixed specifically for the OpenCode server process (see Resolved Issues, M6) — this item covers `task_manager.py`'s general subprocess handling, unchanged.
6. **Subprocess termination delay (local tasks)**: `terminate()` → 3s timeout → `kill()` may leave orphaned processes in edge cases, for `task_manager.py`-managed local subprocesses (M2/M3). Not the OpenCode server, which now uses port-verified termination (M6).
7. ~~OpenCode API-created sessions cannot be proven to execute work~~ — **RESOLVED 2026-07-09, Milestone 6.1.** Root cause was real (shared-storage cross-version incompatibility), fix was storage isolation, not an upstream change. See Resolved Issues and Milestone 6.1.
8. ~~No voice interface~~ — **RESOLVED 2026-07-09, Milestone 7** (browser SpeechRecognition + speechSynthesis). See Resolved Issues and Milestone 7. Real phone/microphone hardware validation remains outstanding — see item 29.
9. **No generic browser control in Jarvis runtime**: Playwright is a testing tool; no browser automation architecture.
10. **No GUI application control**: No vision-based or accessibility-based application interaction.
11. ~~No push notifications~~ — **PARTIALLY RESOLVED 2026-07-09, Milestone 7**: full Web Push infrastructure exists (VAPID, subscribe/unsubscribe, service worker) and in-app/foreground notifications work regardless of push configuration, but real background push delivery to a real device was not verified — see item 29. Phone must still maintain an open WebSocket connection for guaranteed foreground delivery.
12. **Windows asyncio teardown warning**: `PytestUnraisableExceptionWarning` with `_ProactorBasePipeTransport.__del__` during test teardown. Known Python 3.14 Windows issue.
13. **`reconcile_on_startup()` cannot currently upgrade any `degraded` OpenCode task left over from pre-6.1 shared-storage usage** (M6, still open post-6.1): the shared DB those tasks would reconcile against has itself lost relevant rows (see Milestone 6.1's Phase 1 incident). Not revisited in 6.1; new isolated-runtime tasks are unaffected going forward.
14. **Permission handling DB conflation**: Permissions are stored in the `questions` table with prefixed text rather than a separate `permissions` table. This mixes two semantically distinct concepts.
15. **SSE with `directory=*`**: The SSE consumer uses `directory=*` as the parameter. This was not verified against the real server's behavior with the `*` wildcard (event *routing* now correctly parses whatever it receives — see Resolved Issues — this item is only about the `directory=*` subscription scope itself).
16. **OpenCode version sensitivity**: The adapter was verified against v1.15.10. Future OpenCode versions may change API endpoints or event formats.
17. **No timeout for unanswered questions**: Tasks can remain in `waiting_for_user` indefinitely. No automatic cancellation or timeout.
18. **No answer retraction**: Once an answer is submitted, there is no way to undo or modify it.
19. **Mock worker timing**: Uses fixed delays (1s normal, 0.1s test mode). Real workers have variable timing.
20. **`question.asked`/`permission.asked` SSE events are only a "poll now" trigger, not fully parsed** (M6): their real nested schema (`questions: [...]` array, different field names) differs from the legacy poll-endpoint shape that `normalize_question`/`normalize_permission` handle; unifying these was deferred given time constraints — the poll loop remains the correctness-critical path.
21. **OpenCode chat message content is not streamed to the Jarvis timeline** (M6): `message.part.updated`/`message.updated` SSE events are not wired up (schema not verified in the time available); `normalize_message()`/`_emit_message()` remain defined but unused.
22. **SSE reconnect-mechanics have no dedicated test**: `consume_events()`'s exponential backoff (M4, unchanged) was not given a new test in M6; only the event-parsing layer built on top of it was verified as fixed. The Phase 4 idempotent/evidence-based handlers structurally prevent a reconnect from falsely completing/failing a task regardless.
23. **`.jarvis_opencode_owner.json` ownership marker is a single global file**, not scoped per-port (M6): running two Jarvis instances against two different OpenCode ports on one machine would need this generalized.
24. **`session.diff` (file-change evidence) is parsed and logged but not otherwise surfaced** (M6): not used as execution proof beyond the Phase 2 filesystem check, not shown in the UI.
25. **Root cause of the original `session_message.seq` failure is not fully proven** (M6.1, 2026-07-09): high-confidence hypothesis (cross-version incompatible rows in the shared DB) based on behavior, not a line-by-line schema/trigger diff — the direct evidence trail was lost in the Phase 1 incident (item 26) before deeper forensics could be done.
26. **Running the real `opencode.exe` binary against the shared/default (non-isolated) path carries a demonstrated mutation risk** (M6.1, 2026-07-09): a `debug paths` invocation with only some `XDG_*` variables set (leaving data pointed at the shared path) coincided with the shared database's `session_message` table dropping from 59 to 0 rows. Mechanism not fully confirmed (see item 25). Practical mitigation: never invoke `opencode.exe` against the default/shared path for any reason going forward; always use full isolation (all 4 `XDG_*` variables set together) or none of the variables set with intent to inspect Jarvis-owned isolated state only.
27. **Isolated OpenCode's own model selection is not pinned by Jarvis** (M6.1, 2026-07-09): the isolated runtime picks whatever model OpenCode itself defaults to (observed: `gpt-5.3-chat-latest`/openai in one run) — Jarvis only controls which *credentials* (OpenRouter) are available, not which model is actually used for a given task. Distinct from the `JARVIS_LLM_FREE_ONLY` guard, which governs only Jarvis's own supervisor LLM.
28. **`reconcile_on_startup()`'s degraded-task upgrade logic was not extended to query the now-working isolated-runtime message/status endpoints** (M6.1): it still only performs the same `GET /session/{id}` check from M6, which was never the blocker — a future pass could use the now-functional message-history endpoint for real reconciliation.
29. ~~Real phone/real microphone/real background push delivery not tested~~ — **RESOLVED 2026-07-10, Milestone 7.1** (real device tested; background push specifically remains a real, disclosed platform limitation — see item 35). See Resolved Issues and Milestone 7.1.
30. **iOS Safari voice/push behavior not independently verified** (M7): assumed from general platform knowledge (limited/no SpeechRecognition support historically, Web Push requiring iOS 16.4+ and an installed-to-homescreen PWA), not re-derived in this session. Only Android (Chrome, Samsung Galaxy S24 FE) has been tested for real.
31. ~~PWA installability heuristics not confirmed on a real device~~ — **RESOLVED 2026-07-10, Milestone 7.1**: confirmed real installability and standalone launch on a Samsung Galaxy S24 FE (Chrome).
32. **SVG-only manifest icons** (M7): no PNG icons were generated (no image-generation dependency was added); confirmed working on Android Chrome (Milestone 7.1) but not cross-checked against older engines.
33. **OpenCode-originated notifications have no `conversation_id` link** (M7): `opencode_tasks` isn't currently linked back to the conversation that started it, so these notifications route by `task_id`/`question_id` only, not by conversation.
34. **`NotificationType.SUPERVISOR_ALERT` is defined but unused** (M7): reserved for a future explicit-escalation mechanism; no code path produces it yet.
35. **Real background (Doze-idle) push delivery does not reliably work on a real device tested** (M7.1, 2026-07-10): confirmed on a Samsung Galaxy S24 FE (Chrome, installed PWA) after applying every standard mitigation — non-zero TTL, `Urgency: high` header, per-app battery "Unrestricted," confirmed not in Samsung's sleeping-apps list, fresh reinstall. The server-side push pipeline is demonstrably correct (FCM accepts every attempt); the gap is whether/when this device's OS+browser wakes the service worker while genuinely backgrounded, which is outside Jarvis's control. Foreground and recently-active-to-backgrounded delivery both work. See Milestone 7.1 "Background push" section for the full investigation.
36. **Browser speech-to-text transcription accuracy has real-world errors** (M7.1, 2026-07-10): direct user feedback — voice input "works" and is "decent, not bad," but occasional transcription mistakes change the semantic meaning of a spoken answer. Inherent to the browser's own built-in recognition engine (not something Jarvis controls); no fix available without a paid cloud transcription API, which is explicitly out of scope without approval. Partially mitigated by Milestone 7.1's natural-phrasing matching (item resolved: does not require exact "Answer B" phrasing) and the per-question mic, but a genuinely mis-transcribed word can still resolve to the wrong option.
37. **`.env` files without a trailing newline are fragile to append-based setup scripts** (M7.1, 2026-07-10): discovered when appending VAPID keys via `cat >>` silently corrupted the preceding key. No code-level guard was added (the fix was procedural, catching it via `push_configured` returning unexpectedly `false`) — a future setup script that appends to `.env` should verify or ensure a trailing newline first.

### Resolved Issues

- **Async predicate bug** (browser smoke test): Fixed by adding explicit `await` in predicates.
- **`.first` answer targeting** (browser smoke test): Fixed by using `items.nth()` instead of `.first`.
- **`httpx` not in requirements.txt** (M4 limitation #13): resolved — `httpx>=0.28.0` is listed in `requirements.txt`.
- **No LLM supervisor** (former M4 limitation #7): resolved by Milestone 5's `Supervisor`/`ToolRegistry`/`LLMProvider`.
- **WebSocket double-encoding** (M5 closure, 2026-07-09): `OpenCodeSupervisor._notify_broadcast` no longer double-`json.dumps`es broadcast payloads; see Milestone 5 Validation Closure for symptom/root cause/fix/regression test.
- **task_id/question_id truncation broke all follow-up tool calls** (M5 closure, 2026-07-09): fixed in `app/supervisor/{tools.py,context.py,supervisor.py}`; see Milestone 5 Validation Closure for detail.
- **Fast-path attention crash on waiting OpenCode task** (M5 closure, 2026-07-09): fixed a `'t'.get(...)` typo in `app/supervisor/supervisor.py::_fast_path`; see Milestone 5 Validation Closure for detail.
- **Cross-scenario question state leak** (browser smoke test): Fixed by adding `cleanup_tasks()`.
- **Race condition in _monitor_exit** (M2): Readers now stored before monitor starts, gathered on exit.
- **SSE event schema was never real / all real SSE events silently dropped since M4** (M6, 2026-07-09): `process_sse_event()` assumed a named `event:` SSE field that the real server never sends (real events are unnamed `data:` frames with the type nested at `payload.type`). Fixed against the verified real schema; see Milestone 6 Phase 10.
- **`opencode serve` child process not terminated on shutdown** (M6, 2026-07-09): root cause found (the tracked launcher PID exits almost immediately, leaving the real server as a parentless orphan) and fixed via port-resolved PID identification + `taskkill /T` escalation; see Milestone 6 Phase 5. Verified with 3+ real cycles, zero orphans.
- **`opencode_tasks.status` never reached `waiting_for_user`, and resolving a question/permission never resumed the task** (M6, 2026-07-09) (former item 22): both sides now updated together; `_clear_waiting_state()` added. See Milestone 6 Phase 8.
- **`mark_running_opencode_tasks_interrupted()` falsely reported `failed` on restart** (M6, 2026-07-09): now reports `degraded` (state unknown) instead — a Jarvis restart does not prove an OpenCode session failed. See Milestone 6 Phase 4.
- **Redundant/dead DB lookup in `approve_permission()`** (former item 15, M6, 2026-07-09): removed.
- **No conversation continuity across WebSocket turns** (former item 21, M6, 2026-07-09): stable `conversation_id` handshake implemented, real-verified via Playwright reconnect. See Milestone 6 Phase 7.
- **No Playwright/browser-DOM-level validation** (former item 23, M6, 2026-07-09): 6 scenarios (A–F) run against a real server; 2 further bugs found and fixed live (`opencode_task_completed` never rendering in the timeline; reconnect duplicating live-then-replayed events). See Milestone 6 Phase 11.
- **OpenCode execution could not be proven / message-history endpoint appeared broken** (former item 7, M6.1, 2026-07-09): both were downstream symptoms of shared-storage cross-version incompatibility. Fixed by isolating the Jarvis-owned server's storage (`XDG_DATA_HOME`/`CONFIG_HOME`/`CACHE_HOME`/`STATE_HOME`, all four verified via `opencode debug paths`) — real execution now proven twice (fresh + restart/reuse), message-history endpoint now returns real data. See Milestone 6.1.
- **No voice interface** (former item 8, M7, 2026-07-09): browser SpeechRecognition input + speechSynthesis output added, same code path as typed text. See Milestone 7 Phases 4/5.
- **No push notifications** (former item 11, M7, 2026-07-09, partial): full notification model, AttentionPolicy, and Web Push infrastructure added; foreground/in-app delivery fully verified, real background push delivery not yet verified on a real device (item 29). See Milestone 7 Phases 6–9.
- **`"Permission rejectd."` string bug**, **local-question notifications never wired**, and two Playwright test-harness bugs (bare arrow-function init scripts, non-configurable `speechSynthesis`) (M7, 2026-07-09): all fixed — see Milestone 7 "Bugs found and fixed."
- **`#input-area` (mic + text box) invisible off-screen while a question was pending, on a real phone** (M7.1, 2026-07-10): `#needs-attention`/`#active-tasks` had `flex-shrink: 0`, refusing to yield space to `#input-area`. Fixed with `flex-shrink: 3; min-height: 0` on both panels. See Milestone 7.1 Bugs Found #1.
- **Voice answers via the main chat mic didn't connect to the pending question** (M7.1, 2026-07-10): a real transcript like "approach a" doesn't match the literal "answer ..." pattern. Fixed with `_match_pending_option()` deterministic option-word matching, plus a new per-question mic button that bypasses the ambiguity entirely. See Milestone 7.1 Bugs Found #2 and "New capability added."
- **Bell icon on/off state nearly indistinguishable** (M7.1, 2026-07-10): emoji glyphs don't respond to CSS `color`. Fixed with a background/border/indicator-dot treatment independent of the glyph. See Milestone 7.1 Bugs Found #3.
- **Push toggle never reflected an already-existing subscription** (M7.1, 2026-07-10): fixed with `reflectExistingPushSubscription()` on page load. See Milestone 7.1 Bugs Found #4.
- **`pywebpush` missing from the Python environment `pytest` actually uses** (M7.1, 2026-07-10): two separate Python installs on this machine; installed into the correct one. See Milestone 7.1 Bugs Found #5.

## Current Milestone

**Milestone 6, Milestone 6.1, Milestone 7 (Voice and Proactive Contact), and Milestone 7.1 (Real-Phone Acceptance Validation) all complete and closed (2026-07-10).**

Current system state: everything from Milestones 5/6/6.1/7, now real-device-validated on a Samsung Galaxy S24 FE (Chrome, installed PWA) over real HTTPS (mkcert). All 22 real-phone acceptance checklist steps PASS. Six real bugs found and fixed during real-device testing (see Milestone 7.1): a layout bug that hid the mic/text box entirely while a question was pending, voice answers not connecting to pending questions for natural phrasing, an unreadable notification-toggle visual state, a push toggle not reflecting real subscription state, a `pywebpush`/Python-environment mismatch, and `.env` corruption from a missing trailing newline. A new per-question mic button was added directly at the user's request. Full pytest suite: **252/252 passing**. Real browser DOM validation: **31/31 PASS**. Real isolated OpenCode execution re-verified unregressed (`tests/m6_execution_probe.py` PASS, after one confirmed timing-flake false-negative under heavy concurrent load). **One real, disclosed, unresolved limitation remains**: background (Doze-idle) push notification delivery does not reliably work on the tested device despite every standard mitigation being applied — see Known Limitation #35. This is a platform-level gap, not a code bug, and is not expected to be closeable via further Jarvis-side changes.

**Do not begin Milestone 8 without explicit instruction.**

## Next Planned Work

### Immediate priorities before any new milestone work

1. **Investigate whether a native Android wrapper (e.g. a Trusted Web Activity) would resolve the background-push gap** (Known Limitation #35) — worth a small, targeted spike *only* if background push turns out to matter enough in practice; do not treat this as justification for a full native rewrite (a Flutter-equivalent rewrite was explicitly considered and rejected as disproportionate during Milestone 7.1 — see that section's user discussion).
2. **Test on an iOS Safari device** (Known Limitation #30) — only Android has been real-device-tested so far.
3. **Understand the M6.1 Phase 1 incident mechanism more precisely** (Known Limitation #25/26) if further confidence is wanted — not blocking.
4. **Unify `question.asked`/`permission.asked` SSE parsing with the poll-loop schema** (Known Limitation #20).
5. **Wire up `message.part.updated`/`message.updated` SSE events** (Known Limitation #21) to stream real OpenCode chat content into the Jarvis timeline live.
6. **Link OpenCode-originated notifications back to their originating `conversation_id`** (Known Limitation #33) once the conversation→task linkage exists.

### Milestone 8 — not yet designed

No Milestone 8 scope has been defined. Candidates carried over (not designed, not started):
- Additional application adapters (browser, GUI, research tools)
- Browser and GUI control as fallback automation
- Secure remote connectivity beyond LAN (would require mandatory auth/TLS/rate limiting per Milestone 7 Phase 3's stop conditions)
- Separate `permissions` table (stop conflating with `questions`)
- Event/task table rotation to prevent unbounded DB growth
- Question timeout (auto-cancel unanswered questions)
- Full authentication/TLS by default (still open since M1 — `JARVIS_API_TOKEN` exists as an opt-in gate for push endpoints only)
- LLM-based importance/interruption scoring, if the deterministic AttentionPolicy proves insufficient in real use

## Session Handoff

### What Works Now

- **WebSocket phone interface**: Real-time chat with event timeline
- **Task management**: Start, monitor, cancel subprocesses with stdout/stderr streaming
- **Question protocol**: Structured `JARVIS_QUESTION:` protocol for worker-to-user questions
- **Answer delivery**: Stdin write answers back to the same subprocess
- **Permission UI**: Approve/deny buttons for OpenCode permission requests
- **OpenCode integration**: Full supervision loop — session creation, prompt sending, question/permission handling, cancellation, SSE event streaming, server lifecycle management
- **Conversational supervisor (M5)**: Free-text WebSocket messages route through an LLM tool-call loop (fast path for deterministic status questions) that can start OpenCode tasks by safe project alias, relay follow-ups to an existing session, answer questions, resolve permissions, cancel tasks, and report status/attention/recent activity — validated end-to-end against a real OpenCode server and a real free-tier OpenRouter model (7/7 scenarios)
- **Verified OpenCode task lifecycle (M6)**: every state transition requires specific native evidence (SSE `session.error`/`session.idle`/activity events), never inferred from silence; a new `degraded` state replaces false failure claims after a Jarvis restart
- **Correct OpenCode server ownership/shutdown (M6)**: port-resolved PID identification + graceful→forced `taskkill /T` escalation; verified with 3+ real cycles, zero orphans left behind
- **Stale/incompatible OpenCode server diagnostics (M6)**: classifies anything already on the configured port before acting; never silently attaches to or kills an unproven process
- **Conversation continuity (M6)**: stable `conversation_id` across WebSocket turns and reconnects, `localStorage`-persisted client-side, server-validated (malformed/hostile IDs rejected)
- **Coherent attention surface (M6)**: one deduplicated list (questions + permissions), no more phantom "OpenCode task(s) waiting" section
- **Reconnect restoration**: History, running tasks, pending questions survive WebSocket disconnect — and, as of M6, no longer duplicates events that were also seen live
- **Server restart reconciliation**: Local tasks marked interrupted (failed); OpenCode tasks marked `degraded` (unknown, not falsely failed) and reconciliation is attempted
- **Isolated OpenCode storage (M6.1)**: Jarvis-owned server runs against `%LOCALAPPDATA%\JarvisOpenCodeRuntime` (or `JARVIS_OPENCODE_RUNTIME_DIR`), never Desktop's shared storage — verified via real `opencode debug paths` behavior, never officially documented but experimentally confirmed deterministic and repeatable
- **Real, proven OpenCode task execution (M6.1)**: the Milestone 6 blocker is resolved — `tests/m6_execution_probe.py` passes for real, twice, with byte-verified Desktop-database non-mutation
- **All 252 tests pass**: see Testing Status above for the full breakdown
- **Browser smoke tests**: 19/19 PASS (M3-level) + 6/6 Playwright DOM scenarios PASS (M6-level) + **31/31 Playwright DOM scenarios PASS (M7/7.1-level, voice/TTS/notifications/deep-links/reconnect/layout/per-question-mic)** — see Testing Status
- **Real OpenCode integration**: Verified against v1.15.10 for session/prompt/abort lifecycle and real execution (file creation, exact content match, verified terminal state) — re-verified unregressed after both Milestone 7's and Milestone 7.1's changes.
- **Voice input (M7, hardened M7.1)**: browser SpeechRecognition, same WebSocket pipeline as typed text, capability-detected with a typed fallback, no raw audio persistence; real-device-tested, natural phrasing (not just literal "Answer B") now resolves pending questions deterministically; a dedicated per-question mic button answers a specific question directly
- **Spoken responses (M7)**: browser speechSynthesis, off by default, concise/filtered (only direct supervisor replies and policy-gated notifications), interruptible, no overlap, no reconnect-replay speech; real-device-tested, "decent, not bad" per direct user feedback
- **Persisted notifications with DB-enforced dedup (M7)**: `notifications` table, `dedup_key` UNIQUE — one verified event produces exactly one logical notification regardless of replay/reconnect/restart/retry; real-device-confirmed exactly-once delivery
- **Deterministic AttentionPolicy (M7)**: `app/attention_policy.py`, no LLM scoring — question/permission/failure always notify, completion is configurable (default on), routine progress never does
- **Web Push infrastructure (M7, real-device-tested M7.1)**: VAPID-based, subscribe/unsubscribe endpoints, service worker push+click handlers, graceful no-op when unconfigured. Server-side pipeline confirmed genuinely correct on a real device (FCM accepts every attempt); real background/Doze-idle delivery does not reliably reach the tested device despite every standard mitigation — real, disclosed limitation, see Known Limitation #35. Foreground/recently-active delivery works.
- **PWA support (M7, real-device-tested M7.1)**: manifest, service worker (app-shell caching); real install, standalone launch, and service-worker registration all confirmed on a real Samsung Galaxy S24 FE
- **Deep linking (M7, real-device-confirmed M7.1)**: `?conversation=`/`?task=`/`?question=`/`?permission=`/`?notification=`, stale/resolved items correctly reported instead of showing stale controls; real system-tray notification tap confirmed opening the correct question
- **Mobile layout resilient to short/constrained viewports (M7.1)**: `#input-area` (mic + text box) always stays fully visible even when `#needs-attention`/`#active-tasks` have real content — was a real, severe bug on the tested device until fixed

### What Was Most Recently Verified

- Full `pytest tests/` suite — **252/252 PASSED, 0 failed, 0 skipped, 6 warnings, 371.38s** (2026-07-10, Milestone 7.1)
- Real deterministic execution-proof (`tests/m6_execution_probe.py`) re-run **after** all Milestone 7.1 code changes — **PASS** (after one confirmed timing-flake false-negative under heavy concurrent load), confirming no regression of Milestone 6.1's isolated-storage architecture
- Real Playwright DOM validation, 31 scenarios (A–H, M7/7.1) — all **PASSED**, 0 console errors, 0 failed network requests
- **Real phone acceptance, Samsung Galaxy S24 FE (Chrome, installed PWA)**: all 22 checklist steps PASS — typed chat, voice input (main + per-question mic), TTS, PWA install, foreground/recently-active notifications, tap-to-open deep linking, voice-answer resume, exactly-once completion notification. Six real bugs found and fixed (layout, voice-answer routing, toggle visibility, subscription-state reflection, `pywebpush` environment, `.env` corruption) — see Milestone 7.1.
- **Not verified / real, disclosed, unresolved**: background (Doze-idle) push delivery on the tested device — see Known Limitation #35. iOS Safari untested — see Known Limitation #30.

### What Should Be Built Next

See "Immediate priorities before any new milestone work" under Next Planned Work above — testing on iOS and deciding whether the background-push gap (Known Limitation #35) warrants further investigation are the highest-value next steps before Milestone 8 scoping.

Carried-over cleanup items (still open):
1. **Separate permissions table**: Stop conflating permissions in the `questions` table.
2. **Event/task table rotation**: Prevent unbounded database growth (now three growing tables from M7: `notifications`, `push_subscriptions`, plus the pre-existing set).
3. **Question timeout**: Auto-cancel unanswered questions after a configurable timeout.
4. **No authentication/TLS by default**: still open since M1 — `JARVIS_API_TOKEN` exists as an opt-in gate for push endpoints only (M7).

### Architecture Decisions That Must Not Be Accidentally Reversed

1. **REST + SSE for OpenCode**: Do not replace with terminal scraping, PTY control, or `opencode run --format json`. The decision matrix (47/50 vs alternatives) is documented above.
2. **Structured APIs over terminal scraping**: This is a core architectural principle. Do not introduce terminal output parsing as a primary integration method without strong evidence that no structured API exists.
3. **Questions and permissions as distinct concepts**: They use different API endpoints and different UI rendering. Do not merge them unless the upstream API changes.
4. **One server, many sessions**: One shared `opencode serve` instance manages all sessions. Do not start per-task server processes.
5. **Jarvis as supervisor, not primary worker**: Jarvis delegates to workers/agents. The M5 `Supervisor` **routes to tools**, it does not itself execute file/shell work — that remains OpenCode's job. Do not build direct work-execution into Jarvis itself.
6. **`task_id`/`question_id` must never be truncated in any LLM- or tool-facing text**: they are exact-match DB lookup keys, not display-only values (Bug 2, 2026-07-09 M5 closure). Only free-text/instruction fields and the purely-cosmetic `session_id` may be truncated for display.
7. **Free-only LLM guard (`JARVIS_LLM_FREE_ONLY`)**: do not bypass or weaken `validate_free_only_model()` — it is the safety rail preventing accidental paid-model usage.
8. **OpenCode server termination must be by port-resolved PID, never by process name** (M6): `taskkill`-by-name or any broad-matching kill would risk terminating OpenCode Desktop or an unrelated CLI session. Always resolve identity from `netstat`/`Win32_Process` first.
9. **Never attach to or kill an unproven OpenCode server** (M6): `classify_existing_server()`'s fail-closed behavior for `UNRESPONSIVE`/`UNKNOWN`/`AUTH_MISMATCH`/`INCOMPATIBLE` must be preserved — reporting a diagnostic error is always correct over guessing.
10. **Do not modify the OpenCode installation, its config, or its database.** No longer needed to work around execution issues (resolved via storage isolation, M6.1) — but the boundary itself remains load-bearing regardless.
11. **Jarvis-owned OpenCode servers must use isolated storage, never Desktop's shared paths** (M6.1): `OpenCodeServerManager.start()`'s isolation env vars (`isolated_env_overrides()`) must always be applied on the owned-spawn path. Do not revert to running against `~/.local/share/opencode`/`~/.config/opencode` directly.
12. **Never invoke the real `opencode.exe` binary against the shared/default path for diagnostic purposes** (M6.1): a `debug paths`-only invocation coincided with real data loss in the shared Desktop database during this task (see Known Limitation #26). Any future inspection of OpenCode's CLI behavior must use full isolation (all 4 `XDG_*` vars set together) or must not run the real binary at all (docs/source reading only).
13. **Notifications must only ever be created from verified/persisted state, never from LLM prose** (M7): `app/notifications.py::notify()` is the single entry point every producer must call; do not add a code path that creates a notification directly from an LLM's freeform response text.
14. **Notification idempotency depends on the `dedup_key` UNIQUE constraint, not caller discipline** (M7): do not "optimize" `create_notification()` into a plain `INSERT` — the `INSERT OR IGNORE` + re-select pattern is what makes Phase 14 deduplication hold under replay/reconnect/restart.
15. **Voice input must stay on the exact same code path as typed text** (M7): do not add voice-specific supervisor logic, a separate voice conversation store, or bypass `_resolve_deterministic_command`'s ambiguity protections for voice transcripts specifically.
16. **Never persist raw microphone audio by default** (M7): only the browser's recognized text ever reaches the server.
17. **`#needs-attention`/`#active-tasks` must remain shrinkable (`flex-shrink` > 0, `min-height: 0`), `#input-area` must remain `flex-shrink: 0`** (M7.1): reverting the attention/task panels back to `flex-shrink: 0` reproduces a real, severe bug found on a real device — the mic button and text box get pushed off the bottom of the screen entirely whenever a question or task panel has enough content, with no way to answer at all until the app is force-closed and reopened.
18. **Do not refactor `startVoiceInput()`/`submitTranscript()` to serve the per-question mic too** (M7.1): `startVoiceAnswerForQuestion()` is deliberately a separate, small, self-contained function reusing only the shared `recognition`/`voiceState` singleton — the main-mic flow is extensively tested and real-device-validated; reshaping it to serve two different callers is not worth the regression risk for the amount of code saved.
19. **Real Web Push background delivery on Android is not guaranteed by TTL/Urgency headers alone** (M7.1): both were applied and verified correct, but a real device (Samsung Galaxy S24 FE) still did not reliably display a backgrounded push despite them, plus battery-unrestricted and non-sleeping-app confirmation. Do not assume adding these headers "fixes" background push — they are necessary best practice, not sufficient on every device.

### Files a New Agent Should Inspect First

1. `app/main.py` — Entry point, lifespan startup/shutdown, WebSocket routing (slash commands → `Executor`, free text → `Supervisor`), `conversation_id` handshake
2. `app/supervisor/supervisor.py` — Conversational supervisor orchestrator, fast paths, tool-call loop
3. `app/supervisor/tools.py` — Bounded tool registry the LLM can call
4. `app/integrations/opencode_supervisor.py` — OpenCode orchestration, verified lifecycle handlers (`_handle_session_failed`/`_handle_session_idle`/`_handle_activity`), reconciliation
5. `app/integrations/opencode_events.py` — SSE event normalization against the **real** verified schema (`process_sse_event`)
6. `app/integrations/opencode_server.py` + `app/integrations/process_utils.py` — Server ownership, ownership-marker file, port-resolved termination, stale/incompatible classification, **storage isolation** (`resolve_runtime_dir`/`isolated_env_overrides`/`ensure_isolated_runtime_provisioned`)
7. `app/database.py` — Schema, CRUD, startup reconciliation, `conversation_id` validation/minting
8. `app/task_manager.py` — Core local-task lifecycle, question detection, answer delivery
9. `app/static/app.js` — Browser UI: WS handshake, timeline dedup, attention panel, voice input, TTS, notifications, deep links (M7)
10. `app/attention_policy.py`, `app/notifications.py`, `app/push.py` — M7 notification classification/creation/delivery
11. `tests/test_opencode_lifecycle.py`, `tests/test_process_ownership.py`, `tests/test_conversation_continuity.py`, `tests/test_opencode_isolation.py`, `tests/test_notifications.py`, `tests/test_attention_policy.py`, `tests/test_voice_fast_paths.py`, `tests/test_notification_integration.py`, `tests/test_notification_api.py`, `tests/test_push.py` — M6/6.1/7/7.1 new test files
12. `tests/m6_execution_probe.py` — Gated real-execution proof (not part of `pytest tests/`) — **passes for real, re-verified unregressed after M7.1**
13. `tests/m7_browser_validate.py` — Gated real-browser DOM validation (not part of `pytest tests/`) — 31/31 PASS (includes Scenario G layout regression guard and Scenario H per-question-mic guard, both from real-phone findings)
14. `app/static/style.css` — Real-phone-tested mobile layout; see Architecture Decisions #17 before touching `#needs-attention`/`#active-tasks`/`#input-area` flex rules
15. `SESSION.md` (this file) — Project memory

### Tests to Run Before Making Changes

```powershell
# Full automated suite (252 total)
pytest tests/ -v --tb=short

# Specific test files
pytest tests/test_tasks.py -v                  # Milestone 2 (8 tests)
pytest tests/test_questions.py -v              # Milestone 3 (16 tests)
pytest tests/test_opencode.py -v               # Milestone 4/5/6 (21 tests)
pytest tests/test_supervisor.py -v             # Milestone 5/6 (72 tests)
pytest tests/test_env.py -v                    # Milestone 5 .env/dotenv (6 tests)
pytest tests/test_process_ownership.py -v      # Milestone 6 (15 tests)
pytest tests/test_opencode_lifecycle.py -v     # Milestone 6 (15 tests)
pytest tests/test_conversation_continuity.py -v # Milestone 6 (6 tests)
pytest tests/test_opencode_isolation.py -v     # Milestone 6.1 (20 tests)
pytest tests/test_notifications.py -v          # Milestone 7 (15 tests)
pytest tests/test_attention_policy.py -v       # Milestone 7 (13 tests)
pytest tests/test_voice_fast_paths.py -v       # Milestone 7 (15 tests)
pytest tests/test_notification_integration.py -v # Milestone 7 (8 tests)
pytest tests/test_notification_api.py -v       # Milestone 7 (13 tests)
pytest tests/test_push.py -v                   # Milestone 7.1 (3 tests — TTL/Urgency real-phone fix)

# Browser smoke test (requires Playwright Python) — M3-level only
python tests/browser_smoke.py

# Milestone 7/7.1 browser DOM validation (requires Playwright Python; starts
# its own server with JARVIS_TEST_MODE=1; mocks SpeechRecognition/
# speechSynthesis via page.add_init_script — see Milestone 7 Phase 17 for why
# a bare arrow function there silently does nothing, must be an IIFE.
# Scenario G/H are real-phone-finding regression guards — see Milestone 7.1)
python tests/m7_browser_validate.py

# Real end-to-end M5 validation (requires opencode.exe + a configured .env with
# JARVIS_LLM_API_KEY; starts its own Jarvis + opencode serve if none is running;
# takes several minutes against a free-tier model — do not assume a stalled-looking
# log means a hang, cross-check with a direct HTTP/WS probe before killing it)
python tests/m5_validate2.py

# Gated real-execution proof (now passes for real, post-Milestone-6.1 — uses
# isolated storage automatically via the production OpenCodeServerManager
# code path). Uses its own temp DB for Jarvis's own bookkeeping; safe sandbox
# only for the actual OpenCode task.
python tests/m6_execution_probe.py

# Playwright DOM validation of the browser UI (manual, ad hoc — no fixed
# script committed; drive playwright-cli against a running `uvicorn
# app.main:app` per Milestone 6 Phase 11 for the scenario list)

# After ANY real-server test run: verify no orphaned opencode.exe remains
# (netstat -ano | findstr :4097) — should now self-clean via M6's ownership
# fix, but if you force-kill Jarvis itself (taskkill /F) rather than letting
# it shut down gracefully, its own cleanup hook never runs and you must
# clean up the child manually, same as any application.

# NEVER run the real opencode.exe binary directly against its default/shared
# paths (no XDG_* vars set) for inspection purposes — see Safety Constraints.
# Use isolation (all 4 XDG_* vars together) or don't run the real binary.
```

### Safety Constraints

1. **Never store API keys, tokens, passwords, cookies, or other secrets** in source code or SESSION.md.
2. **Never run arbitrary shell commands** from user input. Commands are predefined in `executor.py`.
3. **Do not expose the server to the internet** without authentication and TLS.
4. **Do not modify `opencode.json`** without understanding its purpose (Playwright CLI configuration).
5. **Do not modify the OpenCode database** directly. Use the REST API.
6. **All new API integration must prefer structured interfaces** (REST, JSON-RPC, SSE) over terminal scraping.
7. **Do not terminate any process by name-matching** — OpenCode server termination must resolve a specific PID from the port it's listening on first (M6).
8. **Do not modify the OpenCode installation/config/database.**
9. **Never invoke the real `opencode.exe` binary against its default/shared paths** (M6.1) — always use full isolation (all 4 `XDG_*` variables set together) for any real invocation, including "harmless-looking" introspection commands like `debug paths`. One such invocation, with only some variables set, coincided with real data loss in OpenCode Desktop's shared database during this project — see Known Limitation #26.
10. **Provider credentials for the isolated OpenCode runtime are sourced only from environment variables** (`JARVIS_OPENCODE_OPENROUTER_KEY` or `JARVIS_LLM_API_KEY`) **at provisioning time** — never copied from OpenCode Desktop's `auth.json`, never hardcoded, never logged (M6.1).
11. **Never persist raw microphone audio** (M7) — only the browser's recognized text (already-transcribed by the Web Speech API) ever reaches the server; do not add a MediaRecorder-based audio-upload path without re-evaluating this constraint deliberately.
12. **Never log VAPID private keys, push subscription auth secrets, or full authorization headers** (M7) — `app/push.py` and `scripts/generate_vapid_keys.py` are written to avoid this structurally; preserve that when modifying them.
13. **Notification and push payload bodies must stay generic** (M7 Phase 15) — never include raw filesystem paths, task stdout, or full instruction text in a notification `title`/`body`; that detail belongs only inside the authenticated app surface (the existing `task_permission`/`task_question` WebSocket messages), not in something that could appear on a lock screen.
14. **`cert.pem`/`key.pem` (local dev HTTPS) must never be committed** — already `.gitignore`d (M7); do not remove that entry.

## Maintenance Rules for SESSION.md

1. Append a timestamped entry after every meaningful implementation milestone.
2. Record architecture decisions and why they were made.
3. Record test counts and exact pass/fail results.
4. Record bugs discovered during manual or browser testing.
5. Mark bugs as resolved rather than deleting historical evidence.
6. Separate verified facts from assumptions and plans.
7. Never store API keys, tokens, passwords, cookies, or other secrets.
8. Never claim a feature is implemented merely because it is planned.
9. Update the Current Milestone and Session Handoff sections whenever work materially advances.
10. Use absolute timestamps when available.
11. If exact time is unknown, use date only rather than inventing a time.
12. Preserve important failed approaches and the reason they were rejected.
