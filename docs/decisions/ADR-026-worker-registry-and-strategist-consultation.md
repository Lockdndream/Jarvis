# ADR-026: Worker Registry and Strategist Consultation

## Status

Accepted

## Date

2026-07-30 (Month 1, Week 2 — post-F1 roadmap, worker registry)

## Context

Before this change, the Supervisor's only way to dispatch a task to
another AI agent was a hardwired integration with a single concrete
class, `OpenCodeSupervisor` (`app/integrations/opencode_supervisor.py`),
reached via six tool handlers in `ToolRegistry`
(`app/supervisor/tools.py`) that call its public methods directly.
Adding a second kind of AI worker — in particular, a "strategist" the
Supervisor can consult for planning/review advice, filling the role
originally envisioned for a direct Claude API consultation (no API key
is available for that) — meant either bolting a second hardwired
integration onto `ToolRegistry` or introducing a small abstraction that
both the existing OpenCode path and the new strategist path can sit
behind.

Investigation before this task (Step 0) mapped every real coupling to
`OpenCodeSupervisor`: `ToolRegistry`/`Supervisor` (task dispatch — the
only path this ADR's abstraction wraps), `Executor`
(`app/executor.py`, slash commands — bypasses `ToolRegistry` entirely,
untouched here), `app/operations.py` (ADR-022's lifecycle/claim
machinery — start/stop/restart of the OpenCode subprocess itself, a
different concern from task dispatch, untouched here), and `app/main.py`
's health-check endpoint (direct `.server` attribute read, untouched
here). Only the first of these — the task-dispatch path — is in scope.

## Decision

**A small `WorkerRegistry` (`app/workers/base.py`) that the Supervisor
can look up workers from by name or capability tag.** This is
deliberately *not* a plugin system: there is no discovery, no dynamic
loading, no manifest format. Workers are plain classes implementing a
three-member `Worker` interface (`name`, `capabilities: list[str]`,
async `invoke()`, async `status()`), registered explicitly by
`app/main.py` at startup:

```python
worker_registry = WorkerRegistry()
worker_registry.register(OpenCodeWorker(opencode_supervisor))
worker_registry.register(StrategistWorker())
```

Capability matching is exact-string-match on a flat list (`"planning" in
worker.capabilities`) — no ontology, no fuzzy matching, no scoring. This
is a single-user system with a handful of known workers; the matching
logic does not need to be more capable than the number of workers
justifies.

### `OpenCodeWorker` wraps, does not replace, `OpenCodeSupervisor`

Not a single line of `opencode_supervisor.py` changed. `OpenCodeWorker`
(`app/workers/opencode_worker.py`) wraps exactly one method,
`start_session()`, and only for the one tool handler that needed it
(`start_opencode_task`). The other five OpenCode tool handlers
(`send_opencode_instruction`, `get_task_result`, `answer_question`,
`resolve_permission`, `cancel_task`) still call `self._oc.*` directly,
unchanged — a generic "task description" string cannot express "answer
question Q with text T" or "cancel task T", so forcing those through a
`Worker.invoke()` contract would have distorted five working paths to
fit one interface. `ToolRegistry` builds its own `OpenCodeWorker`
internally from whatever `opencode_supervisor` it's given, independent of
whether an external `WorkerRegistry` was injected — this is what keeps
every pre-existing 3-positional-argument `ToolRegistry(...)` test
construction working unmodified; the externally injected registry is
used only to look up the strategist.

### `invoke()` returns a tagged result, never blocks on OpenCode completion

`OpenCodeSupervisor.start_session()` returns immediately with
`{task_id, session_id, name}`; the actual result arrives later via an
SSE-driven completion path (`_handle_session_idle`/`_handle_session_failed`
→ `_capture_task_result`). A naive `invoke() -> str` contract would tempt
a caller to reach for `wait_for_completion(task_id, timeout)`, which
would block the Supervisor's async tool-call loop — and therefore
`main.py`'s WebSocket receive loop — for the full duration of an OpenCode
task (minutes, not the ~30s already accepted for `stt.transcribe`). To
avoid this trap, `Worker.invoke()` returns a tagged `WorkerResult`
(`status: DISPATCHED | COMPLETED | FAILED`, plus `output`/`task_id`/
`error`/`metadata`). `OpenCodeWorker.invoke()` always returns
`DISPATCHED` immediately, mirroring `start_session()`'s own fire-and-
later-complete shape; how a dispatched task's result eventually reaches
the user is unchanged from before (SSE → DB → notification).
`StrategistWorker.invoke()`, by contrast, is genuinely synchronous — the
subprocess call returns the answer directly — so it returns `COMPLETED`
with the output in hand.

### The strategist consults an OpenCode Go model via CLI, isolated

`StrategistWorker` (`app/workers/strategist_worker.py`) shells out to
`opencode run -m <model> "<prompt>"` via
`asyncio.create_subprocess_exec` (never a blocking subprocess call, for
the same reason as above), with a bounded timeout
(`JARVIS_STRATEGIST_TIMEOUT_SECONDS`, default 120s) that terminates then
kills the process if exceeded. The default model is
`opencode-go/deepseek-v4-pro` (`JARVIS_STRATEGIST_MODEL`), overridable
per-deployment (e.g. to a Kimi K3 model, once appropriate) without a
code change.

The subprocess runs against its **own isolated OpenCode runtime
directory** (`JARVIS_STRATEGIST_RUNTIME_DIR`, a sibling of
`OpenCodeServerManager`'s own `opencode_runtime_dir()`), reusing the
existing isolation primitives in `app/integrations/opencode_server.py`
(`isolated_env_overrides`, `ensure_isolated_runtime_provisioned`,
`config.os_essential_subprocess_env`) rather than reinventing them. A
separate directory was chosen deliberately over sharing the OpenCode
server's own runtime dir: a long-running `opencode serve` process and a
one-shot `opencode run` process both touching the same SQLite-backed
data directory concurrently is an avoidable, untested risk (lock
contention, session cross-talk) — a second isolated directory costs
nothing and removes the question entirely.

### `consult_strategist` — a new Supervisor tool, with a known scope limit

`ToolRegistry` gained a `consult_strategist(question: str)` tool. It
assembles context via the existing `build_context()`
(`app/supervisor/context.py`) — the same data (projects, active tasks,
pending questions/permissions) the main Supervisor conversation already
uses — formatted by a small tools.py-local helper, then sends that plus
the question to the strategist worker.

**Known, deliberate scope limit**: this context does *not* include a
summary of the current conversation's recent turns. `ToolRegistry` has
no access to `conversation_id`/turn history — that state lives only in
`Supervisor._process_message_inner`'s local scope. Threading it through
would mean special-casing one tool's argument construction outside the
normal "the calling LLM decides the tool arguments" contract every other
tool follows — a larger design change than this task's scope, and one
made without evidence yet that the calling LLM's own self-contained
`question` text (which it is expected to write with whatever specific
detail is relevant, the same way it already writes `start_opencode_task`'s
`instruction`) is actually insufficient in practice. Documented here as
an accepted v1 boundary, not a defect; see Future Revisit Conditions.

### `Worker` is a minimum contract, not a ceiling

Injection follows the existing pattern exactly: `worker_registry` is a
4th optional keyword argument on both `Supervisor.__init__` and
`ToolRegistry.__init__`, defaulting to `None`, the same way
`connection_manager` was added as the 3rd (Milestone F1.10). Nothing
about `Worker`'s three-member interface prevents a concrete worker from
exposing more than that (e.g. `OpenCodeWorker` holds a reference to the
full `OpenCodeSupervisor` it wraps) — the interface is what the registry
and generic dispatch code need, not a restriction on what a worker class
may otherwise offer to code that already knows its concrete type.

### The ADR-013 cost gate does not (and structurally cannot) cover the strategist's model choice

`validate_opencode_model()`/`validate_free_only_model()`
(`app/integrations/opencode_adapter.py`, `app/supervisor/llm.py`) gate
OpenRouter-style model IDs (must end in `:free`, or be the one allowed
paid model) used by the OpenCode *HTTP API* task-dispatch path. The
strategist's `opencode-go/*` models are a structurally different
namespace — the `opencode` CLI's own model registry, reached via a
one-shot subprocess call, not an OpenRouter-routed HTTP request — that
this gate was never designed to validate, and that the project already
treats as free/included (these are the same models this whole project's
own subagent delegation already uses via CLI shell-out). This is a
deliberate scope boundary, not an oversight: forcing an OpenRouter-shaped
validator onto a CLI model-selector string would be incorrect, not just
unnecessary. If a future paid `opencode-go` model is introduced, cost
control for it should be a new, explicit decision — not a silent
extension of ADR-013's existing gate.

### A note on "worker" as a word

ADR-005 already named the general architectural principle this ADR
formalizes into code — "Supervisor routes, workers execute" — so
`app/workers/`'s `Worker` class is the first concrete crystallization of
that term, not a competing sense of it. Two other, unrelated uses of the
same word already existed and are unaffected: `app/worker_events.py`'s
`worker_type: "mock_worker" | "opencode"` (which producer generated an
attention event) and `app/task_manager.py`'s literal
`app/workers/mock_worker.py` (a standalone demo subprocess script,
invoked by path, never imported as a package member — it happened to
already live in the same directory this task added new files to). Worth
knowing when grepping for "worker" in this codebase; no unification is
proposed or needed.

## Alternatives Considered

**A plugin/discovery system (auto-load worker classes from a directory or
entry-point manifest).** Rejected. This is a single-user system with a
small, known, slowly-changing set of workers; a discovery mechanism
would add real complexity (manifest format, load-order concerns, error
handling for a worker that fails to import) to solve a problem — dynamic
extensibility — nobody has asked for.

**Route `consult_strategist` through the existing `OpenCodeSupervisor`
HTTP-API path instead of a separate CLI shell-out.** Rejected. The
existing path is designed around session lifecycle (SSE-driven,
eventually-completes, tied to a project directory) for coding tasks; a
planning consultation is a single request/response exchange with no
session to manage. Reusing the CLI shell-out mechanism this project
already uses for subagent delegation is simpler and matches an
already-proven pattern.

**Thread `conversation_id`/history through the tool-dispatch loop now,
to make `consult_strategist`'s context complete.** Rejected for this
pass — see the "known, deliberate scope limit" discussion above.

## Consequences

### Positive Outcomes

- Adding a future worker (e.g. Claude Code, once a plan executor exists)
  is a new file plus one registration line in `main.py` — no change to
  `ToolRegistry`, `Supervisor`, or any existing tool handler.
- All 207 pre-existing tests across the six OpenCode-related test files
  pass unmodified; the one test-file change (`tests/test_supervisor.py`)
  widened a test fake's signature by two optional kwargs to match the
  real `OpenCodeSupervisor.start_session()` signature it already had —
  zero assertion or test-logic changes.
- The Supervisor gains a second, qualitatively different kind of AI
  consultation (synchronous planning advice) alongside the existing
  asynchronous coding-task dispatch, without either path leaking into
  the other's assumptions.

### Tradeoffs

- `consult_strategist`'s advice quality is bounded by how self-contained
  the calling LLM's `question` text is — see the known scope limit above.
- Two `OpenCodeWorker` instances now exist in the running process (one
  built internally by `ToolRegistry`, one registered into the external
  `WorkerRegistry` in `main.py`) — harmless, since the class is a
  stateless wrapper, but worth knowing if debugging worker identity ever
  matters.
- The strategist's isolated runtime directory is provisioned
  independently of the OpenCode server's — a second isolated-runtime
  auth/config footprint on disk, not shared with the server's.

## Future Revisit Conditions

- Real usage shows `consult_strategist`'s advice is consistently missing
  context the calling LLM doesn't think to restate — revisit threading a
  conversation summary through, at that point with evidence of an actual
  gap rather than a speculative one.
- A plan executor is built on top of this registry (per the Month 1
  roadmap) and needs to add Claude Code as a worker — expected to be a
  new `app/workers/claude_code_worker.py` plus one `main.py` registration
  line, per this ADR's design.
- A Claude API key becomes available, making a direct-API strategist
  (the originally-envisioned role) preferable to the CLI shell-out.

## References

- `CLAUDE.md` (Supervisor routes, workers execute — ADR-005; no
  over-engineering; ADR-driven architecture rule).
- `docs/decisions/ADR-005-worker-supervisor-architecture.md` ("Supervisor
  routes, workers execute").
- `docs/decisions/ADR-013-standing-delegation-workforce-cost-policy.md`
  (the free-only/paid-model gate this ADR explicitly does not extend to
  the strategist's CLI model namespace).
- `docs/decisions/ADR-022-jarvis-operations-subsystem.md` (the
  `operations.py` lifecycle/claim concern this task deliberately left
  untouched).

## Related Milestones

Month 1, Week 2 of the post-F1 feature roadmap (worker registry),
Steps 0–5.

## Related Source Files

- `app/workers/base.py` (new — `Worker`, `WorkerRegistry`, `WorkerResult`,
  `WorkerResultStatus`, `WorkerStatus`)
- `app/workers/opencode_worker.py` (new — wraps `OpenCodeSupervisor`)
- `app/workers/strategist_worker.py` (new — CLI-based strategist)
- `app/supervisor/tools.py` (`_start_opencode_task` routed through
  `OpenCodeWorker`; new `_consult_strategist` tool)
- `app/supervisor/supervisor.py` (`worker_registry` constructor param)
- `app/main.py` (registry construction, explicit worker registration)
- `app/config.py` (`strategist_model`, `strategist_timeout_seconds`,
  `strategist_runtime_dir` accessors)
