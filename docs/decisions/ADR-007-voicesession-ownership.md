# ADR-007: VoiceSession Ownership

## Status

Accepted. The multi-client ownership guard (Tradeoffs, below) was
implemented in Milestone 9B.4 (ADR-016) — see that ADR for the
implementation; this ADR's own text is left as originally written for
historical accuracy (the guard's design predates its implementation).

## Date

2026-07-10 (Milestone 8, `VoiceSessionManager` introduced)

## Context

Milestone 7 added voice input/output on the same code path as typed
text. Milestone 8 needed something more: a *session* concept — a voice
conversation can span multiple turns, can be proactively opened by
Jarvis itself (a bound attention contact), and needs a lifecycle
(listening, processing, speaking, waiting, deferred) distinct from a
single request/response. The risk this ADR specifically addresses: once
a session concept exists, it becomes a plausible place to accidentally
put conversational logic — answer matching, defer parsing — that
duplicates what `Supervisor`/`deferral.py` already do.

## Problem

Who owns a voice conversation's lifecycle state, and how is that kept
from becoming a second place where conversational reasoning lives?

## Decision

`VoiceSessionManager` (`app/voice_session_manager.py`) is the single
owner of `VoiceSession` lifecycle and state (the `voice_sessions` table),
and it is deliberately a **thin pass-through**: `handle_transcript()`
does no answer-matching or defer-parsing itself — it forwards directly to
`Supervisor.process_message()`, the same conversational entry point typed
text uses, and only re-checks the bound `AttentionRequest`'s status
afterward to decide the correct next lifecycle transition (e.g.
`WAITING → DEFERRED` if the turn just deferred the bound source).

The state machine itself (`idle → opening → listening ⇄ processing →
speaking`, with `waiting`/`deferred`/`closing`/`closed`/`failed` as
documented in `ARCHITECTURE.md` §7) is explicit and guarded — an entity
transitions because a specific allowed transition function ran, per
ADR-001's broader "explicit state transitions" principle.

## Alternatives Considered

**Let `VoiceSessionManager` interpret transcripts directly (e.g. match
"yes"/"no", parse deferral phrases inline).** Rejected, and this is not
hypothetical: an early version of this module's own docstring claimed a
behavior (`WAITING → DEFERRED` transition on bound-defer) that no code
path actually implemented — a real, phone-found bug (multi-turn sessions
silently broken from Milestone 8 through Milestone 9A, every second turn
in any session rejected) traced directly to the ambiguity between "the
comment says this is handled" and "reasoning logic actually lives
elsewhere." The fix reinforced rather than reversed this ADR: complete
the *lifecycle* transition after `Supervisor.process_message()` returns,
still without adding any interpretation logic to this module.

**A separate voice-specific conversation store, distinct from
`conversations`.** Rejected: voice transcripts flow through the exact
same conversational pipeline as typed text (ADR from Milestone 7 — "voice
input must stay on the exact same code path as typed text"); a separate
store would fork that guarantee for no benefit.

**No session concept at all — treat every voice turn as independent.**
Rejected: cannot support proactive Jarvis-initiated contact (a bound
attention session needs to know it's still the same ongoing
conversation, e.g. to state context once and not repeat it every turn)
or multi-turn clarification exchanges, both real, already-implemented
requirements by Milestone 8.

## Consequences

Any future change to how a voice turn is processed must go through
`Supervisor.process_message()`, unchanged — `VoiceSessionManager` is not
an acceptable place to add a shortcut, even for voice-specific
convenience. This constraint is expected to extend directly to a future
native Android client (ADR-002): a native companion's voice UI should
call the same server-side session/turn protocol, not reimplement any part
of transcript interpretation locally (ADR-001).

## Positive Outcomes

- The exact bug this design otherwise would have hidden (the missing
  `WAITING → LISTENING` transition) was findable and fixable in one place,
  with one regression test, precisely because the module's only job is
  lifecycle — there was no ambiguity about where the bug lived once
  found.
- A bound voice session's context-greeting behavior (Milestone 8.1) and
  transcript-echo behavior (also Milestone 8.1) were both real gaps found
  on a real phone and fixed without touching `Supervisor` at all — proof
  the lifecycle/reasoning split holds up under real device pressure, not
  just in design.

## Tradeoffs

- **No ownership guard currently exists for a second client binding to
  the same `AttentionRequest` simultaneously.** Two clients could each
  successfully call `open_session()` against the same bound source today,
  creating two independent `VoiceSession`s with no coordination between
  them. This was found by Milestone 9A's independent code review, judged
  to only matter once a second (e.g. native) client actually exists
  concurrently with the PWA, and deliberately left unimplemented as of
  this ADR. The designed fix (a nullable
  `attention_requests.active_voice_session_id`, atomic set-if-null,
  reusing the existing guarded-transition pattern) is documented but not
  built.
- Every voice turn pays the cost of a full round trip through
  `Supervisor.process_message()` even for what might look like a trivial
  voice-specific shortcut — a deliberate cost, consistent with ADR-001.

## Future Revisit Conditions

**Must be revisited — the ownership guard must be implemented — before a
second client (native Android companion or otherwise) is ever connected
and voice-capable simultaneously with the PWA.** This is not a
someday-maybe item; it is a known, scoped gap with a designed fix,
blocking on the precondition that currently does not yet exist.

## References

- `ARCHITECTURE.md` §4 (VoiceSessionManager), §7 (Voice Architecture)
- `SESSION.md`, Milestone 8 (introduced, docstring/implementation gap and
  fix), Milestone 8.1 (context-greeting and transcript-echo fixes),
  Milestone 9A (ownership-guard gap found by D2 review), Architecture
  Decision #10, Known Limitations #45–46

## Related Milestones

Milestone 7, Milestone 8, Milestone 8.1, Milestone 9A

## Related Source Files

- `app/voice_session_manager.py`
- `app/supervisor/supervisor.py`
- `app/deferral.py`
