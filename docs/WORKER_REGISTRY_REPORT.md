# Worker Registry — Final Report

Month 1, Week 2 of the post-F1 feature roadmap. Formalizes the
Supervisor's hardwired OpenCode integration into a small worker
abstraction, and adds a "strategist" worker — an OpenCode Go model
consulted for planning/review advice, filling the role originally
envisioned for a direct Claude API consultation (no API key available).

## Step 0 — current integration, mapped before any change

Mapped via my own direct reading of `app/supervisor/supervisor.py` and
`app/supervisor/tools.py` in full, plus targeted greps against
`app/integrations/opencode_supervisor.py`, `app/main.py`, and
`app/executor.py`, cross-checked against an independent read-only
delegate (deepseek-v4-flash) mapping the same integration — both agreed
on every point. Key findings:

- The Supervisor's delegate-vs-handle-directly decision is entirely an
  LLM tool-call decision (no keyword/intent classifier).
- `OpenCodeSupervisor.start_session()` returns immediately; the actual
  task result arrives later via an SSE loop
  (`_handle_session_idle`/`_handle_session_failed` → `_capture_task_result`).
  Any wrapper that turns this into a single `invoke()` call must not
  block waiting for that later event.
- Four distinct classes of consumer touch `OpenCodeSupervisor`: `ToolRegistry`/
  `Supervisor` (task dispatch — the only one in scope here), `Executor`
  (slash commands, bypasses `ToolRegistry` entirely), `app/operations.py`
  (ADR-022 lifecycle/claim machinery — a different concern from task
  dispatch), and `app/main.py`'s health-check endpoint (direct `.server`
  attribute read). Only the first was touched.
- Existing tests covering this integration:
  `tests/test_opencode.py`, `tests/test_opencode_lifecycle.py`,
  `tests/test_opencode_isolation.py`, `tests/test_opencode_operational_state.py`,
  `tests/test_supervisor.py`, `tests/test_attention_supervisor_tools.py`
  — baseline recorded before any change: **207 passed, 302.58s.**

## New files created

| File | Purpose |
|---|---|
| `app/workers/base.py` | `Worker` ABC, `WorkerRegistry`, `WorkerResult`, `WorkerResultStatus`, `WorkerStatus` |
| `app/workers/opencode_worker.py` | `OpenCodeWorker` — wraps `OpenCodeSupervisor.start_session()` only |
| `app/workers/strategist_worker.py` | `StrategistWorker` — shells out to `opencode run -m <model>` |
| `tests/test_workers_base.py` | 8 tests — registry register/lookup/duplicate-name |
| `tests/test_opencode_worker.py` | 8 tests — invoke/status against a mocked `OpenCodeSupervisor` |
| `tests/test_strategist_worker.py` | 9 tests — invoke/status against a mocked subprocess |
| `tests/test_worker_registry_wiring.py` | 7 tests — constructor injection, `consult_strategist` |
| `docs/decisions/ADR-026-worker-registry-and-strategist-consultation.md` | this task's ADR |

## Existing files changed

| File | Change |
|---|---|
| `app/supervisor/tools.py` | `worker_registry` 4th optional param; internal `_oc_worker`; `_start_opencode_task` routed through `OpenCodeWorker.invoke()` (identical return strings); new `_consult_strategist` tool + `_format_context_for_strategist` helper |
| `app/supervisor/supervisor.py` | `worker_registry` 4th optional param, passed through to `ToolRegistry` |
| `app/main.py` | Constructs `WorkerRegistry()`, registers `OpenCodeWorker`/`StrategistWorker` explicitly, passes registry into `Supervisor(...)` |
| `app/config.py` | `strategist_model()`, `strategist_timeout_seconds()`, `strategist_runtime_dir()` accessors |
| `.env.example` | Three new `JARVIS_STRATEGIST_*` entries documented |
| `tests/test_supervisor.py` | **1 line**: `_FakeOpenCodeSupervisor.start_session()` widened to accept `provider_id=None, model_id=None` — matches the real `OpenCodeSupervisor.start_session()` signature (which already had these) that `OpenCodeWorker.invoke()` now calls explicitly. Verified via `git diff`: zero assertion or test-logic changes, purely a stale test-double signature fix. |
| `docs/decisions/README.md` | ADR-026 index row |

## Worker abstraction design

```python
class WorkerResultStatus(str, Enum):
    COMPLETED = "completed"   # synchronous worker, result is ready now
    DISPATCHED = "dispatched" # async worker, work is running elsewhere
    FAILED = "failed"

class WorkerStatus(str, Enum):
    AVAILABLE = "available"; BUSY = "busy"; UNAVAILABLE = "unavailable"

@dataclass
class WorkerResult:
    status: WorkerResultStatus
    output: str | None = None
    task_id: str | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

class Worker(ABC):
    name: str
    capabilities: list[str]
    async def invoke(self, task_description: str, **kwargs) -> WorkerResult: ...
    async def status(self) -> WorkerStatus: ...

class WorkerRegistry:
    def register(self, worker: Worker) -> None: ...       # raises on duplicate name
    def get_by_name(self, name: str) -> Worker | None: ...
    def get_by_capability(self, capability: str) -> list[Worker]: ...
    def list_workers(self) -> list[Worker]: ...
```

Not a plugin system — no discovery, no dynamic loading. Workers are
registered explicitly by `app/main.py` at startup. Capability matching is
exact-string-match on a flat list; no ontology, no fuzzy matching.

The `WorkerResult` tagging exists specifically to prevent a trap:
`OpenCodeSupervisor.start_session()` returns immediately while the real
result arrives minutes later via SSE. A naive `invoke() -> str` contract
would tempt a caller to block on `wait_for_completion()`, freezing the
Supervisor's async tool-call loop (and therefore the phone's WebSocket
connection) for the task's full duration. `OpenCodeWorker.invoke()`
always returns `DISPATCHED` immediately; `StrategistWorker.invoke()`,
genuinely synchronous, returns `COMPLETED` with the answer in hand.

`OpenCodeWorker` wraps exactly one method — `start_session()` — for
exactly one tool (`start_opencode_task`). The other five OpenCode tool
handlers (`send_opencode_instruction`, `get_task_result`,
`answer_question`, `resolve_permission`, `cancel_task`) still call
`self._oc.*` directly, unchanged — a generic "task description" string
can't express "answer question Q with text T."

## Strategist worker implementation

Shells out to `opencode run -m <model> "<prompt>"` via
`asyncio.create_subprocess_exec` (never blocking), with a bounded
timeout (`JARVIS_STRATEGIST_TIMEOUT_SECONDS`, default 120s) that
terminates then kills the process if exceeded. Default model
`opencode-go/deepseek-v4-pro` (`JARVIS_STRATEGIST_MODEL`, overridable).
Runs against its own isolated OpenCode runtime directory
(`JARVIS_STRATEGIST_RUNTIME_DIR`, a sibling of the OpenCode server's own
runtime dir — kept separate to avoid two processes touching the same
SQLite-backed storage concurrently), reusing the existing isolation
primitives in `app/integrations/opencode_server.py`
(`isolated_env_overrides`, `ensure_isolated_runtime_provisioned`,
`config.os_essential_subprocess_env`) rather than reinventing them.
stderr is truncated to 2000 chars on failure so a runaway CLI output
can't balloon a `WorkerResult`.

## Supervisor wiring — what changed, what didn't

`ToolRegistry` builds its own internal `OpenCodeWorker` from whatever
`opencode_supervisor` it's given, **independent of** whether an external
`WorkerRegistry` was injected — this is what keeps every pre-existing
3-positional-argument `ToolRegistry(...)` test construction working
unmodified. The externally injected registry is used *only* by the new
`consult_strategist` tool. `Supervisor.__init__` gained the same 4th
optional `worker_registry` param, passed through. `main.py` constructs
the registry and registers both workers explicitly before constructing
`Supervisor`.

**What didn't change**: `OpenCodeSupervisor` itself (zero diff, verified
every step), the five untouched OpenCode tool handlers, `Executor`,
`app/operations.py`.

## ADR-026 summary

Documents the registry-not-plugin-system decision, the tagged-result
design and why, the strategist's isolated-runtime and CLI-shell-out
choices, and two scope boundaries recorded as deliberate rather than
gaps: (1) the ADR-013 free/paid-model cost gate does not extend to the
strategist's `opencode-go/*` models — a structurally different namespace
(CLI model registry, not OpenRouter-routed HTTP) that the gate was never
designed to validate; (2) `consult_strategist`'s context has no
conversation-history summary, since `ToolRegistry` has no access to
`conversation_id` — accepted as a v1 boundary pending evidence it's
actually insufficient in practice, rather than built speculatively now.
Full text: `docs/decisions/ADR-026-worker-registry-and-strategist-consultation.md`.

## Test output

Baseline (recorded before Step 1): **207 passed, 302.58s** across the six
OpenCode-related test files.

Final full suite, run independently by me (not the delegates'
self-reports) after all three implementation steps:

```
729 passed, 2 warnings in 902.62s (0:15:02)
```

This reconciles exactly: 690 (the STT-work baseline, immediately prior to
this task) + 39 new tests (16 in Step 1, 16 in Step 2, 7 in Step 3) = 729.
Zero regressions, zero unaccounted-for test count changes. The two
warnings are pre-existing (a `websockets.legacy` deprecation notice) plus
one Windows `ProactorEventLoop` unclosed-transport `ResourceWarning`
firing at interpreter teardown after the last test collected — not
attached to any specific test, not a failure, not something introduced by
this work.

`ruff check .` (whole repo): **clean, 0 issues.**
`mypy app/` (whole app): **clean, 0 errors** (only pre-existing
`annotation-unchecked` informational notes, unrelated to this work).

Every delegated step was independently re-verified by me before
acceptance: full diffs read line-by-line, `git status`/`git diff`
checked to confirm the claimed scope of changes was the actual scope,
all tests re-run myself (not trusted from self-reports), ruff/mypy
re-run myself.

## Strategist smoke test (real, not mocked)

Ran `StrategistWorker().invoke(...)` for real against the live `opencode`
CLI. **The subprocess integration mechanics are confirmed correct**: a
fresh isolated runtime directory was created at
`%LOCALAPPDATA%\JarvisOpenCodeRuntime_strategist` (first-ever invocation
there triggered OpenCode's one-time DB migration — direct proof of
genuine isolation from any other runtime, including the OpenCode
server's own), `status()` correctly reported `AVAILABLE`, the real
subprocess was spawned with the correct arguments, and on failure the
`FAILED` `WorkerResult` correctly captured the real CLI's actual stderr
verbatim.

**The specific call failed** with: `Model not found:
opencode-go/deepseek-v4-pro. Did you mean: deepseek-v4-pro?` Root cause
identified, not left as a mystery: this session's own successful
`opencode-go/*` delegations (used throughout this task for Steps 1–3)
ran against a separate scratchpad XDG directory whose `auth.json` has a
dedicated `opencode-go` provider credential, provisioned at some earlier
point outside this task. `ensure_isolated_runtime_provisioned()` (reused
correctly from `opencode_server.py`) only provisions an `openrouter`
auth entry — it has no `opencode-go` provisioning logic, since it was
originally written for `OpenCodeSupervisor`'s own OpenRouter-based path.
A freshly created, deliberately separate strategist runtime dir therefore
has no `opencode-go` credential, and the default model can't resolve
there. Confirmed expected: no `auth.json` was written at all in the
strategist's runtime dir (no `JARVIS_OPENCODE_OPENROUTER_KEY`/
`JARVIS_LLM_API_KEY` set in this environment either).

**Resolved** (option c, per your direction): added `config.opencode_go_key()`
(`JARVIS_OPENCODE_GO_KEY`, same `_str()` call-time-read pattern as
`opencode_openrouter_key()`), extended `ensure_isolated_runtime_provisioned()`
to write an `"opencode-go": {"type": "api", "key": ...}` entry into
`auth.json` alongside the existing `openrouter` entry whenever the key
is present — both conditionally, both idempotent-only-if-`auth.json`-
missing, matching the existing pattern exactly. Documented in
`.env.example`. Added 2 new tests
(`test_provisioning_writes_opencode_go_key_when_present`,
`test_provisioning_writes_both_keys_when_both_present`) plus hardened
the pre-existing `test_provisioning_without_any_key_does_not_write_auth`
to also delete the new env var, so it can't silently pass by ambient
accident. All 24 tests in `tests/test_opencode_isolation.py` pass (22
pre-existing + 2 new); ruff/mypy clean on both changed files.

**Re-ran the smoke test with a real `opencode-go` key provisioned**:
succeeded end-to-end. `status()` → `AVAILABLE`; `invoke()` → `COMPLETED`
with a real model response ("A worker registry pattern centralizes the
discovery and routing of tasks to the appropriate handler by maintaining
a named mapping of worker types."). Confirmed the isolated `auth.json`
now contains exactly `{"opencode-go": ...}` (no `openrouter` entry, since
no OpenRouter key was set in this environment) — provisioning wrote
precisely what was configured, nothing more. The worker registry's
strategist path is now fully verified working end-to-end against the
real CLI, not just mocked.

## Issues, judgment calls, and corrections to this plan

1. **`ToolRegistry` builds its own internal `OpenCodeWorker`, separate from
   the externally injected registry** — a design decision made before
   delegating Step 3, not left to a delegate to guess. Without it, every
   pre-existing 3-positional-argument `ToolRegistry(...)` test
   construction would have broken (no injected registry → no OpenCode
   worker found → `start_opencode_task` fails). This is the single
   biggest reason the "existing tests pass unmodified" constraint held.
2. **A circular-import constraint found and resolved before writing the
   Step 3 brief**: `supervisor.py` imports `ToolRegistry` from `tools.py`,
   so `tools.py` cannot import `_format_context` from `supervisor.py`.
   Resolved by having `consult_strategist` reuse `build_context()` (no
   cycle) and write its own small local formatter, rather than reusing
   `_format_context` or moving it.
3. **The one existing-test-file edit** (`tests/test_supervisor.py`'s fake
   widened by two optional kwargs) was scrutinized specifically because
   the brief's hard constraint said not to edit tests to make them pass.
   Verified via `git diff` this is a 2-line signature widening with zero
   assertion changes, justified because the fake was already out of sync
   with the real `OpenCodeSupervisor.start_session()` signature (which
   already had `provider_id`/`model_id` before this task started) —
   judged legitimate, not a masked regression.
4. **The strategist's model-credential gap** (above) — a genuine, honest
   finding from the real smoke test, deliberately not patched
   unilaterally.
5. **`consult_strategist`'s missing conversation-history context** — the
   original task spec wanted a "recent conversation summary" in the
   strategist's context; delivered adequate-but-thinner (project/task/
   question state only) for a structural reason (`ToolRegistry` has no
   `conversation_id` access), documented as an accepted v1 boundary in
   ADR-026 rather than solved by threading history through the
   tool-dispatch loop's LLM-decides-the-arguments contract.

## Did the Opus checkpoints change anything?

Yes, at both gates:

- **Checkpoint #1** (after Step 0, before designing the abstraction):
  changed the `Worker` interface itself. The originally-planned
  `invoke() -> str` contract would have tempted a caller to block on
  `wait_for_completion()`, freezing the Supervisor's WebSocket loop for
  minutes. Replaced with the tagged `WorkerResult` design before any
  code was written. Also surfaced that `invoke()` should cover only the
  `start_opencode_task` path, not force the other five OpenCode tool
  handlers through a `Worker` shape that doesn't fit them.
- **Checkpoint #2** (after Step 3 wiring): confirmed, rather than
  assumed, that the registry wraps OpenCode without changing behavior
  (207 baseline tests + zero `OpenCodeSupervisor` diff), confirmed no
  circular-import issues (verified both by reading every import and by
  `python -c "import app.main"` succeeding), and produced the explicit,
  reasoned decision to *not* thread conversation history through for
  `consult_strategist` right now — turning a vague "is the context
  adequate?" question into a concrete, documented scope boundary in
  ADR-026 rather than an unresolved ambiguity.

(Advisor was unavailable for Checkpoint #2's live call — reasoned
through directly with the same rigor, per the pattern already
established during the STT work.)
