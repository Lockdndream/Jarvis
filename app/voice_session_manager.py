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
STATE_CLOSING = "closing"
STATE_CLOSED = "closed"
STATE_FAILED = "failed"

# Explicit legal-transition map: to_state -> allowed current (from) states.
_LEGAL_TRANSITIONS = {
    STATE_OPENING: (STATE_IDLE,),
    STATE_LISTENING: (STATE_OPENING, STATE_SPEAKING, STATE_WAITING),
    # Barge-in guard lives here: PROCESSING only accepts LISTENING as its
    # predecessor, so a second concurrent transcript for the same session
    # (already PROCESSING) cannot re-enter PROCESSING — see
    # handle_transcript()'s explicit check of this transition's result.
    STATE_PROCESSING: (STATE_LISTENING,),
    STATE_SPEAKING: (STATE_PROCESSING,),
    STATE_WAITING: (STATE_PROCESSING, STATE_SPEAKING),
    STATE_DEFERRED: (STATE_PROCESSING,),
    STATE_CLOSING: (STATE_OPENING, STATE_LISTENING, STATE_PROCESSING, STATE_SPEAKING, STATE_WAITING, STATE_DEFERRED),
    STATE_CLOSED: (STATE_CLOSING, STATE_FAILED),
    STATE_FAILED: (STATE_OPENING, STATE_LISTENING, STATE_PROCESSING, STATE_SPEAKING, STATE_WAITING),
}

_TERMINAL_ATTENTION_STATUSES = ("resolved", "cancelled", "expired")


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


class VoiceSessionManager:
    def __init__(self, supervisor):
        self._supervisor = supervisor

    def _transition(self, voice_session_id: str, to_state: str) -> bool:
        session = db.get_voice_session(voice_session_id)
        if not session:
            raise VoiceSessionError(f"Unknown voice session {voice_session_id}")
        froms = _LEGAL_TRANSITIONS.get(to_state, ())
        if session["state"] not in froms:
            logger.info(
                "voice session transition rejected: id=%s from=%s to=%s",
                voice_session_id, session["state"], to_state,
            )
            return False
        db.update_voice_session_state(voice_session_id, to_state)
        logger.info("voice session state: id=%s %s -> %s", voice_session_id, session["state"], to_state)
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
        db.create_voice_session(voice_session_id, conversation_id, attention_request_id)
        self._transition(voice_session_id, STATE_OPENING)
        self._transition(voice_session_id, STATE_LISTENING)
        logger.info(
            "voice session opened: id=%s conversation_id=%s attention_request_id=%s",
            voice_session_id, conversation_id, attention_request_id,
        )
        session = db.get_voice_session(voice_session_id)
        session["greeting"] = _build_greeting(bound_row) if bound_row else None
        return session

    async def handle_transcript(self, voice_session_id: str, transcript: str) -> dict:
        """Routes the transcript to the existing Supervisor, scoped to the
        session's bound AttentionRequest if any (Phase 12). Returns
        {"response", "conversation_id", "attention_request_id"}."""
        session = db.get_voice_session(voice_session_id)
        if not session or session["state"] == STATE_CLOSED:
            raise VoiceSessionError(f"Voice session {voice_session_id} is not open")

        if not self._transition(voice_session_id, STATE_PROCESSING):
            # Barge-in / overlap guard: a transcript is already being
            # processed for this session (e.g. two arrived concurrently).
            # Never process both — the caller should discard this one.
            raise VoiceSessionError(f"Voice session {voice_session_id} is already processing a turn")

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
                return {
                    "response": f"That item is already {status_word} — nothing more to do there.",
                    "conversation_id": session["conversation_id"],
                    "attention_request_id": None,
                    "voice_session_state": STATE_LISTENING,
                }

        result = await self._supervisor.process_message(
            transcript, session["conversation_id"], bound_attention_request_id=bound_attention_id,
        )

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
                return {
                    "response": result.get("response", ""),
                    "conversation_id": result.get("conversation_id") or session["conversation_id"],
                    "attention_request_id": bound_attention_id,
                    "voice_session_state": STATE_DEFERRED,
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
        return {
            "response": result.get("response", ""),
            "conversation_id": result.get("conversation_id") or session["conversation_id"],
            "attention_request_id": bound_attention_id,
            "voice_session_state": STATE_LISTENING,
        }

    def mark_deferred(self, voice_session_id: str) -> bool:
        return self._transition(voice_session_id, STATE_DEFERRED)

    def close_session(self, voice_session_id: str) -> bool:
        session = db.get_voice_session(voice_session_id)
        if not session:
            return False
        if session["state"] == STATE_CLOSED:
            return True
        self._transition(voice_session_id, STATE_CLOSING)
        return self._transition(voice_session_id, STATE_CLOSED)

    def fail_session(self, voice_session_id: str) -> bool:
        ok = self._transition(voice_session_id, STATE_FAILED)
        if ok:
            logger.info("voice session failed: id=%s", voice_session_id)
        return ok
