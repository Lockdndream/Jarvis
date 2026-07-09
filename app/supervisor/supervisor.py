"""Supervisor orchestrator: receives user messages, builds context,
calls LLM, executes tools, and returns conversational responses.

Max 5 tool calls per user turn. No infinite loops.
"""
import json
import logging
import os
import re
import uuid

import app.database as db
from app.supervisor.tools import ToolRegistry
from app.supervisor.llm import LLMProvider, FakeLLMProvider
from app.supervisor.context import build_context

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 5
def _supervisor_enabled():
    return os.environ.get("JARVIS_SUPERVISOR_ENABLED", "1") == "1"

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

## Available Tools
Use the provided function definitions to interact with Jarvis services.
"""


class Supervisor:
    """Main supervisor orchestrator."""

    def __init__(self, task_manager=None, opencode_supervisor=None):
        self.tm = task_manager
        self.oc = opencode_supervisor
        self.tools = ToolRegistry(task_manager, opencode_supervisor)
        self._llm = None
        self._configure_llm()

    def _configure_llm(self):
        if os.environ.get("JARVIS_LLM_API_KEY"):
            self._llm = LLMProvider()
        elif os.environ.get("JARVIS_TEST_MODE") == "1" or not _supervisor_enabled():
            self._llm = FakeLLMProvider()
        else:
            self._llm = FakeLLMProvider()

    @property
    def llm(self):
        return self._llm

    async def process_message(self, user_message: str, conversation_id: str | None = None) -> dict:
        """Process a user message through the supervisor.

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

        # Fast path for deterministic status questions
        fast = _fast_path(user_message)
        if fast:
            _persist_conversation(conversation_id, "user", user_message)
            _persist_conversation(conversation_id, "assistant", fast)
            return {"response": fast, "conversation_id": conversation_id}

        # Milestone 7 Phase 10: deterministic voice/text command resolution
        # for "Answer B", "Approve it", "Reject it", "Stop it" and close
        # variants. Same code path for voice transcripts and typed text —
        # voice is just another input transport (Milestone 7 principle 1).
        # Never guesses under ambiguity; see _resolve_deterministic_command.
        deterministic = await _resolve_deterministic_command(user_message, self.tools)
        if deterministic:
            _persist_conversation(conversation_id, "user", user_message)
            _persist_conversation(conversation_id, "assistant", deterministic)
            return {"response": deterministic, "conversation_id": conversation_id}

        # Load conversation history
        history = _load_conversation(conversation_id, max_turns=10)

        # Build context
        context = build_context(history)

        # Build LLM messages
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _format_context(context) + "\n\nUser: " + user_message},
        ]

        # Add history
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})

        messages.append({"role": "user", "content": user_message})

        tool_defs = self._build_tool_definitions()

        # Main tool-call loop
        tool_call_count = 0
        final_content = None

        while tool_call_count < MAX_TOOL_CALLS:
            llm_response = await self._llm.chat_completion(messages, tools=tool_defs)

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

                    logger.info("Tool call #%d: %s(%s)", tool_call_count, name, func["arguments"][:100])
                    result = await self.tools.call(name, args)
                    _persist_tool_call(conversation_id, name, args, result)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
            else:
                final_content = llm_response.get("content") or "Done."
                break

        if final_content is None:
            final_content = "I've reached the maximum number of actions I can take in one response. Please let me know what you'd like to do next."

        _persist_conversation(conversation_id, "user", user_message)
        _persist_conversation(conversation_id, "assistant", final_content)

        return {"response": final_content, "conversation_id": conversation_id}

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


async def _resolve_deterministic_command(msg: str, tools) -> str | None:
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
        pending = [q for q in db.get_pending_questions() if not q["question"].startswith("Permission:")]
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
    pending_for_option_match = [q for q in db.get_pending_questions() if not q["question"].startswith("Permission:")]
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
        pending = [q for q in db.get_pending_questions() if q["question"].startswith("Permission:")]
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
        cancellable = _cancellable_tasks()
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

def _persist_conversation(conversation_id: str, role: str, content: str):
    """Store a conversation message."""
    db.save_conversation_message(conversation_id, role, content)


def _persist_tool_call(conversation_id: str, tool_name: str, args: dict, result: str):
    """Store a tool call as a tool-role message."""
    content = json.dumps({"tool": tool_name, "args": args, "result": result[:500]})
    db.save_conversation_message(conversation_id, "tool", content)


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
