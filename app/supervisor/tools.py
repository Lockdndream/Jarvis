"""Bounded supervisor tool registry for Jarvis.

Tools are async methods registered with name, description, JSON schema.
The registry is populated by the Supervisor at init time with references
to existing service layers (TaskManager, OpenCodeSupervisor).
"""
import asyncio
import json
import logging
from typing import Any

from app import db_async as adb
from app.supervisor.projects import resolve_project, get_projects

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of bounded supervisor tools backed by service references."""

    def __init__(
        self,
        task_manager: Any = None,
        opencode_supervisor: Any = None,
        connection_manager: Any = None,
    ) -> None:
        self._tm = task_manager
        self._oc = opencode_supervisor
        self._cm = connection_manager
        self._vsm: Any = None
        self._tools: dict[str, dict] = {}
        self._register_all()

    def set_voice_session_manager(self, vsm: Any) -> None:
        """Late-bound injection. VoiceSessionManager is constructed from the
        Supervisor (app/main.py), which already owns this ToolRegistry, so it
        cannot be passed to __init__ -- the object does not exist yet. Setter
        injection replaces the previous function-level import of the
        voice_session_manager singleton inside the tool handlers (ADR-005 /
        F1.10): the tool layer must not import the composition root."""
        self._vsm = vsm

    def _register(self, name: str, description: str, parameters: dict, handler: Any) -> None:
        self._tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def get(self, name: str) -> dict | None:
        return self._tools.get(name)

    def list_definitions(self) -> list[dict]:
        return [
            {"name": n, "description": t["description"], "parameters": t["parameters"]}
            for n, t in self._tools.items()
        ]

    async def call(self, name: str, args: dict) -> str:
        tool = self.get(name)
        if not tool:
            return f"Error: unknown tool '{name}'"
        try:
            return await tool["handler"](**args)
        except TypeError as e:
            return f"Error: invalid arguments for '{name}': {e}"
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return f"Error executing '{name}': {e}"

    # ── Tool implementations ─────────────────────────────────────

    # task_id/question_id are exact-match DB keys the LLM must echo back in
    # later tool calls (send_opencode_instruction, cancel_task, answer_question,
    # ...). Never truncate them in tool output, only free-text fields.
    async def _get_attention(self) -> str:
        """One coherent, deduplicated attention list.

        A task needing attention has exactly one reason: a pending native
        question, or a pending native permission request. Both are stored
        as rows in the `questions` table (permissions prefixed "Permission:"
        — see M4 known limitation on DB conflation), which is the single
        source of truth here. There is deliberately no second "tasks
        waiting for user" section derived from opencode_tasks.status — that
        status is set by the exact same code path that creates the question
        row, so listing both would just show the same item twice
        (Milestone 6 Phase 8 fix).
        """
        pending_qs = await adb.get_pending_questions()
        if not pending_qs:
            return "Nothing needs your attention right now."

        lines = [f"{len(pending_qs)} item(s) need your attention:"]
        for q in pending_qs:
            task = await adb.get_task(q["task_id"])
            tn = task["name"] if task else "Unknown"
            src = " (OpenCode)" if await adb.get_opencode_task(q["task_id"]) else ""
            if q["question"].startswith("Permission:"):
                detail = q["question"][len("Permission:"):].strip()
                lines.append(f"  - [{q['question_id']}] {tn}{src} needs permission: {detail[:120]}")
            else:
                lines.append(f"  - [{q['question_id']}] {tn}{src} is waiting for your answer: {q['question'][:120]}")
        return "\n".join(lines)

    async def _list_tasks(self) -> str:
        active = await adb.get_opencode_running_tasks()
        recent = await adb.get_recent_tasks(10)
        lines = ["Active OpenCode Tasks:"]
        if active:
            for t in active:
                lines.append(f"  - {t['task_id']}: {t.get('instruction','')[:60]} ({t['status']})")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Recent Tasks:")
        for t in recent:
            lines.append(f"  - {t['task_id']}: {t['name']} ({t['status']})")
        return "\n".join(lines)

    async def _get_task_status(self, task_id: str) -> str:
        task = await adb.get_task(task_id)
        if not task:
            return f"Task '{task_id}' not found"
        lines = [f"Task: {task['name']}", f"Status: {task['status']}", f"Started: {task['started_at']}"]
        if task.get("completed_at"):
            lines.append(f"Completed: {task['completed_at']}")
        if task.get("exit_code") is not None:
            lines.append(f"Exit code: {task['exit_code']}")
        oc_task = await adb.get_opencode_task(task_id)
        if oc_task:
            lines.append(f"OpenCode session: {oc_task['session_id'][:20]}")
        pending_qs = await adb.get_pending_questions()
        for q in pending_qs:
            if q["task_id"] == task_id:
                lines.append(f"Pending question: {q['question'][:100]} (id={q['question_id']})")
        return "\n".join(lines)

    async def _start_opencode_task(self, project_alias: str, instruction: str) -> str:
        if not self._oc:
            return "Error: OpenCode supervisor not available"
        alias, path = resolve_project(project_alias)
        if not alias:
            return f"Cannot start task: {path}"
        try:
            result = await self._oc.start_session(path, instruction)
            return f"Task started: {result['task_id']} (session {result['session_id'][:20]}). Instruction: {instruction[:100]}"
        except Exception as e:
            return f"Error starting task: {e}"

    async def _send_opencode_instruction(self, task_id: str, instruction: str) -> str:
        if not self._oc:
            return "Error: OpenCode supervisor not available"
        oc_task = await adb.get_opencode_task(task_id)
        if not oc_task:
            return f"Task '{task_id}' is not an OpenCode task"
        try:
            await self._oc.send_instruction(task_id, instruction)
            return f"Instruction sent to task {task_id}"
        except Exception as e:
            return f"Error sending instruction: {e}"

    async def _get_task_result(self, task_id: str) -> str:
        """Delegated Observation and Reporting milestone: the substantive
        answer for a completed/failed OpenCode task — what the delegated
        agent actually found or did, not just its lifecycle status (that's
        get_task_status). Prefers the result captured at completion time
        (app/integrations/opencode_supervisor.py's _capture_task_result);
        falls back to a live fetch from OpenCode's own message history if
        nothing was persisted (e.g. an older task from before this
        capture existed, or the capture itself failed) — never re-runs
        the task to answer a follow-up question about it."""
        task = await adb.get_task(task_id)
        if not task:
            return f"Task '{task_id}' not found"
        oc_task = await adb.get_opencode_task(task_id)
        if not oc_task:
            return f"Task '{task_id}' is not an OpenCode task"
        if oc_task.get("result_summary"):
            return oc_task["result_summary"]
        if task["status"] not in ("completed", "failed", "cancelled"):
            return f"Task '{task_id}' is still {task['status']} — no result yet."
        if not self._oc:
            return "Error: OpenCode supervisor not available"
        text = await self._oc.fetch_task_result_text(task_id)
        if text:
            return text
        return f"Task '{task_id}' finished ({task['status']}) but no result text is available."

    async def _answer_question(self, question_id: str, answer: str) -> str:
        q = await adb.get_question_record(question_id)
        if not q:
            return f"Question '{question_id}' not found"
        if q["status"] != "pending":
            return f"Question '{question_id}' is already {q['status']}"
        oc_task = await adb.get_opencode_task(q["task_id"])
        if oc_task and self._oc:
            try:
                return await self._oc.answer_question(question_id, answer)
            except Exception as e:
                return f"Error answering OpenCode question: {e}"
        elif self._tm:
            try:
                return await self._tm.answer_question(question_id, answer)
            except Exception as e:
                return f"Error answering question: {e}"
        return "Error: no available handler for this question"

    async def _resolve_permission(self, permission_id: str, decision: str) -> str:
        if decision not in ("approve", "reject"):
            return "Decision must be 'approve' or 'reject'"
        if not self._oc:
            return "Error: OpenCode supervisor not available"
        try:
            return await self._oc.approve_permission(permission_id, decision == "approve")
        except Exception as e:
            return f"Error resolving permission: {e}"

    async def _cancel_task(self, task_id: str) -> str:
        oc_task = await adb.get_opencode_task(task_id)
        if oc_task and self._oc:
            try:
                return await self._oc.cancel_session(task_id)
            except Exception as e:
                return f"Error cancelling OpenCode task: {e}"
        elif self._tm:
            try:
                return await self._tm.cancel(task_id)
            except Exception as e:
                return f"Error cancelling task: {e}"
        return "Error: no available handler for cancellation"

    async def _recent_activity(self, count: int = 5) -> str:
        if count < 1:
            count = 5
        if count > 50:
            count = 50
        events = await adb.get_recent_events(count * 3)
        filtered = []
        for ev in events:
            ev_type = ev.get("type", "")
            if ev_type in (
                "task_started", "task_completed", "task_failed", "task_cancelled",
                "question_asked", "question_answered", "question_cancelled",
                "opencode_task_created", "opencode_task_completed", "opencode_task_cancelled",
                "opencode_question_answered", "opencode_question_rejected", "opencode_permission_handled",
            ):
                filtered.append(ev)
            if len(filtered) >= count:
                break
        if not filtered:
            return "No recent activity."
        lines = []
        for ev in filtered:
            ts = (ev.get("timestamp") or "")[11:19]
            content = ev.get("content") or ""
            if content:
                try:
                    c = json.loads(content)
                    if "task_id" in c:
                        content = f"{c.get('name','')} ({c.get('task_id','')[:8]})"
                    elif "question" in c:
                        content = f"{c['question'][:60]}"
                    elif "answer" in c:
                        content = f"answer: {c['answer'][:40]}"
                    else:
                        content = str(c)[:60]
                except (json.JSONDecodeError, TypeError):
                    content = content[:80]
            lines.append(f"  [{ts}] {ev['type']}: {content}")
        return "\n".join(lines)

    async def _get_projects(self) -> str:
        projects = get_projects()
        if not projects:
            return "No projects configured."
        lines = []
        for alias, info in projects.items():
            dn = info.get("display_name", alias)
            desc = info.get("description", "")
            lines.append(f"  - {alias}: {dn}" + (f" — {desc}" if desc else ""))
        return "\n".join(lines)

    # ── Milestone 8: AttentionRequest / VoiceSession tools ─────────
    # Bounded and deterministic-error: full IDs (never truncated), no
    # unrestricted scheduler control, no silent source mutation.

    async def _list_attention_requests(self) -> str:
        rows = await adb.get_unresolved_attention_requests()
        if not rows:
            return "No unresolved attention requests."
        lines = [f"{len(rows)} unresolved attention request(s):"]
        for r in rows[:20]:
            lines.append(
                f"  - [{r['attention_request_id']}] {r['attention_type']} ({r['status']}): {r['summary'][:100]}"
            )
        return "\n".join(lines)

    async def _get_attention_request(self, attention_request_id: str) -> str:
        r = await adb.get_attention_request(attention_request_id)
        if not r:
            return f"Attention request '{attention_request_id}' not found"
        lines = [
            f"Attention: {r['attention_request_id']}",
            f"Type: {r['attention_type']}",
            f"Status: {r['status']}",
            f"Summary: {r['summary']}",
            f"Task: {r['task_id']}",
        ]
        if r.get("deferred_until"):
            lines.append(f"Deferred until: {r['deferred_until']}")
        if r.get("resolution_type"):
            lines.append(f"Resolution: {r['resolution_type']} = {r.get('resolution_value')}")
        return "\n".join(lines)

    async def _defer_attention(self, attention_request_id: str, deferred_until: str) -> str:
        from app import attention_manager
        row = await adb.get_attention_request(attention_request_id)
        if not row:
            return f"Attention request '{attention_request_id}' not found"
        ok = await attention_manager.defer(attention_request_id, deferred_until)
        if not ok:
            return f"Could not defer '{attention_request_id}' (it may already be resolved/cancelled)"
        return f"Deferred {attention_request_id} until {deferred_until}"

    async def _resume_attention(self, attention_request_id: str) -> str:
        from app import attention_manager
        row = await adb.get_attention_request(attention_request_id)
        if not row:
            return f"Attention request '{attention_request_id}' not found"
        if row["status"] != "deferred":
            return f"Attention request '{attention_request_id}' is not deferred (status: {row['status']})"
        ok = await attention_manager.mark_due(attention_request_id)
        if not ok:
            return f"Could not resume '{attention_request_id}'"
        fresh = await adb.get_attention_request(attention_request_id)
        if not fresh:
            return f"Could not resume '{attention_request_id}' (it disappeared after marking due)"
        await attention_manager.initiate_contact(self._cm, fresh)
        return f"Resumed {attention_request_id}"

    async def _open_voice_session(self, conversation_id: str, attention_request_id: str | None = None) -> str:
        if self._vsm is None:
            return "Voice session manager not available"
        session = await asyncio.to_thread(self._vsm.open_session, conversation_id, attention_request_id)
        return f"Voice session opened: {session['voice_session_id']} (state={session['state']})"

    async def _close_voice_session(self, voice_session_id: str) -> str:
        if self._vsm is None:
            return "Voice session manager not available"
        ok = await asyncio.to_thread(self._vsm.close_session, voice_session_id)
        if not ok:
            return f"Voice session '{voice_session_id}' not found"
        return f"Voice session {voice_session_id} closed"

    def _register_all(self) -> None:
        self._register("get_attention", "Get summary of everything needing user attention", {"type": "object", "properties": {}, "required": []}, self._get_attention)
        self._register("list_tasks", "List all active, waiting, and recent tasks", {"type": "object", "properties": {}, "required": []}, self._list_tasks)
        self._register("get_task_status", "Get detailed status for a specific task", {"type": "object", "properties": {"task_id": {"type": "string", "description": "Task ID to inspect"}}, "required": ["task_id"]}, self._get_task_status)
        self._register("get_task_result", "Get the substantive result of a completed or failed task -- what the delegated agent actually found or did, not just its status. Use this to answer follow-up questions about finished work instead of starting a new task.", {"type": "object", "properties": {"task_id": {"type": "string", "description": "Task ID to get the result for"}}, "required": ["task_id"]}, self._get_task_result)
        self._register("start_opencode_task", "Start a new OpenCode task in a safe project. NEVER accept or invent filesystem paths — always use the project_alias.", {"type": "object", "properties": {"project_alias": {"type": "string", "description": "Project alias"}, "instruction": {"type": "string", "description": "Instruction for OpenCode"}}, "required": ["project_alias", "instruction"]}, self._start_opencode_task)
        self._register("send_opencode_instruction", "Send a follow-up instruction to an existing OpenCode session", {"type": "object", "properties": {"task_id": {"type": "string", "description": "Task ID"}, "instruction": {"type": "string", "description": "Follow-up instruction"}}, "required": ["task_id", "instruction"]}, self._send_opencode_instruction)
        self._register("answer_question", "Answer a pending question", {"type": "object", "properties": {"question_id": {"type": "string", "description": "Question ID"}, "answer": {"type": "string", "description": "Answer text"}}, "required": ["question_id", "answer"]}, self._answer_question)
        self._register("resolve_permission", "Approve or reject a permission request", {"type": "object", "properties": {"permission_id": {"type": "string", "description": "Permission ID"}, "decision": {"type": "string", "enum": ["approve", "reject"], "description": "Decision"}}, "required": ["permission_id", "decision"]}, self._resolve_permission)
        self._register("cancel_task", "Cancel a running task", {"type": "object", "properties": {"task_id": {"type": "string", "description": "Task ID"}}, "required": ["task_id"]}, self._cancel_task)
        self._register("recent_activity", "Get recent meaningful events", {"type": "object", "properties": {"count": {"type": "integer", "description": "Number of events (max 50)", "default": 5}}, "required": []}, self._recent_activity)
        self._register("get_projects", "List configured safe project aliases", {"type": "object", "properties": {}, "required": []}, self._get_projects)
        self._register("list_attention_requests", "List all unresolved AttentionRequests (pending/contacting/deferred/resolving)", {"type": "object", "properties": {}, "required": []}, self._list_attention_requests)
        self._register("get_attention_request", "Get full detail for a specific AttentionRequest by ID", {"type": "object", "properties": {"attention_request_id": {"type": "string", "description": "Attention request ID"}}, "required": ["attention_request_id"]}, self._get_attention_request)
        self._register("defer_attention", "Defer an AttentionRequest until a specific ISO-8601 UTC timestamp. Never answers or cancels the underlying source.", {"type": "object", "properties": {"attention_request_id": {"type": "string", "description": "Attention request ID"}, "deferred_until": {"type": "string", "description": "ISO-8601 UTC timestamp to re-contact at"}}, "required": ["attention_request_id", "deferred_until"]}, self._defer_attention)
        self._register("resume_attention", "Mark a deferred AttentionRequest as due now, re-evaluating contact policy immediately", {"type": "object", "properties": {"attention_request_id": {"type": "string", "description": "Attention request ID"}}, "required": ["attention_request_id"]}, self._resume_attention)
        self._register("open_voice_session", "Open a voice session, optionally bound to a specific AttentionRequest for scoped answer resolution", {"type": "object", "properties": {"conversation_id": {"type": "string", "description": "Conversation ID"}, "attention_request_id": {"type": "string", "description": "Optional AttentionRequest ID to bind"}}, "required": ["conversation_id"]}, self._open_voice_session)
        self._register("close_voice_session", "Close an open voice session", {"type": "object", "properties": {"voice_session_id": {"type": "string", "description": "Voice session ID"}}, "required": ["voice_session_id"]}, self._close_voice_session)
