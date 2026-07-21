# Jarvis Control Center — Observer Protocol & Contracts — v1

This is the durable contract for the Control Center's observer channel:
what a dashboard client sends and receives, what every contributor —
human or AI — must preserve when touching any file this document
references, and what is safe to depend on versus what is still
internal detail. It complements `docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`
(which explains *why*) and `docs/protocols/websocket-protocol-v1.md`
(the base protocol this one extends, not replaces).

**v1** covers the protocol as implemented and hardened in Milestone
9B.10. There is no v0; this is the first time this channel has existed.

---

## 1. Connecting as an observer

An observer is a normal `/ws` client (see `websocket-protocol-v1.md`
§1 for connecting and authentication — identical, no exceptions) that
sends one additional message after connecting:

```json
{"type": "register_observer"}
```

There is no unregister message. A connection stops being an observer
only by disconnecting — `ConnectionManager.disconnect()` removes it
from the observer set along with everything else it cleans up.

A connection that never sends `register_observer` behaves exactly as
it always has: it can send `voice_session_*`/`user_message`/
`device_status` and receive ordinary replies and general broadcasts,
and it will never receive an observer-only event no matter how long
it stays connected. A connection that sends `register_observer` can
still send every other message type too — becoming an observer adds a
capability, it does not remove any existing one.

### 1.1 Bootstrap: `GET /api/dashboard/snapshot`

Before or immediately after connecting, a dashboard fetches this
endpoint once to get a starting state (recent tasks, recent events,
pending questions/notifications/attention, OpenCode status,
connection stats) — see §5 for exactly what fields it returns and why
that list is closed, not open. Requires the same
`Authorization: Bearer <JARVIS_API_TOKEN>` header every other
authenticated endpoint requires (§4).

## 2. Messages an observer receives

All of the following are sent *only* to connections that have called
`register_observer` — never to any other connection, regardless of
how the general `broadcast()` path evolves in the future (Contract 1).

| Event type | Source | Meaning |
|---|---|---|
| `supervisor_turn_started` | `app/supervisor/supervisor.py` | A `process_message()` call began. |
| `supervisor_tool_call` | `app/supervisor/supervisor.py` | A tool was invoked mid-turn: name, arguments, truncated result. Never the LLM's reasoning text — there is no such field to send in the first place (Contract 7). |
| `supervisor_turn` | `app/supervisor/supervisor.py` | A turn completed: final response text, conversation id. |
| `voice_session_lifecycle` | `app/voice_session_manager.py` | A `VoiceSession` state transition (opening/listening/processing/waiting/closing/closed). |
| `device_status_update` | `app/main.py` | A client's `device_status` message, relayed for observers only. |

Everything already broadcast generally (task/attention/notification
events, OpenCode task lifecycle events) is *not* duplicated onto the
observer channel — the dashboard's snapshot endpoint and its own
5-second poll are how it learns about those; only the five event
types above are observer-exclusive.

### 2.1 Messages an observer sends (besides the base protocol)

| Message | Purpose |
|---|---|
| `register_observer` | Opt in, once, per connection. |
| `heartbeat` | Recorded via `ConnectionManager.record_heartbeat()`; used for the Connectivity Monitor panel's staleness detection. |
| `ping` | Answered for round-trip latency measurement. |

## 3. The contracts

Each of these is an invariant, not a suggestion. If you are changing
any file listed under a contract, re-read it first.

### Contract 1 — Observer isolation

**Invariant**: An event type introduced for the dashboard is routed
through `ConnectionManager.broadcast_observers()`. It is never passed
to `ConnectionManager.broadcast()`.

**Why it matters**: this is the one thing standing between "a desktop
dashboard" and "a second wire protocol every phone and PWA client must
also learn to ignore." It was violated once, during this subsystem's
own initial implementation, and broke six pre-existing protocol tests
through cross-talk before being caught.

**What breaks if violated**: any client that doesn't recognize a new
observer-only event type receives a WS frame it has no handler for.
Depending on that client's own parsing strictness, this ranges from
"harmlessly ignored" to "throws and disconnects." The 2026-07-22
hardening pass added `tests/test_dashboard_observer_isolation.py`
specifically to make a future violation of this contract fail a test
immediately rather than surface as a field-reported client crash.

**Files**: `app/connection_manager.py`, `app/supervisor/supervisor.py`,
`app/voice_session_manager.py`, `app/main.py`.

### Contract 2 — Dashboard clients never affect phone/PWA behavior

**Invariant**: Nothing a dashboard client sends can change what a
phone or PWA client observes, receives, or is permitted to do. A
dashboard is read-only with respect to the rest of the system —
`register_observer`/`heartbeat`/`ping` have no side effect visible to
any other connection.

**Why it matters**: the Control Center's whole purpose is to observe
Jarvis, not to become an alternate control surface with its own,
less-tested set of side effects on the primary user-facing product.

**What breaks if violated**: the dashboard stops being "safe to have
open all the time" and becomes one more thing that can destabilize a
real phone conversation — precisely the risk this ADR's whole design
exists to avoid.

**Files**: `app/main.py` (the `/ws` message dispatch — any new
dashboard-originated message type added here must be checked against
this contract before merging).

### Contract 3 — Non-observer clients never receive observer events

Restated from the other direction of Contract 1, and tested
independently: `tests/test_dashboard_observer_isolation.py` asserts
this for phone-style, PWA-style, and dashboard-style connections
explicitly, not only that observers *do* receive what they should.

### Contract 4 — Snapshot and dashboard auth match the rest of the API

**Invariant**: `GET /api/dashboard/snapshot` requires
`Depends(_require_api_token)`, identically to every other real
endpoint in `app/main.py`. `dashboard.js`'s own calls to
`/api/ws-token` and `/api/dashboard/snapshot` send whatever
`Authorization` header the deployment's `JARVIS_API_TOKEN` setting
requires.

**Why it matters**: found and fixed during hardening — the initial
implementation's dashboard sent no auth header at all, which meant
the moment `JARVIS_API_TOKEN` was ever set, the dashboard would fail
100% of the time with no way to recover, while (separately, see
Contract 5) the snapshot endpoint's *lack* of an auth check meant a
bulk, unauthenticated read of recent conversation and task data
remained reachable regardless. This contract exists specifically so
"the dashboard is exempt from auth because it's just for me" can never
quietly become true again.

**What breaks if violated**: either the dashboard silently stops
working the moment the deployment is hardened (ADR-014, TD-018), or —
worse — an unauthenticated endpoint returning recent conversation text
and task commands exists in a codebase that otherwise takes auth
seriously everywhere else.

**Files**: `app/main.py` (`GET /api/dashboard/snapshot`), `app/static/dashboard/dashboard.js`
(`connectWs()`, snapshot fetch).

### Contract 5 — Explicit field allow-lists, never `SELECT *`

**Invariant**: Every field `GET /api/dashboard/snapshot` returns is a
deliberately named field, chosen to match what `dashboard.js` actually
renders — never a raw forwarded row or ORM object. The `tasks` field
specifically returns exactly `{task_id, name, status, started_at,
completed_at}`, matching `/api/task/{id}`'s own existing convention of
withholding raw command/instruction text.

**Why it matters**: found during hardening that the initial
implementation forwarded `SELECT * FROM tasks` unfiltered, exposing a
field the project had already deliberately decided, elsewhere, to keep
out of API responses. An allow-list makes that decision durable —
adding a column to the underlying table can never silently expand
this API's contract; someone has to choose to add it here too.

**What breaks if violated**: a future schema change to `tasks`,
`events`, or any other table this endpoint reads from silently starts
appearing in dashboard responses, regardless of whether that column
was ever meant to be public.

**Files**: `app/main.py` (`GET /api/dashboard/snapshot`).

### Contract 6 — Zero observers, zero extra cost

**Invariant**: `ConnectionManager.has_observers()` is checked before
`db.save_event()` is called for any observer-only event, and before
any corresponding broadcast work — not only before the broadcast
itself. If no dashboard is connected, the phone-facing hot path
(`Supervisor.process_message()`, `VoiceSessionManager`'s lifecycle
transitions) does exactly the same work it would do if this subsystem
did not exist.

**Why it matters**: observability must never become a standing tax on
ordinary use. This system's primary product is the phone conversation,
not the dashboard; the dashboard is a lens onto the primary product,
never the other way around.

**What breaks if violated**: every Supervisor turn and every voice
session pays 2-6 extra blocking SQLite writes whether or not anyone
benefits from them — directly compounding the already-disclosed
TD-019 (unbounded `events` table growth) at a higher, unnecessary rate.

**Files**: `app/supervisor/supervisor.py` (`_broadcast()`),
`app/voice_session_manager.py` (`_broadcast_lifecycle()`,
`_schedule_lifecycle()`).

### Contract 7 — Observable decisions, never chain-of-thought

**Invariant**: Every event and every dashboard panel describes
*executed, observable* actions — a tool name, its arguments, its
result, a state transition — never the LLM's internal reasoning.

**Why it matters**: this is a product commitment as much as a
technical one. "Explainable without exposing chain-of-thought" is
this codebase's stated engineering principle going forward (see
ADR-018 §Decision point 7).

**What breaks if violated**: nothing in the current codebase's
tool-calling loop (`app/supervisor/supervisor.py`) has a "reason" or
raw-reasoning field to leak today — this contract's job is to make
sure that stays true as the Supervisor evolves, not to redact
something that currently exists.

**Files**: `app/supervisor/supervisor.py` (any future field added to
`supervisor_tool_call`/`supervisor_turn` payloads must be checked
against this contract before merging).

### Contract 8 — Hook registration is None-safe and set-once

**Invariant**: `set_broadcast_hook()` in `supervisor.py` and
`voice_session_manager.py` follows the exact pattern already
established by `app/attention_manager.py` — a module-global, set once
at startup by `app/main.py`, safely `None` (a no-op) if never set at
all (e.g. in most existing unit tests that construct a `Supervisor()`
or `VoiceSessionManager()` directly without going through `main.py`).

**Why it matters**: this is what lets every existing test that doesn't
care about observability keep working unmodified. It is also, without
care, a source of cross-test contamination — a module-global set by
one test file's import of `app.main` stays set for the rest of that
pytest process. `tests/conftest.py`'s autouse fixture resets both
hooks around every test specifically to close that risk.

**What breaks if violated**: a test file that imports `app.main`
anywhere in the suite causes unrelated `Supervisor`/`VoiceSessionManager`
unit tests elsewhere in the same pytest run to silently start
performing extra `db.save_event()` writes into their own isolated temp
databases — an order-dependent side effect with no current assertion
watching for it, meaning a regression here would not fail loudly on
its own merit; it would need to be caught by whatever it happens to
break next.

**Files**: `app/supervisor/supervisor.py`, `app/voice_session_manager.py`,
`tests/conftest.py`.

## 4. Authentication

Identical to the base protocol (`websocket-protocol-v1.md` §1.1) for
the WebSocket connection itself. `GET /api/dashboard/snapshot` follows
the same `Depends(_require_api_token)` convention as every other
authenticated REST endpoint in `app/main.py` — see Contract 4.

## 5. Subsystem ownership — what future contributors can depend on

### Stable, public — safe to depend on

- The `register_observer` message and the five observer-only event
  types in §2 (names and the fields documented above). Adding a new
  field to an existing event's payload is additive and safe; removing
  or renaming an existing field is a breaking change to this document,
  not a routine edit.
- `GET /api/dashboard/snapshot`'s field allow-list, per event type, as
  currently defined in `app/main.py`. Same rule: additive is safe,
  removal/rename is breaking.
- `ConnectionManager.has_observers()`, `mark_observer()`,
  `broadcast_observers()` as an interface — any future subsystem that
  wants to become observable calls these, exactly as `supervisor.py`
  and `voice_session_manager.py` already do.
- `set_broadcast_hook()`'s None-safe, set-once contract (Contract 8).

### Internal implementation — free to change without notice

- `app/static/dashboard/dashboard.js`'s internal state model (the
  `state` object, its per-panel rendering functions). This is
  presentation logic with no external consumers; refactor freely as
  long as the panels keep rendering what the protocol above promises.
- `ConnectionManager`'s internal storage of `_observers` (currently a
  set of connection objects) — any data structure that correctly
  implements `mark_observer()`/`broadcast_observers()`/`has_observers()`
  satisfies the contract.
- The exact SQL behind `GET /api/dashboard/snapshot`'s underlying
  queries (`get_recent_tasks()`, `get_recent_events()`, etc.) — free to
  optimize as long as the response shape (§5's allow-list) is
  unchanged.
- `scripts/dashboard_demo_seed.py` — a development/manual-verification
  tool, not a production code path. Not covered by any contract in
  this document.

### Explicitly experimental — no contract yet, expect change

- Nothing in the current implementation is marked experimental. Any
  future extension (see the roadmap document) that has not yet gone
  through the same review this subsystem went through should be
  built in a way that's clearly separable, and should not be assumed
  stable until it earns its own entry in this document.

## References

- `docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`
- `docs/protocols/websocket-protocol-v1.md`
- `docs/TECHNICAL_DEBT.md` (TD-019, TD-018 — both referenced above)
