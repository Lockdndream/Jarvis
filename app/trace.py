"""Trace ID: the correlation primitive spanning one logical unit of work
(one conversational turn) across the Supervisor, its tools, and any
OpenCode task the turn starts. See ADR-020.

Generalizes ADR-017's client_request_id rather than inventing a fourth
identifier alongside conversation_id/voice_session_id/task_id: when a
client-supplied correlation id exists for a request, it is reused as that
request's trace_id; otherwise Jarvis mints its own. ADR-017 already states
the intent explicitly — "any future asynchronous client operation... should
reuse this same primitive rather than inventing its own feature-specific
identifier."

Read via a ContextVar, not a threaded function parameter, because the
call sites that need it (ToolRegistry -> OpenCodeSupervisor.start_session,
several levels below Supervisor.process_message) are too deep to thread a
new parameter through without touching every intermediate signature.
asyncio tasks each get their own copy of the current context, which is
exactly what's needed given Supervisor's own _active_turns comment: two
turns (e.g. phone + PWA) can genuinely overlap, so a bare module-global
would let one turn's trace_id leak into another's records.
"""
import contextvars
import uuid

_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_trace_id", default=None
)


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:12]}"


def current_trace_id() -> str | None:
    """The trace_id of the turn currently executing on this asyncio Task, or
    None if called with no turn bound (e.g. a background loop tick, or a
    plain synchronous unit test)."""
    return _current_trace_id.get()


def bind_trace_id(trace_id: str) -> contextvars.Token:
    return _current_trace_id.set(trace_id)


def reset_trace_id(token: contextvars.Token) -> None:
    _current_trace_id.reset(token)
