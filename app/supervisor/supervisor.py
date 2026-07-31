"""Supervisor orchestrator: receives user messages, builds context,
calls LLM, executes tools, and returns conversational responses.

Max 5 tool calls per user turn. No infinite loops.
"""
import asyncio
import json
import logging
import re
import uuid
from typing import Any

import app.database as db
from app import db_async as adb
from app import config
from app.supervisor.tools import ToolRegistry
from app.supervisor.llm import LLMProvider, FakeLLMProvider
from app.supervisor.context import build_context, format_memory_sections
from app import deferral
from app import attention_manager
from app import trace
from app.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 5
def _supervisor_enabled() -> bool:
    return config.supervisor_enabled()


# Control Center dashboard support (additive). Same set-once-at-startup,
# None-safe hook pattern as app/attention_manager.py's _broadcast_hook and
# app/voice_session_manager.py's — the supervisor's tool-calling loop
# previously broadcast nothing at all; a tool call was only visible in the
# log line right below and in the `conversations` table (see
# _persist_tool_call), neither of which is a live stream. Both new event
# types below are observable-execution-only (tool name, arguments, a
# truncated *result* string) — never the LLM's own reasoning/explanation
# text, which this codebase never captures in the first place (there is
# no "reason" field anywhere in this module to broadcast). Uses
# ConnectionManager.broadcast_observers() — the dashboard-only fan-out —
# not the general broadcast() every phone/PWA connection also receives:
# a real regression found and fixed while building this feature, where
# the general broadcast() interleaved these frames with other
# connections' own expected request/response frames and broke existing
# protocol tests that assume strict per-connection frame ordering.
_broadcast_hook = None

# Control Center status display (Milestone 9B.10, additive): a plain
# in-memory counter, not persisted, not gated by has_observers() -- unlike
# _broadcast() this costs nothing worth gating (an int increment/decrement
# already sitting on every code path through process_message()). A count
# rather than a bool because two turns (e.g. phone + PWA) could genuinely
# overlap; "processing" means the count is nonzero, not that a specific
# turn is in flight.
_active_turns = 0


def get_supervisor_state() -> str:
    return "processing" if _active_turns > 0 else "idle"


def set_broadcast_hook(conn_manager: ConnectionManager) -> None:
    global _broadcast_hook
    _broadcast_hook = conn_manager


async def _broadcast(event_type: str, payload: dict) -> None:
    # Phase 4 (Control Center hardening): checked *before* db.save_event,
    # not just before the broadcast_observers() call inside it — with no
    # dashboard open, this whole function (including the sqlite write)
    # is a real, avoidable per-turn/per-tool-call cost on the phone-
    # facing hot path for a subsystem nobody is currently watching.
    if _broadcast_hook is None or not _broadcast_hook.has_observers():
        return
    # ADR-020: every call site of _broadcast() runs inside the same
    # asyncio Task that process_message() bound a trace_id into, so this
    # is always the current turn's trace_id (or None outside any turn).
    trace_id = trace.current_trace_id()
    try:
        content = json.dumps({**payload, "trace_id": trace_id})
        await adb.save_event(event_type, content, trace_id=trace_id)
        await _broadcast_hook.broadcast_observers({"type": event_type, "timestamp": db.utcnow(), "content": content})
    except Exception as e:
        logger.warning("%s broadcast failed (state unaffected): %s", event_type, e)


async def _broadcast_phone(payload: dict) -> None:
    """Phone-facing broadcast for thinking_update frames during tool execution.

    No DB write, no observer gating — this sends directly to every connected
    client via the general broadcast() fan-out, so the phone sees tool-call
    transparency in real time."""
    if _broadcast_hook is None:
        return
    try:
        await _broadcast_hook.broadcast(payload)
    except Exception as e:
        logger.warning("thinking_update broadcast failed (state unaffected): %s", e)

SYSTEM_PROMPT = """You are Jarvis, a laptop-resident supervisor agent. You help the user supervise tasks running on their laptop.

You have access to a set of bounded tools. Use them to answer the user's questions and execute their requests.

## Rules
1. NEVER invent filesystem paths. Use project aliases with start_opencode_task.
2. NEVER execute shell commands or access arbitrary URLs.
3. If you are unsure which task or question the user means, ask for clarification.
4. Be concise. Respond in 1-3 sentences unless the user asks for detail.
5. Report tool failures honestly — do not pretend a failed action succeeded.
6. For status questions, use the get_attention or list_tasks tools first.
7. When the user says "Answer B" and there is exactly one pending question, use answer_question.
8. When the user says "Approve it" or "Reject it", use resolve_permission with the appropriate decision.
9. When the user says "Stop it" and there is exactly one cancellable task, use cancel_task.
10. When the user provides a follow-up instruction about an active task, use send_opencode_instruction.
11. Your responses are spoken aloud by text-to-speech, never displayed as formatted text. Never use markdown (no **bold**, no #headings, no bullet lists, no code fences) — plain spoken sentences only.
12. When the user asks what a completed or failed task found, did, or reported (e.g. "what did it find", "list the modified files", "what changed"), use get_task_result — never start a new task to re-answer a question about one that already finished.
13. When the user says things like "what happened", "catch me up", "what did I miss", "status update", or "while I was gone/away", use catch_me_up — not recent_activity or list_tasks, which only show current state, not a summary of what occurred.

## Single Action vs. Multi-Step Plan

Most requests are a single action — call the one relevant tool directly (e.g. start_opencode_task) and do not create a plan. Only use create_plan when the user asks for multiple actions that must happen in a specific order.

Decomposing a plan: each step becomes one item in create_plan's `steps` list, with `worker_name` set to the worker that should run it ("opencode" for anything involving code, tests, git, or the filesystem; "strategist" for advice/review). `on_failure` controls what happens when a step technically fails (crashes, errors, times out) — it does NOT let you branch on what a step's result *says*. Default `on_failure` is "stop": if the step fails, the whole plan stops there and later steps never run. Use "escalate" only for steps whose failure needs the user's decision before anything later can proceed; use "continue" only when a step's failure genuinely shouldn't block the rest.

- **Sequential, no conditions** ("run the tests, then lint it"): a plan with one step per action, default on_failure=stop is correct — if a step errors, later steps shouldn't run anyway.
- **Sequential with a real precondition** ("run the tests, then lint it, then push if everything passes"): a 3-step plan. Leave on_failure=stop (the default) on every step — "push if clean" IS "stop the plan if an earlier step fails," which is exactly what on_failure=stop already does. Do not invent a different mechanism for this.
- **Branching on content, not on failure** ("check the CI status, and if it's red, run the tests locally to see what's failing"): this is NOT expressible as a create_plan with on_failure, because checking CI status succeeds (technically) whether CI is red or green — a technical-failure gate will never trigger just because the reported status is bad. Do not build a plan for this. Instead, either fold the condition into a single worker instruction (e.g. one start_opencode_task instruction: "check CI status for the develop branch; if it's red, also run the tests locally and report what's failing"), or make the first tool call yourself, look at its actual result, and decide the next tool call as an ordinary follow-up in the same turn.

**Every step's `description` must be imperative and self-contained — never phrase it as a condition on an earlier step** (e.g. never write "if all tests and linting passed, commit and push changes" for a step whose worker will run in isolation). By the time a later step runs, `on_failure=stop` has already guaranteed every earlier step succeeded — the worker executing this step has no visibility into earlier steps' results and cannot evaluate a condition referring to them, so embedding one just wastes the worker's time re-checking something already guaranteed, or worse, lets it decide the condition itself. Write "commit and push the changes," not "if everything passed, commit and push the changes" — the gate belongs in `on_failure`, never repeated in the step text.

Examples:
- "Run the tests on Jarvis" → single action. Call start_opencode_task directly. No plan.
- "Run the tests, then lint it, then push if everything passes" → create_plan with 3 steps (worker_name="opencode" for all three; on_failure left at the default "stop" for each), then start_plan.
- "Check the CI status, and if it's red, run the tests locally" → not a plan. Either one start_opencode_task instruction covering both parts, or check first and decide the next tool call yourself based on the actual result.

## Available Tools
Use the provided function definitions to interact with Jarvis services.
"""


class Supervisor:
    """Main supervisor orchestrator."""

    def __init__(self, task_manager: Any = None, opencode_supervisor: Any = None, connection_manager: Any = None, worker_registry: Any = None, plan_executor: Any = None) -> None:
        self.tm = task_manager
        self.oc = opencode_supervisor
        self.worker_registry = worker_registry
        self.tools = ToolRegistry(task_manager, opencode_supervisor, connection_manager, worker_registry, plan_executor)
        self._llm: LLMProvider | FakeLLMProvider | None = None
        self._configure_llm()

    def set_voice_session_manager(self, vsm: Any) -> None:
        """Forward late-bound voice session manager injection to the tool
        registry. See ToolRegistry.set_voice_session_manager() for why this
        cannot be constructor-injected."""
        self.tools.set_voice_session_manager(vsm)

    def _configure_llm(self) -> None:
        if config.llm_api_key():
            self._llm = LLMProvider()
        elif config.test_mode() or not _supervisor_enabled():
            self._llm = FakeLLMProvider()
        else:
            self._llm = FakeLLMProvider()

    @property
    def llm(self) -> LLMProvider | FakeLLMProvider | None:
        return self._llm

    async def process_message(
        self, user_message: str, conversation_id: str | None = None,
        bound_attention_request_id: str | None = None,
        confirm_before_tools: frozenset[str] | None = None,
    ) -> dict:
        """Public entry point -- see _process_message_inner for the actual
        logic. This thin wrapper only maintains the Control Center's
        active-turn counter (Milestone 9B.10, additive): try/finally here,
        once, is simpler and safer than threading an increment/decrement
        through every one of _process_message_inner's several early-return
        branches, and correctly still decrements even if something in
        there raises past its own exception handling.

        confirm_before_tools (Interaction Layer v1, Goal 5): tool names
        that must be read back to the user for confirmation before
        executing, instead of running immediately when the LLM proposes
        them. Only VoiceSessionManager ever passes this (voice-specific —
        speech recognition can mis-hear technical identifiers in a way
        typed text cannot); every other/existing caller passes None,
        unchanged behavior."""
        global _active_turns
        _active_turns += 1
        # ADR-020: one trace_id per turn (per unit of work), bound into a
        # ContextVar for the duration of this asyncio Task so every layer
        # below -- tool persistence, OpenCode task creation -- can read it
        # via trace.current_trace_id() without threading a new parameter
        # through ToolRegistry.call() and every tool handler signature.
        trace_id = trace.new_trace_id()
        token = trace.bind_trace_id(trace_id)
        try:
            result = await self._process_message_inner(
                user_message, conversation_id, bound_attention_request_id,
                confirm_before_tools,
            )
            result["trace_id"] = trace_id
            return result
        finally:
            trace.reset_trace_id(token)
            _active_turns -= 1

    async def _process_message_inner(
        self, user_message: str, conversation_id: str | None = None,
        bound_attention_request_id: str | None = None,
        confirm_before_tools: frozenset[str] | None = None,
    ) -> dict:
        """Process a user message through the supervisor.

        `bound_attention_request_id` (Milestone 8 Phase 12): set only when
        this message came from a voice session opened *from* a specific
        AttentionRequest (e.g. tapping its mic). Deterministic
        answer/approve/reject/stop/defer resolution then targets that
        exact source directly — no global ambiguity resolution against
        unrelated attention items. Never overrides safety: if the bound
        request is already resolved/cancelled, that's reported instead of
        acting on a stale source.

        Returns dict with:
          - "response": the assistant's conversational response text
          - "conversation_id": the conversation ID
        """
        if not _supervisor_enabled():
            return {
                "response": "The conversational supervisor is disabled. Use slash commands instead.",
                "conversation_id": conversation_id or "",
            }

        if not conversation_id:
            conversation_id = f"conv_{uuid.uuid4().hex[:12]}"

        # Control Center dashboard support (additive): "a turn started" /
        # "a turn completed" bracket every path through this method
        # (fast path, deferral, bound command, deterministic command, or
        # the full LLM tool-calling loop) with exactly the observable
        # facts already computed at each return point below — never the
        # LLM's reasoning, which this module doesn't retain anywhere.
        await _broadcast("supervisor_turn_started", {
            "conversation_id": conversation_id,
            "user_message": user_message[:500],
            "bound_attention_request_id": bound_attention_request_id,
        })

        async def _emit_turn(response_text: str, tool_call_count: int = 0) -> dict:
            await _broadcast("supervisor_turn", {
                "conversation_id": conversation_id,
                "user_message": user_message[:500],
                "response": response_text[:1000],
                "tool_call_count": tool_call_count,
            })
            return {"response": response_text, "conversation_id": conversation_id}

        # Fast path for deterministic status questions
        fast = await asyncio.to_thread(_fast_path, user_message)
        if fast:
            await asyncio.to_thread(_persist_conversation, conversation_id, "user", user_message)
            await self._persist_conversation_turn(conversation_id, fast)
            return await _emit_turn(fast)

        # Milestone 8 Phase 18: deterministic deferral phrases ("Come back
        # in 15 minutes", "remind me tomorrow morning") are checked before
        # both the answer/approve/stop grammar and the LLM — a defer
        # intent is syntactically distinctive enough that checking it
        # first cannot plausibly misfire against an unrelated answer.
        deferred = await _resolve_defer_command(user_message, bound_attention_request_id)
        if deferred:
            await asyncio.to_thread(_persist_conversation, conversation_id, "user", user_message)
            await self._persist_conversation_turn(conversation_id, deferred)
            return await _emit_turn(deferred)

        # Milestone 8 Phase 12: when this turn came from a voice session
        # bound to a specific AttentionRequest, resolve answer/approve/
        # reject/stop against *that exact source* — no ambiguity check
        # against unrelated pending items, but also never act on a stale
        # (already resolved/cancelled) source.
        if bound_attention_request_id:
            bound = await _resolve_bound_command(user_message, self.tools, bound_attention_request_id)
            if bound:
                await asyncio.to_thread(_persist_conversation, conversation_id, "user", user_message)
                await self._persist_conversation_turn(conversation_id, bound)
                return await _emit_turn(bound)

        # Milestone 7 Phase 10: deterministic voice/text command resolution
        # for "Answer B", "Approve it", "Reject it", "Stop it" and close
        # variants. Same code path for voice transcripts and typed text —
        # voice is just another input transport (Milestone 7 principle 1).
        # Never guesses under ambiguity; see _resolve_deterministic_command.
        deterministic = await _resolve_deterministic_command(user_message, self.tools)
        if deterministic:
            await asyncio.to_thread(_persist_conversation, conversation_id, "user", user_message)
            await self._persist_conversation_turn(conversation_id, deterministic)
            return await _emit_turn(deterministic)

        # Load conversation history
        history = await asyncio.to_thread(_load_conversation, conversation_id, max_turns=10)

        # Build context
        context = await asyncio.to_thread(build_context, history, user_message)

        # Build LLM messages: system -> history -> context -> current turn.
        # TD-028 / MILESTONE_F1 F1.6: the current message used to be embedded in the
        # context message AND appended again, and it preceded the history.
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # History first, so the model sees the conversation that led to this turn.
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})

        # Then the situational context, then the actual current turn.
        messages.append({"role": "user", "content": _format_context(context)})
        messages.append({"role": "user", "content": user_message})

        tool_defs = self._build_tool_definitions()

        # Main tool-call loop
        tool_call_count = 0
        final_content = None
        # Interaction Layer v1 (Goal 5): set when the LLM proposes a tool
        # in confirm_before_tools -- the loop stops *before* calling it,
        # so nothing executes until the user confirms on the next turn
        # (see VoiceSessionManager's STATE_CONFIRMING handling, the only
        # caller that ever passes a non-empty confirm_before_tools; text/
        # browser chat callers pass none and this is always None for them,
        # unchanged behavior).
        pending_confirmation = None

        try:
            while tool_call_count < MAX_TOOL_CALLS:
                llm_response = await self._llm.chat_completion(messages, tools=tool_defs)  # type: ignore[union-attr]  # _llm is set by _configure_llm() on all branches in __init__

                if llm_response.get("tool_calls"):
                    tool_call_count += 1
                    messages.append(llm_response)

                    for tc in llm_response["tool_calls"]:
                        func = tc["function"]
                        name = func["name"]
                        try:
                            args = json.loads(func["arguments"])
                        except json.JSONDecodeError:
                            args = {}

                        if confirm_before_tools and name in confirm_before_tools:
                            pending_confirmation = {"name": name, "args": args}
                            break

                        logger.info("Tool call #%d: %s(%s)", tool_call_count, name, func["arguments"][:100], extra={"conversation_id": conversation_id, "tool": name})

                        turn_trace_id = trace.current_trace_id()
                        await _broadcast_phone({
                            "type": "thinking_update",
                            "action": "tool_call",
                            "status": "started",
                            "summary": f"Calling {name}",
                            "detail": None,
                            "conversation_id": conversation_id,
                            "trace_id": turn_trace_id,
                            "timestamp": db.utcnow(),
                        })

                        result = await self.tools.call(name, args, conversation_id=conversation_id)
                        await asyncio.to_thread(_persist_tool_call, conversation_id, name, args, result)

                        result_str = result if isinstance(result, str) else str(result)
                        detail = result_str[:300]
                        if len(detail) > 200:
                            detail = detail[:200]
                        await _broadcast_phone({
                            "type": "thinking_update",
                            "action": "tool_call",
                            "status": "completed",
                            "summary": f"{name} finished",
                            "detail": detail,
                            "conversation_id": conversation_id,
                            "trace_id": turn_trace_id,
                            "timestamp": db.utcnow(),
                        })

                        await _broadcast("supervisor_tool_call", {
                            "conversation_id": conversation_id,
                            "sequence": tool_call_count,
                            "tool": name,
                            "args": args,
                            "result_summary": result[:300] if isinstance(result, str) else str(result)[:300],
                        })

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })

                    if pending_confirmation:
                        final_content = _build_confirmation_prompt(pending_confirmation["name"], pending_confirmation["args"])
                        break
                else:
                    final_content = llm_response.get("content") or "Done."
                    break
        except Exception:
            # A network failure calling the LLM here must never propagate
            # past this point: main.py's websocket loop has no per-message
            # exception handling, so an uncaught error here tears down the
            # entire connection (see Milestone 9B.10 RC finding — a real
            # ConnectTimeout to the LLM API killed the WS mid-tool-call,
            # even though a tool call already in flight had succeeded).
            logger.exception("LLM tool-call loop failed for conversation %s", conversation_id)
            final_content = "I ran into a problem reaching my reasoning engine. If I was already working on something, ask me for its status."

        if final_content is None:
            final_content = "I've reached the maximum number of actions I can take in one response. Please let me know what you'd like to do next."

        await asyncio.to_thread(_persist_conversation, conversation_id, "user", user_message)
        await self._persist_conversation_turn(conversation_id, final_content)

        turn_result = await _emit_turn(final_content, tool_call_count=tool_call_count)
        if pending_confirmation:
            turn_result["pending_tool_call"] = pending_confirmation
        return turn_result

    async def resolve_pending_tool_confirmation(
        self, conversation_id: str, transcript: str, pending_tool_call: dict,
    ) -> dict:
        """Interaction Layer v1 (Goal 5): resolves a yes/no reply to a
        confirmation prompt _build_confirmation_prompt generated. Called
        only by VoiceSessionManager while a session is in STATE_CONFIRMING
        — never re-enters the LLM tool loop, since the user is confirming
        exact arguments already proposed on a prior turn, not starting a
        new one. Mirrors process_message()'s persistence/broadcast/return
        shape (including an optional pending_tool_call in the result, for
        an "unclear" reply that re-asks rather than guessing) so the
        caller can treat both the same way."""
        await asyncio.to_thread(_persist_conversation, conversation_id, "user", transcript)
        decision = _classify_yes_no(transcript)
        name = pending_tool_call["name"]
        args = pending_tool_call["args"]

        if decision == "yes":
            logger.info(
                "Confirmed tool call: %s(%s)", name, json.dumps(args)[:100],
                extra={"conversation_id": conversation_id, "tool": name},
            )

            turn_trace_id = trace.current_trace_id()
            await _broadcast_phone({
                "type": "thinking_update",
                "action": "tool_call",
                "status": "started",
                "summary": f"Calling {name}",
                "detail": None,
                "conversation_id": conversation_id,
                "trace_id": turn_trace_id,
                "timestamp": db.utcnow(),
            })

            result = await self.tools.call(name, args, conversation_id=conversation_id)
            await asyncio.to_thread(_persist_tool_call, conversation_id, name, args, result)

            result_str = result if isinstance(result, str) else str(result)
            detail = result_str[:300]
            if len(detail) > 200:
                detail = detail[:200]
            await _broadcast_phone({
                "type": "thinking_update",
                "action": "tool_call",
                "status": "completed",
                "summary": f"{name} finished",
                "detail": detail,
                "conversation_id": conversation_id,
                "trace_id": turn_trace_id,
                "timestamp": db.utcnow(),
            })

            await _broadcast("supervisor_tool_call", {
                "conversation_id": conversation_id,
                "sequence": 1,
                "tool": name,
                "args": args,
                "result_summary": result[:300] if isinstance(result, str) else str(result)[:300],
            })
            response = result if isinstance(result, str) else str(result)
            await self._persist_conversation_turn(conversation_id, response)
            return {"response": response, "conversation_id": conversation_id}

        if decision == "no":
            response = "Okay, I won't do that. What would you like instead?"
            await self._persist_conversation_turn(conversation_id, response)
            return {"response": response, "conversation_id": conversation_id}

        response = "Sorry, was that a yes or a no?"
        await self._persist_conversation_turn(conversation_id, response)
        return {"response": response, "conversation_id": conversation_id, "pending_tool_call": pending_tool_call}

    def _build_tool_definitions(self) -> list[dict]:
        definitions = self.tools.list_definitions()
        result = []
        for d in definitions:
            result.append({
                "type": "function",
                "function": {
                    "name": d["name"],
                    "description": d["description"],
                    "parameters": d["parameters"],
                },
            })
        return result

    async def _persist_conversation_turn(self, conversation_id: str, content: str) -> None:
        await asyncio.to_thread(_persist_conversation, conversation_id, "assistant", content)
        # get_conversation_messages defaults to limit=100 (oldest-first) --
        # an explicit high limit is required here so the assistant-turn
        # count keeps growing instead of plateauing once a conversation
        # exceeds 100 total messages (which would silently stop the
        # every-10-turns summarization trigger forever for exactly the
        # long conversations that most need it).
        messages = await asyncio.to_thread(db.get_conversation_messages, conversation_id, 100_000)
        assistant_count = sum(1 for m in messages if m["role"] == "assistant")
        if assistant_count > 0 and assistant_count % 10 == 0:
            asyncio.create_task(self._summarize_and_store(conversation_id))

    async def _summarize_and_store(self, conversation_id: str) -> None:
        try:
            history = await asyncio.to_thread(_load_conversation, conversation_id, max_turns=10)
            prompt_lines = [
                "Summarize the following conversation between a user and Jarvis in 2-4 sentences. "
                "Focus on what was discussed and decided:"
            ]
            for msg in history:
                prompt_lines.append(f"{msg['role']}: {msg['content']}")
            prompt = "\n".join(prompt_lines)
            response = await self._llm.chat_completion(  # type: ignore[union-attr]  # _llm is set by _configure_llm() on all branches in __init__
                [{"role": "user", "content": prompt}],
                tools=None,
            )
            summary = response.get("content", "") if response else ""
            if summary:
                await adb.store_memory(
                    category="episodic",
                    content=summary,
                    source="conversation",
                    source_id=conversation_id,
                )
        except Exception:
            logger.warning("Summarization failed for conversation %s", conversation_id, exc_info=True)


# ── Fast path ─────────────────────────────────────────────────────

def _fast_path(msg: str) -> str | None:
    """Handle deterministic status questions without LLM."""
    lowered = msg.lower().strip().rstrip("?.,!")

    if lowered in ("what needs my attention", "what needs attention", "attention", "anything i need to look at"):
        # One coherent, deduplicated list — see ToolRegistry._get_attention
        # for why there is no separate "opencode tasks waiting" section.
        pending = db.get_pending_questions()
        if not pending:
            return "Nothing needs your attention right now."
        lines = [f"You have {len(pending)} item(s) needing attention:"]
        for q in pending:
            task = db.get_task(q["task_id"])
            tn = task["name"] if task else "Unknown"
            if q["question"].startswith("Permission:"):
                lines.append(f"  - {tn} needs permission: {q['question'][len('Permission:'):].strip()[:100]}")
            else:
                lines.append(f"  - {tn} is waiting for your answer: {q['question'][:100]}")
        return "\n".join(lines)

    if lowered in ("what tasks are running", "what is running", "show tasks", "list tasks", "tasks"):
        active = db.get_opencode_running_tasks()
        recent = db.get_recent_tasks(5)
        lines = []
        if active:
            lines.append(f"Active OpenCode tasks ({len(active)}):")
            for t in active:
                lines.append(f"  - {t['task_id']}: {(t.get('instruction') or '')[:60]} ({t['status']})")
        else:
            lines.append("No active OpenCode tasks.")
        recent_local = [t for t in recent if t["status"] in ("running", "waiting_for_user")]
        if recent_local:
            for t in recent_local:
                lines.append(f"  - {t['task_id']}: {t['name']} ({t['status']})")
        return "\n".join(lines)

    if lowered in ("what happened while i was away", "what happened", "recent activity", "whats new"):
        events = db.get_recent_events(10)
        lines = []
        for ev in events:
            ev_type = ev.get("type", "")
            if ev_type in (
                "task_started", "task_completed", "task_failed", "task_cancelled",
                "question_asked", "question_answered",
            ):
                ts = (ev.get("timestamp") or "")[11:19]
                content = ev.get("content", "")[:80]
                lines.append(f"  [{ts}] {ev_type}: {content}")
        if not lines:
            return "Nothing notable happened while you were away."
        return "\n".join(lines)

    return None


# ── Milestone 7 Phase 10: deterministic voice/text command grammar ─
#
# Kept separate from _fast_path (above) rather than merged into it, because
# these need to actually mutate state via the tool registry (answer a
# question, resolve a permission, cancel a task) and therefore must be
# async — _fast_path is a synchronous pure function relied on by many
# existing tests and left untouched.

_ANSWER_RE = re.compile(r"^(?:answer|the answer is|my answer is)[:\s]+(.+)$")
_PERMISSION_RE = re.compile(r"^(approve|reject|deny)(?: it)?$")
_STOP_RE = re.compile(r"^(?:stop|cancel)(?: it| the task)?$")


def _cancellable_tasks() -> list[dict]:
    """Local and OpenCode tasks are both rows in the `tasks` table with a
    shared status field (OpenCode's start_session() calls
    create_task_record() too) — one query covers both task kinds."""
    return [t for t in db.get_recent_tasks(50) if t["status"] in ("running", "waiting_for_user")]


def _match_pending_option(cleaned: str, pending_question: dict) -> str | None:
    """Real-phone finding (Milestone 7 Phase 18, 2026-07-09): a spoken
    answer rarely says the literal word "answer" — real transcripts looked
    like "approach a" or "let's go with B", which _ANSWER_RE never matches,
    silently falling through to the LLM with no idea a question was even
    pending. Still fully deterministic: if exactly one of the pending
    question's own options appears as a distinct word in the message,
    that's the answer. Zero or more than one matching option defers
    (returns None) rather than guessing."""
    try:
        options = json.loads(pending_question.get("options_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        options = []
    if not options:
        return None
    words = set(re.findall(r"[a-z0-9']+", cleaned.lower()))
    matched = [opt for opt in options if str(opt).strip().lower() in words]
    return str(matched[0]) if len(matched) == 1 else None


# ── Milestone 8 Phase 18: deterministic deferral fast path ──────────

def _describe_relative(deferred_until_iso: str) -> str:
    """Only ever used in a confirmation message, only after defer state
    has already been successfully persisted (Phase 7/18)."""
    from datetime import datetime, timezone as _tz

    target = datetime.fromisoformat(deferred_until_iso.replace("Z", "+00:00"))
    now = datetime.now(_tz.utc)
    minutes = int((target - now).total_seconds() // 60)
    if minutes <= 0:
        return "shortly"
    if minutes < 60:
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    if hours < 20:
        return f"in about {hours} hour{'s' if hours != 1 else ''}"
    local_target = target.astimezone()
    return "around " + local_target.strftime("%A %I:%M %p").lstrip("0")


async def _resolve_defer_command(msg: str, bound_attention_request_id: str | None) -> str | None:
    """Milestone 8 Phase 18. If bound to a specific AttentionRequest
    (Phase 12), defers exactly that one. Otherwise: exactly one active
    (pending/contacting) AttentionRequest -> defer it; zero -> defer
    entirely to ordinary conversation (returns None, matching the same
    "nothing to guess about" philosophy as Phase 10's answer/stop paths);
    more than one -> ask which, never guess."""
    result = deferral.parse_defer_phrase(msg)
    if result.kind == "none":
        return None

    if bound_attention_request_id:
        row = await adb.get_attention_request(bound_attention_request_id)
        if not row or row["status"] in ("resolved", "cancelled", "expired"):
            return "That item is already resolved — there's nothing to come back to."
        target_id = bound_attention_request_id
    else:
        candidates = [r for r in await adb.get_unresolved_attention_requests() if r["status"] in ("pending", "contacting")]
        if not candidates:
            return None
        if len(candidates) > 1:
            return f"I have {len(candidates)} things needing attention — which one should I come back to later?"
        target_id = candidates[0]["attention_request_id"]

    if result.kind == "vague":
        return result.message

    deferred_until = result.deferred_until
    assert deferred_until is not None  # guaranteed: kind="resolved" always has deferred_until set (deferral.py)
    ok = await attention_manager.defer(target_id, deferred_until)
    if not ok:
        return "I couldn't defer that — it may have already been resolved."
    return f"Okay. I'll come back to this {_describe_relative(deferred_until)}."


async def _resolve_bound_command(msg: str, tools: ToolRegistry, attention_request_id: str) -> str | None:
    """Milestone 8 Phase 12: same grammar as _resolve_deterministic_command,
    but always targets the bound AttentionRequest's exact source — never
    counts pending items, never asks "which one". If the bound request is
    already resolved/cancelled/expired, reports that instead of acting on
    a stale source (safety always wins over context binding)."""
    row = await adb.get_attention_request(attention_request_id)
    if not row:
        return None
    if row["status"] in ("resolved", "cancelled", "expired"):
        return f"That item is already {row['status']} — nothing more to do there."

    cleaned = msg.strip().rstrip(".!?")
    lowered = cleaned.lower()
    source_id = row["source_id"]

    if row["attention_type"] == "QUESTION":
        answer_text = None
        m = _ANSWER_RE.match(lowered)
        if m:
            answer_text = cleaned[m.start(1):].strip()
        else:
            question = await adb.get_question_record(source_id)
            if question:
                answer_text = _match_pending_option(cleaned, question)
        if answer_text:
            result = await tools.call("answer_question", {"question_id": source_id, "answer": answer_text})
            if result.startswith("Error"):
                return f"I couldn't deliver that answer: {result}"
            return f'Delivered your answer: "{answer_text}"'

    if row["attention_type"] == "PERMISSION":
        m = _PERMISSION_RE.match(lowered)
        if m:
            decision = "reject" if m.group(1) in ("reject", "deny") else "approve"
            result = await tools.call("resolve_permission", {"permission_id": source_id, "decision": decision})
            if result.startswith("Error"):
                return f"I couldn't {decision} that: {result}"
            return "Permission approved." if decision == "approve" else "Permission rejected."

    if _STOP_RE.match(lowered) and row.get("task_id"):
        result = await tools.call("cancel_task", {"task_id": row["task_id"]})
        if result.startswith("Error"):
            return f"I couldn't stop that task: {result}"
        return "Task stopped."

    return None


async def _resolve_deterministic_command(msg: str, tools: ToolRegistry) -> str | None:
    """Deterministic, LLM-free resolution for "Answer B", "Approve it",
    "Reject it", "Stop it" (and close variants).

    If there is exactly one relevant pending item, resolve it and confirm
    only after the underlying tool call succeeds. If there is more than
    one, ask which and modify nothing. If there are zero, this function
    defers entirely (returns None) so the message falls through to the LLM
    as ordinary conversation — this is what makes "answer the question" or
    "stop the task" with nothing actually pending behave exactly as before.
    """
    cleaned = msg.strip().rstrip(".!?")
    lowered = cleaned.lower()

    m = _ANSWER_RE.match(lowered)
    if m:
        pending = [q for q in await adb.get_pending_questions() if not q["question"].startswith("Permission:")]
        if not pending:
            return None
        if len(pending) > 1:
            return f"I have {len(pending)} pending questions — which task did you mean?"
        answer_text = cleaned[m.start(1):].strip()
        if not answer_text:
            return None
        question_id = pending[0]["question_id"]
        result = await tools.call("answer_question", {"question_id": question_id, "answer": answer_text})
        if result.startswith("Error"):
            return f"I couldn't deliver that answer: {result}"
        return f'Delivered your answer: "{answer_text}"'

    # No literal "answer ..." prefix — try matching one of the pending
    # question's own options as a natural-language fallback (see
    # _match_pending_option). Only when exactly one question is pending;
    # multiple pending questions still means "ask which", not a guess.
    pending_for_option_match = [q for q in await adb.get_pending_questions() if not q["question"].startswith("Permission:")]
    if len(pending_for_option_match) == 1:
        matched_option = _match_pending_option(cleaned, pending_for_option_match[0])
        if matched_option:
            question_id = pending_for_option_match[0]["question_id"]
            result = await tools.call("answer_question", {"question_id": question_id, "answer": matched_option})
            if result.startswith("Error"):
                return f"I couldn't deliver that answer: {result}"
            return f'Delivered your answer: "{matched_option}"'

    m = _PERMISSION_RE.match(lowered)
    if m:
        decision = "reject" if m.group(1) in ("reject", "deny") else "approve"
        pending = [q for q in await adb.get_pending_questions() if q["question"].startswith("Permission:")]
        if not pending:
            return None
        if len(pending) > 1:
            return f"I have {len(pending)} pending permission requests — which one did you mean?"
        permission_id = pending[0]["question_id"]
        result = await tools.call("resolve_permission", {"permission_id": permission_id, "decision": decision})
        if result.startswith("Error"):
            return f"I couldn't {decision} that: {result}"
        return "Permission approved." if decision == "approve" else "Permission rejected."

    if _STOP_RE.match(lowered):
        cancellable = await asyncio.to_thread(_cancellable_tasks)
        if not cancellable:
            return None
        if len(cancellable) > 1:
            return f"I have {len(cancellable)} active tasks — which one should I stop?"
        task_id = cancellable[0]["task_id"]
        result = await tools.call("cancel_task", {"task_id": task_id})
        if result.startswith("Error"):
            return f"I couldn't stop that task: {result}"
        return "Task stopped."

    return None


# ── Conversation persistence ──────────────────────────────────────

# Interaction Layer v1 (Goal 5): a small, deterministic yes/no grammar —
# matches this module's existing no-guessing-under-ambiguity philosophy
# (_resolve_deterministic_command above). Never inferred from arbitrary
# phrasing — an unmatched (or self-contradictory) reply is "unclear", not
# silently treated as either answer.
#
# Word/phrase-boundary matching, not prefix-only: a real-device finding
# during capability testing (Level 5) showed a natural correction —
# "that was a no, I need you to..." — silently fell through as "unclear"
# because the leading-prefix check only ever looked at the first word.
# People embed "yes"/"no" naturally anywhere in a sentence ("no, that's
# not what I said"), especially when correcting a misunderstanding; the
# matcher has to find the word, not just the start of the string. Single
# words are matched on token boundaries (so "know" never matches "no");
# multi-word phrases are matched as substrings of the punctuation-
# stripped text, since they can't be tokens themselves.
_YES_TOKENS = frozenset({"yes", "yeah", "yep", "yup", "correct", "right", "confirm", "confirmed", "sure", "affirmative"})
_NO_TOKENS = frozenset({"no", "nope", "negative", "wrong", "incorrect", "cancel", "don't", "stop"})
_YES_PHRASES = ("go ahead", "do it")
_NO_PHRASES = ("never mind",)


def _classify_yes_no(transcript: str) -> str:
    cleaned = transcript.strip().lower()
    tokens = set(re.findall(r"[a-z']+", cleaned))

    is_yes = bool(tokens & _YES_TOKENS) or any(p in cleaned for p in _YES_PHRASES)
    is_no = bool(tokens & _NO_TOKENS) or any(p in cleaned for p in _NO_PHRASES)

    if is_yes and is_no:
        return "unclear"  # e.g. "no wait yes" -- genuinely ambiguous, never guess
    if is_yes:
        return "yes"
    if is_no:
        return "no"
    return "unclear"


def _build_confirmation_prompt(name: str, args: dict) -> str:
    """Interaction Layer v1 (Goal 5): read back a technical delegation
    instruction before executing it. Speech recognition can silently
    mis-hear filenames/extensions/technical identifiers — a real, live
    finding during capability testing: "level3_probe.txt" was transcribed
    as "level 3_probe.text" and executed exactly as heard, with no chance
    to catch it. Scoped to start_opencode_task only (per the milestone's
    own scope): the one tool whose arguments are free-form technical text
    with a real filesystem side effect — other tools take small enums/IDs
    the deterministic fast paths or the LLM already resolve safely."""
    if name == "start_opencode_task":
        instruction = args.get("instruction", "")
        project = args.get("project_alias", "the project")
        return f"I heard: {instruction} — for the {project} project. Should I go ahead?"
    return "Should I go ahead with that?"


def _persist_conversation(conversation_id: str, role: str, content: str) -> None:
    """Store a conversation message. trace_id (ADR-020) is read from the
    ContextVar rather than threaded as a parameter -- every call site sits
    inside the same asyncio Task process_message() bound it into."""
    db.save_conversation_message(conversation_id, role, content, trace_id=trace.current_trace_id())


def _persist_tool_call(conversation_id: str, tool_name: str, args: dict, result: str) -> None:
    """Store a tool call as a tool-role message."""
    content = json.dumps({"tool": tool_name, "args": args, "result": result[:500]})
    db.save_conversation_message(conversation_id, "tool", content, trace_id=trace.current_trace_id())


def _load_conversation(conversation_id: str, max_turns: int = 10) -> list[dict]:
    """Load recent conversation messages, excluding tool messages."""
    messages = db.get_conversation_messages(conversation_id, limit=50)
    # Only include user and assistant messages in context
    filtered = [m for m in messages if m["role"] in ("user", "assistant")]
    return filtered[-max_turns:]


# ── Context formatting ────────────────────────────────────────────

def _format_context(context: dict) -> str:
    """Format the context dict into a readable string for the LLM."""
    parts = [f"Current time: {context.get('current_time', 'unknown')}"]

    memory_block = format_memory_sections(context)
    if memory_block:
        parts.append(memory_block)

    projects = context.get("projects", [])
    if projects:
        parts.append("Safe projects: " + ", ".join(f"{p['alias']} ({p['display_name']})" for p in projects))

    active = context.get("active_tasks", [])
    if active:
        parts.append("Active tasks:")
        for t in active:
            parts.append(f"  - {t['id']}: {t['name']} [{t['status']}] ({t['type']})")

    pending = context.get("pending_questions", [])
    if pending:
        parts.append("Pending questions:")
        for q in pending:
            parts.append(f"  - [{q['id']}] task={q['task_id']}: {q['question']}")

    perms = context.get("pending_permissions", [])
    if perms:
        parts.append("Pending permissions:")
        for p in perms:
            parts.append(f"  - [{p['id']}] task={p['task_id']}: {p['text']}")

    oc_tasks = context.get("opencode_running", [])
    if oc_tasks:
        parts.append("Running OpenCode tasks:")
        for t in oc_tasks:
            parts.append(f"  - {t['id']}: {t['instruction'][:80]} ({t['status']})")

    activity = context.get("recent_activity", [])
    if activity:
        parts.append(f"Recent activity ({len(activity)} events):")
        for ev in activity:
            parts.append(f"  [{ev['time']}] {ev['type']}: {ev['summary'][:120]}")

    return "\n".join(parts)
