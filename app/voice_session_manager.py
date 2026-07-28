"""VoiceSessionManager (Milestone 8 Phase 10).

Not a new AI agent — manages voice interaction *state* around the existing
Supervisor. All reasoning stays in app/supervisor/supervisor.py; this
module only tracks session lifecycle, prevents overlapping speech/
listening turns, supports barge-in at the state level, and retains exact
attention correlation so a voice session opened from a specific
AttentionRequest never needs global ambiguity resolution (Phase 12).

Flow: transcript -> VoiceSessionManager.handle_transcript() -> existing
Supervisor.process_message() -> response text -> caller (the WebSocket
handler) sends it to the browser, which speaks it with the existing M7
browser TTS. This module never talks to speech APIs directly — those stay
entirely client-side, unchanged since Milestone 7.
"""
import asyncio
import json
import logging
import uuid

import app.database as db

logger = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_OPENING = "opening"
STATE_LISTENING = "listening"
STATE_PROCESSING = "processing"
STATE_SPEAKING = "speaking"
STATE_WAITING = "waiting"
STATE_DEFERRED = "deferred"
# Interaction Layer v1 (Goal 5): the session is waiting on a yes/no reply
# to a technical-command confirmation prompt (see supervisor.py's
# _build_confirmation_prompt / pending_confirmation). Behaves like
# LISTENING for the purpose of accepting the next transcript (both are
# legal predecessors of PROCESSING) — the difference is entirely in how
# handle_transcript() below routes that next transcript once it arrives.
STATE_CONFIRMING = "confirming"
STATE_CLOSING = "closing"
STATE_CLOSED = "closed"
STATE_FAILED = "failed"

# Explicit legal-transition map: to_state -> allowed current (from) states.
_LEGAL_TRANSITIONS = {
    STATE_OPENING: (STATE_IDLE,),
    STATE_LISTENING: (STATE_OPENING, STATE_SPEAKING, STATE_WAITING),
    # Barge-in guard lives here: PROCESSING only accepts LISTENING/
    # CONFIRMING as its predecessor, so a second concurrent transcript for
    # the same session (already PROCESSING) cannot re-enter PROCESSING —
    # see handle_transcript()'s explicit check of this transition's result.
    STATE_PROCESSING: (STATE_LISTENING, STATE_CONFIRMING),
    STATE_SPEAKING: (STATE_PROCESSING,),
    STATE_WAITING: (STATE_PROCESSING, STATE_SPEAKING),
    STATE_DEFERRED: (STATE_PROCESSING,),
    STATE_CONFIRMING: (STATE_PROCESSING,),
    STATE_CLOSING: (STATE_OPENING, STATE_LISTENING, STATE_PROCESSING, STATE_SPEAKING, STATE_WAITING, STATE_DEFERRED, STATE_CONFIRMING),
    STATE_CLOSED: (STATE_CLOSING, STATE_FAILED),
    STATE_FAILED: (STATE_OPENING, STATE_LISTENING, STATE_PROCESSING, STATE_SPEAKING, STATE_WAITING, STATE_CONFIRMING),
}

_TERMINAL_ATTENTION_STATUSES = ("resolved", "cancelled", "expired")

# Interaction Layer v1 (Goal 5): tool names that must be read back to the
# user for a yes/no confirmation before executing, on voice turns only
# (see Supervisor.process_message's confirm_before_tools parameter — text/
# browser chat callers never pass this). Scoped to start_opencode_task
# only, per the milestone's own scope: the one tool whose arguments are
# free-form technical text with a real filesystem side effect.
CONFIRM_BEFORE_TOOLS = frozenset({"start_opencode_task"})


def _build_greeting(attention_row: dict) -> str:
    """Milestone 8.1 real-phone finding (2026-07-10): a voice session
    opened bound to an AttentionRequest silently went straight to
    listening, with nothing spoken/shown first — a re-contacted user had
    no way to know what they were being asked about except by reading a
    different on-screen panel. This is not a new capability, it completes
    an already-specified requirement (Phase 9 and the mandatory Phase 25
    primary scenario's "Jarvis: 'You asked me to come back...'" line).
    Deterministic, no LLM — same style as supervisor.py's
    _describe_relative() confirmation messages."""
    prefix = "You asked me to come back. " if attention_row.get("contact_attempt_count", 0) > 1 else ""
    attention_type = attention_row.get("attention_type")
    source_id = attention_row.get("source_id")
    if attention_type == "QUESTION" and source_id:
        q = db.get_question_record(source_id)
        if q and q.get("question"):
            try:
                options = json.loads(q.get("options_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                options = []
            opts_text = f" Options: {', '.join(options)}." if options else ""
            return f"{prefix}{q['question']}{opts_text}"
    return f"{prefix}{attention_row.get('summary') or 'Jarvis needs your input.'}"


class VoiceSessionError(Exception):
    pass


# Control Center dashboard support (additive). Same set-once-at-startup,
# None-safe hook pattern as app/attention_manager.py's _broadcast_hook —
# VoiceSessionManager previously broadcast nothing at all (every state
# transition below was visible only in the log and to the single
# WebSocket connection that happened to be driving it), so there was no
# existing plumbing to piggyback on for a dashboard-observable voice
# pipeline. Uses ConnectionManager.broadcast_observers() — the dashboard-
# only fan-out, never the general broadcast() every protocol connection
# receives — so an ordinary phone/PWA connection never sees this new
# "voice_session_lifecycle" event type at all (a real regression found
# and fixed while building this: the general broadcast() interleaved
# these frames with other connections' own expected request/response
# frames, breaking existing multi-connection protocol tests).
_broadcast_hook = None


def set_broadcast_hook(conn_manager) -> None:
    global _broadcast_hook
    _broadcast_hook = conn_manager


async def _broadcast_lifecycle(phase: str, session: dict, trace_id: str | None = None, **extra) -> None:
    # Phase 4 (Control Center hardening): checked before db.save_event,
    # not just before broadcast_observers() inside it -- with no
    # dashboard open, this is an avoidable sqlite write on every voice
    # turn for a subsystem nobody is currently watching.
    if _broadcast_hook is None or not _broadcast_hook.has_observers():
        return
    # ADR-020: a VoiceSession spans many turns, so it has no single
    # trace_id of its own -- callers explicitly pass the trace_id of the
    # turn just completed (from Supervisor.process_message()'s result)
    # where one exists; phases with no turn behind them yet (session open,
    # transcript just received, a deterministic reply that never reached
    # process_message) correctly have none. `trace.current_trace_id()` is
    # NOT read as a fallback here -- by the time most of these calls run,
    # process_message() has already returned and reset its ContextVar
    # binding, so reading it here would silently be stale/None regardless.
    payload = {
        "voice_session_id": session.get("voice_session_id"),
        "conversation_id": session.get("conversation_id"),
        "attention_request_id": session.get("attention_request_id"),
        "phase": phase,
        "trace_id": trace_id,
        **extra,
    }
    try:
        content = json.dumps(payload)
        db.save_event("voice_session_lifecycle", content, trace_id=trace_id)
        await _broadcast_hook.broadcast_observers({
            "type": "voice_session_lifecycle", "timestamp": db.utcnow(), "content": content,
        })
    except Exception as e:
        logger.warning("voice_session_lifecycle broadcast failed (state unaffected): %s", e)


def _schedule_lifecycle(phase: str, session: dict, **extra) -> None:
    """Fire-and-forget variant for open_session/close_session/fail_session,
    which are synchronous methods (unchanged signatures — many existing
    call sites, including plain synchronous unit tests with no running
    event loop, call them without `await`). Skips entirely when no hook is
    registered (the common unit-test case). Checks for a running loop
    *before* constructing the `_broadcast_lifecycle(...)` coroutine object
    (rather than constructing it and letting asyncio.create_task fail) so
    a synchronous caller with no loop never leaves an unawaited coroutine
    behind — that leftover construct-then-fail pattern is exactly what
    produces Python's "coroutine was never awaited" RuntimeWarning."""
    if _broadcast_hook is None or not _broadcast_hook.has_observers():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running event loop (e.g. a synchronous unit test) — skip
    loop.create_task(_broadcast_lifecycle(phase, session, **extra))


class VoiceSessionManager:
    def __init__(self, supervisor):
        self._supervisor = supervisor

    def _transition(self, voice_session_id: str, to_state: str, termination_reason: str | None = None) -> bool:
        session = db.get_voice_session(voice_session_id)
        if not session:
            raise VoiceSessionError(f"Unknown voice session {voice_session_id}")
        froms = _LEGAL_TRANSITIONS.get(to_state, ())
        if session["state"] not in froms:
            logger.info(
                "voice session transition rejected: id=%s from=%s to=%s",
                voice_session_id, session["state"], to_state,
                extra={"conversation_id": session["conversation_id"]},
            )
            return False
        db.update_voice_session_state(voice_session_id, to_state, termination_reason=termination_reason)
        # M-OX.3 live-validation finding: every voice_session_manager log
        # line carried conversation_id: null despite it being right there
        # on `session` -- indistinguishable from a concurrent second
        # session's own lines except by parsing the free-text `id=...`
        # substring. conversation_id is genuinely known and stable here
        # (unlike trace_id, which is never bound at any of this method's
        # call sites -- see ADR-020's own documented transcript_received
        # limitation -- so it is deliberately not added here).
        logger.info(
            "voice session state: id=%s %s -> %s", voice_session_id, session["state"], to_state,
            extra={"conversation_id": session["conversation_id"]},
        )
        return True

    def open_session(self, conversation_id: str, attention_request_id: str | None = None) -> dict:
        voice_session_id = f"vs_{uuid.uuid4().hex[:12]}"
        bound_row = None
        if attention_request_id:
            row = db.get_attention_request(attention_request_id)
            if not row or row["status"] in _TERMINAL_ATTENTION_STATUSES:
                # Phase 12: never bind a new session to a stale/already-
                # resolved source — open unbound instead of silently acting
                # on something that no longer applies.
                logger.info(
                    "voice session open: requested attention_request_id=%s is stale/missing, opening unbound",
                    attention_request_id,
                )
                attention_request_id = None
            else:
                bound_row = row

        if attention_request_id and not db.try_claim_voice_session_lease(attention_request_id, voice_session_id):
            # TD-002/ADR-007 ownership guard: a second client (e.g. the PWA
            # and the Android companion simultaneously) cannot each open an
            # independent, uncoordinated VoiceSession bound to the same
            # AttentionRequest. Raise instead of silently opening unbound —
            # the whole point is to surface the conflict, not hide it.
            logger.info(
                "voice session open rejected: attention_request_id=%s already has an active session",
                attention_request_id,
            )
            raise VoiceSessionError(
                f"AttentionRequest {attention_request_id} already has an active voice session on another device"
            )

        db.create_voice_session(voice_session_id, conversation_id, attention_request_id)
        self._transition(voice_session_id, STATE_OPENING)
        self._transition(voice_session_id, STATE_LISTENING)
        logger.info(
            "voice session opened: id=%s conversation_id=%s attention_request_id=%s",
            voice_session_id, conversation_id, attention_request_id,
        )
        session = db.get_voice_session(voice_session_id)
        session["greeting"] = _build_greeting(bound_row) if bound_row else None  # type: ignore[index]  # TODO(F1.14): transaction boundaries
        _schedule_lifecycle("opened", session)  # type: ignore[arg-type]  # TODO(F1.14): transaction boundaries
        return session  # type: ignore[return-value]  # TODO(F1.14): transaction boundaries

    async def handle_transcript(self, voice_session_id: str, transcript: str) -> dict:
        """Routes the transcript to the existing Supervisor, scoped to the
        session's bound AttentionRequest if any (Phase 12). Returns
        {"response", "conversation_id", "attention_request_id"}."""
        session = db.get_voice_session(voice_session_id)
        if not session or session["state"] == STATE_CLOSED:
            raise VoiceSessionError(f"Voice session {voice_session_id} is not open")

        # Interaction Layer v1 (Goal 5): captured *before* the PROCESSING
        # transition below, since that transition itself overwrites
        # session["state"] as far as the DB is concerned — this is the
        # only place that still knows whether the incoming transcript is
        # an ordinary turn or a yes/no reply to a pending confirmation.
        was_confirming = session["state"] == STATE_CONFIRMING

        if not self._transition(voice_session_id, STATE_PROCESSING):
            # Barge-in / overlap guard: a transcript is already being
            # processed for this session (e.g. two arrived concurrently).
            # Never process both — the caller should discard this one.
            raise VoiceSessionError(f"Voice session {voice_session_id} is already processing a turn")

        await _broadcast_lifecycle("transcript_received", session, transcript=transcript[:500])

        if was_confirming:
            pending_json = session.get("pending_tool_call")
            pending = json.loads(pending_json) if pending_json else None
            if pending is None:
                # Defensive only — CONFIRMING and pending_tool_call are
                # always set together (see supervisor.py's
                # pending_confirmation handling), so this should be
                # unreachable. Never crash a live voice turn on an
                # inconsistent row; fall back to plain listening.
                result = {"response": "Sorry, I lost track of what I was confirming. What would you like to do?", "conversation_id": session["conversation_id"]}
            else:
                result = await self._supervisor.resolve_pending_tool_confirmation(
                    session["conversation_id"], transcript, pending,
                )

            still_pending = result.get("pending_tool_call")
            if still_pending:
                # "Unclear" reply (see Supervisor.resolve_pending_tool_
                # confirmation) — re-ask and stay in CONFIRMING rather
                # than guessing which way the user meant it.
                db.set_pending_tool_call(voice_session_id, still_pending["name"], still_pending["args"])
                self._transition(voice_session_id, STATE_CONFIRMING)
                next_state = STATE_CONFIRMING
            else:
                db.clear_pending_tool_call(voice_session_id)
                self._transition(voice_session_id, STATE_WAITING)
                self._transition(voice_session_id, STATE_LISTENING)
                next_state = STATE_LISTENING

            await _broadcast_lifecycle(
                "turn_completed", session, trace_id=result.get("trace_id"),
                response=result.get("response", "")[:500], voice_session_state=next_state,
            )
            return {
                "response": result.get("response", ""),
                "conversation_id": result.get("conversation_id") or session["conversation_id"],
                "attention_request_id": None,
                "voice_session_state": next_state,
                "trace_id": result.get("trace_id"),
            }

        bound_attention_id = session.get("attention_request_id")
        if bound_attention_id:
            row = db.get_attention_request(bound_attention_id)
            if not row or row["status"] in _TERMINAL_ATTENTION_STATUSES:
                # Phase 12: if the bound attention became stale mid-session
                # (resolved/cancelled elsewhere), report current state
                # instead of acting on a dead source.
                self._transition(voice_session_id, STATE_WAITING)
                self._transition(voice_session_id, STATE_LISTENING)
                status_word = row["status"] if row else "no longer available"
                response = f"That item is already {status_word} — nothing more to do there."
                await _broadcast_lifecycle("turn_completed", session, response=response, voice_session_state=STATE_LISTENING)
                return {
                    "response": response,
                    "conversation_id": session["conversation_id"],
                    "attention_request_id": None,
                    "voice_session_state": STATE_LISTENING,
                }

        result = await self._supervisor.process_message(
            transcript, session["conversation_id"], bound_attention_request_id=bound_attention_id,
            confirm_before_tools=CONFIRM_BEFORE_TOOLS,
        )

        # Milestone 9B.10 (independent review finding): process_message()
        # awaits, and the session could have been closed by something
        # else while this turn was in flight — the idle-timeout reaper
        # (VoiceSessionReaper), an explicit close from another device, or
        # even the LLM's own close_voice_session tool call acting on this
        # exact session mid-turn. Without this check, the WAITING/
        # LISTENING transitions below fail silently (return False from
        # _transition() on a CLOSED session, since CLOSED is never a
        # legal from-state for either) and this function would return
        # voice_session_state: "listening" for a session that is actually
        # closed — a real, misleading response, not just a theoretical
        # concern.
        current = db.get_voice_session(voice_session_id)
        if not current or current["state"] == STATE_CLOSED:
            raise VoiceSessionError(f"Voice session {voice_session_id} was closed while processing this turn")

        # If this turn was a successful defer of the bound AttentionRequest
        # (Phase 18's fast path, resolved inside process_message), the call
        # is over from the user's perspective ("Not now, come back in 15
        # minutes" -> "Okay.") — move straight to DEFERRED instead of
        # WAITING for another turn, so the caller (the WS handler) knows
        # not to keep the mic listening.
        if bound_attention_id:
            post_row = db.get_attention_request(bound_attention_id)
            if post_row and post_row["status"] == "deferred":
                self._transition(voice_session_id, STATE_DEFERRED)
                await _broadcast_lifecycle(
                    "turn_completed", session, trace_id=result.get("trace_id"),
                    response=result.get("response", "")[:500], voice_session_state=STATE_DEFERRED,
                )
                return {
                    "response": result.get("response", ""),
                    "conversation_id": result.get("conversation_id") or session["conversation_id"],
                    "attention_request_id": bound_attention_id,
                    "voice_session_state": STATE_DEFERRED,
                    "trace_id": result.get("trace_id"),
                }

        # Interaction Layer v1 (Goal 5): the LLM proposed a tool in
        # CONFIRM_BEFORE_TOOLS this turn — process_message() stopped
        # *before* executing it (see supervisor.py's pending_confirmation
        # handling) and final_content is already the read-back question.
        # Persist the pending call and move to CONFIRMING instead of the
        # usual WAITING -> LISTENING; the next transcript resolves it via
        # the was_confirming branch above, never re-entering the LLM loop.
        pending_tool_call = result.get("pending_tool_call")
        if pending_tool_call:
            db.set_pending_tool_call(voice_session_id, pending_tool_call["name"], pending_tool_call["args"])
            self._transition(voice_session_id, STATE_CONFIRMING)
            await _broadcast_lifecycle(
                "turn_completed", session, trace_id=result.get("trace_id"),
                response=result.get("response", "")[:500], voice_session_state=STATE_CONFIRMING,
            )
            return {
                "response": result.get("response", ""),
                "conversation_id": result.get("conversation_id") or session["conversation_id"],
                "attention_request_id": bound_attention_id,
                "voice_session_state": STATE_CONFIRMING,
                "trace_id": result.get("trace_id"),
            }

        # Milestone 9A real-phone finding (2026-07-10): STATE_WAITING was a
        # dead-end — the legal-transition map already allowed WAITING ->
        # LISTENING (for exactly this reason) but nothing ever performed
        # that transition, so every second turn in a session was rejected
        # by the PROCESSING guard (which requires LISTENING as its
        # predecessor) with a misleading "already processing" error. The
        # client always resumes listening after speaking a non-terminal
        # response (see speakForVoiceSession() in app.js), so completing
        # this already-designed transition here — immediately, not via a
        # separate round-trip signal — keeps the server's state honest
        # without inventing a new WS message type.
        self._transition(voice_session_id, STATE_WAITING)
        self._transition(voice_session_id, STATE_LISTENING)
        await _broadcast_lifecycle(
            "turn_completed", session, trace_id=result.get("trace_id"),
            response=result.get("response", "")[:500], voice_session_state=STATE_LISTENING,
        )
        return {
            "response": result.get("response", ""),
            "conversation_id": result.get("conversation_id") or session["conversation_id"],
            "attention_request_id": bound_attention_id,
            "voice_session_state": STATE_LISTENING,
            "trace_id": result.get("trace_id"),
        }

    def mark_deferred(self, voice_session_id: str) -> bool:
        return self._transition(voice_session_id, STATE_DEFERRED)

    def close_session(self, voice_session_id: str, reason: str = "client_requested") -> bool:
        """reason (Milestone 9B.10) is recorded on the terminal transition
        only — 'client_requested' (explicit voice_session_close),
        'disconnect' (main.py's finally block on WebSocket teardown), or
        'idle_timeout' (reap_idle_sessions() below). Never overwrites an
        already-recorded reason (see db.update_voice_session_state's
        COALESCE) — close_session() is idempotent and a second call here
        (e.g. a duplicate close message) must not blame the wrong cause."""
        session = db.get_voice_session(voice_session_id)
        if not session:
            return False
        if session["state"] == STATE_CLOSED:
            return True
        self._transition(voice_session_id, STATE_CLOSING)
        ok = self._transition(voice_session_id, STATE_CLOSED, termination_reason=reason)
        if ok and session.get("attention_request_id"):
            # Release the TD-002 lease so a future session (this device or
            # another) can bind to the same AttentionRequest again.
            db.release_voice_session_lease(session["attention_request_id"], voice_session_id)
        if ok:
            _schedule_lifecycle("closed", session, reason=reason)
        return ok

    def reap_idle_sessions(self, max_idle_seconds: int, now: str | None = None) -> list[dict]:
        """Milestone 9B.10: closes any VoiceSession that never reached a
        terminal state and has had no activity (no transition of any
        kind — including each conversational turn) for over
        max_idle_seconds. Backstops every orphan path that isn't already
        covered: the OS killing VoiceActivity before its own onStop() can
        run (ADR-017/9B.9's client-side fix), a Supervisor tool call that
        opens a session and is never followed by a matching close, and any
        non-Android client that abandons a session while its connection
        stays alive (main.py's disconnect-triggered close in the `finally`
        block only fires when the connection itself actually drops).
        Returns the list of reaped session rows so the caller can notify
        any still-connected client that its session was force-closed."""
        idle = db.get_idle_voice_sessions(max_idle_seconds, now=now)
        reaped = []
        for session in idle:
            if self.close_session(session["voice_session_id"], reason="idle_timeout"):
                logger.info(
                    "voice session idle-timeout reap: id=%s state=%s idle_since=%s",
                    session["voice_session_id"], session["state"], session["updated_at"],
                )
                reaped.append(session)
        return reaped

    def fail_session(self, voice_session_id: str) -> bool:
        session = db.get_voice_session(voice_session_id)
        ok = self._transition(voice_session_id, STATE_FAILED)
        if ok:
            logger.info("voice session failed: id=%s", voice_session_id)
            if session and session.get("attention_request_id"):
                db.release_voice_session_lease(session["attention_request_id"], voice_session_id)
            if session:
                _schedule_lifecycle("failed", session)
        return ok
