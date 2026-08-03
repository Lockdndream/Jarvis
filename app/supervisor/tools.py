"""Bounded supervisor tool registry for Jarvis.

Tools are async methods registered with name, description, JSON schema.
The registry is populated by the Supervisor at init time with references
to existing service layers (TaskManager, OpenCodeSupervisor).
"""
import asyncio
import json
import logging
from typing import Any

import uuid as _uuid

from app import db_async as adb
from app.supervisor.projects import resolve_project, get_projects
from app.supervisor.context import build_context, format_memory_sections
from app.workers.opencode_worker import OpenCodeWorker
from app.workers.base import WorkerResultStatus

logger = logging.getLogger(__name__)

_VALID_ON_FAILURE = {"continue", "stop", "escalate"}


class ToolRegistry:
    """Registry of bounded supervisor tools backed by service references."""

    def __init__(
        self,
        task_manager: Any = None,
        opencode_supervisor: Any = None,
        connection_manager: Any = None,
        worker_registry: Any = None,
        plan_executor: Any = None,
    ) -> None:
        self._tm = task_manager
        self._oc = opencode_supervisor
        self._cm = connection_manager
        self._worker_registry = worker_registry
        self._plan_executor = plan_executor
        self._oc_worker = OpenCodeWorker(opencode_supervisor) if opencode_supervisor else None
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

    def _register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Any,
        wants_conversation_id: bool = False,
    ) -> None:
        self._tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
            "wants_conversation_id": wants_conversation_id,
        }

    def get(self, name: str) -> dict | None:
        return self._tools.get(name)

    def list_definitions(self) -> list[dict]:
        return [
            {"name": n, "description": t["description"], "parameters": t["parameters"]}
            for n, t in self._tools.items()
        ]

    async def call(
        self, name: str, args: dict, conversation_id: str | None = None,
    ) -> str:
        tool = self.get(name)
        if not tool:
            return f"Error: unknown tool '{name}'"
        try:
            if tool.get("wants_conversation_id"):
                return await tool["handler"](**args, conversation_id=conversation_id)
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
        if not self._oc_worker:
            return "Error: OpenCode supervisor not available"
        alias, path = resolve_project(project_alias)
        if not alias:
            return f"Cannot start task: {path}"
        result = await self._oc_worker.invoke(instruction, project_dir=path)
        if result.status == WorkerResultStatus.FAILED:
            return f"Error starting task: {result.error}"
        session_id = result.metadata.get("session_id") or ""
        return f"Task started: {result.task_id} (session {session_id[:20]}). Instruction: {instruction[:100]}"

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

    async def _consult_strategist(self, question: str) -> str:
        if not self._worker_registry:
            return "Error: strategist not available"
        worker = self._worker_registry.get_by_name("strategist")
        if not worker:
            return "Error: strategist not available"
        context = await asyncio.to_thread(build_context, None, question)
        prompt = (
            f"{_format_context_for_strategist(context)}\n\n"
            f"Question for the strategist: {question}"
        )
        result = await worker.invoke(prompt)
        try:
            advice = result.output if result.status != WorkerResultStatus.FAILED else result.error
            advice_text = (advice or "")[:500]
            content = f"Strategist advised on '{question}': {advice_text}"
            await adb.store_memory(
                category="episodic",
                content=content,
                source="strategist",
                source_id=None,
            )
        except Exception:
            logger.warning("Failed to write strategist consultation memory", exc_info=True)
        if result.status == WorkerResultStatus.FAILED:
            return f"Error consulting strategist: {result.error}"
        return result.output or "(strategist returned no output)"

    async def _create_plan(self, title: str, steps: list[dict]) -> str:
        if not self._plan_executor:
            return "Error: plan executor not available"
        for i, step in enumerate(steps):
            on_failure = step.get("on_failure", "stop")
            if on_failure not in _VALID_ON_FAILURE:
                return (
                    f"Error: step {i} has invalid on_failure '{on_failure}'. "
                    f"Must be one of: {', '.join(sorted(_VALID_ON_FAILURE))}"
                )
        plan_id = f"plan_{_uuid.uuid4().hex[:12]}"
        await adb.create_plan_record(plan_id, title)
        for i, step in enumerate(steps):
            step_id = f"step_{_uuid.uuid4().hex[:12]}"
            await adb.create_plan_step_record(
                step_id, plan_id, i,
                description=step.get("description", ""),
                worker_name=step.get("worker_name", ""),
                on_failure=step.get("on_failure", "stop"),
                verification=step.get("verification"),
            )
        return f"Plan '{plan_id}' created with {len(steps)} steps."

    async def _start_plan(self, plan_id: str) -> str:
        if not self._plan_executor:
            return "Error: plan executor not available"
        ok = await self._plan_executor.start_plan(plan_id)
        if not ok:
            return f"Could not start plan '{plan_id}' — it may already be running or no longer pending."
        return f"Plan '{plan_id}' is now running in the background."

    async def _plan_status(self, plan_id: str) -> str:
        if not self._plan_executor:
            return "Error: plan executor not available"
        plan = await adb.get_plan(plan_id)
        if not plan:
            return f"Plan '{plan_id}' not found."
        steps = await adb.get_plan_steps(plan_id)
        lines = [
            f"Plan '{plan_id}': {plan['title']}",
            f"Status: {plan['status']}",
            f"Current step: {plan['current_step_index']} of {len(steps)}",
            "Steps:",
        ]
        for s in steps:
            extra = ""
            if s["status"] == "failed" and s.get("error"):
                extra = f" — {s['error']}"
            lines.append(
                f"  [{s['step_index']}] {s['description'][:80]} "
                f"(worker={s['worker_name']}, status={s['status']}){extra}"
            )
        return "\n".join(lines)

    async def _resume_plan(self, plan_id: str, instruction: str) -> str:
        if not self._plan_executor:
            return "Error: plan executor not available"
        try:
            await self._plan_executor.resume_plan(plan_id, instruction)
        except ValueError as e:
            return str(e)
        return f"Plan '{plan_id}' resumed with instruction '{instruction}'."

    async def _list_plans(self, count: int = 10) -> str:
        if not self._plan_executor:
            return "Error: plan executor not available"
        if count < 1:
            count = 10
        if count > 50:
            count = 50
        plans = await adb.get_recent_plans(count)
        if not plans:
            return "No plans found."
        lines = [f"Recent plans ({len(plans)}):"]
        for p in plans:
            lines.append(f"  - [{p['plan_id']}] {p['title']} ({p['status']})")
        return "\n".join(lines)

    async def _remember_this(self, content: str, project: str | None = None) -> str:
        try:
            await adb.store_memory(
                category="explicit",
                content=content,
                project=project,
                source="user_explicit",
                source_id=None,
            )
            return "Got it, I'll remember that."
        except Exception:
            logger.warning("Failed to remember explicit memory", exc_info=True)
            return "I couldn't save that — please try again."

    async def _forget_this(self, query: str | None = None, memory_id: str | None = None) -> str:
        if memory_id is not None:
            try:
                await adb.delete_memory(memory_id)
            except Exception:
                logger.warning("Failed to delete memory by id", exc_info=True)
                return "I couldn't delete that — please try again."
            return "Forgotten."

        if query is None:
            return "Please provide either a query or a memory_id."

        try:
            matches = await adb.retrieve_memories(query, limit=10)
        except Exception:
            logger.warning("Failed to retrieve memories for forget", exc_info=True)
            return "I couldn't look that up — please try again."

        if not matches:
            return f"I couldn't find a memory matching '{query}'."

        if len(matches) == 1:
            mem = matches[0]
            try:
                await adb.delete_memory(mem["id"])
            except Exception:
                logger.warning("Failed to delete matched memory", exc_info=True)
                return "I couldn't delete that — please try again."
            excerpt = mem["content"][:80]
            if len(mem["content"]) > 80:
                excerpt = excerpt + "..."
            return f"Forgotten: {excerpt}"

        lines = [
            f"I found {len(matches)} memories matching '{query}'. "
            "Please be more specific or use what_do_you_remember to find the exact id, "
            "then call forget_this with memory_id:"
        ]
        for mem in matches:
            excerpt = mem["content"][:80]
            if len(mem["content"]) > 80:
                excerpt = excerpt + "..."
            project_note = f" (project: {mem['project']})" if mem.get("project") else ""
            lines.append(f"  - [{mem['id']}] {mem['category']}{project_note}: {excerpt}")
        return "\n".join(lines)

    async def _what_do_you_remember(self, project: str | None = None, count: int = 10) -> str:
        if count < 1:
            count = 10
        if count > 50:
            count = 50
        try:
            memories = await adb.get_recent_memories(project=project, limit=count)
        except Exception:
            logger.warning("Failed to retrieve recent memories", exc_info=True)
            return "I couldn't retrieve memories — please try again."

        if not memories:
            if project:
                return f"I don't have any memories stored for project '{project}'."
            return "I don't have any memories stored."

        lines = []
        if project:
            lines.append(f"Recent memories for project '{project}':")
        else:
            lines.append("Recent memories:")
        for mem in memories:
            content = mem["content"]
            excerpt = content[:150]
            if len(content) > 150:
                excerpt = excerpt + "..."
            project_part = f" project={mem['project']}," if mem.get("project") else ""
            lines.append(f"  - [{mem['id']}] {mem['category']},{project_part} {excerpt}")
        return "\n".join(lines)

    async def _catch_me_up(
        self,
        since: str | None = None,
        project: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        window: str | None
        if since is not None:
            window = since
        else:
            window = await adb.get_previous_conversation_boundary(conversation_id)

        summary = await adb.get_activity_summary(since=window, project=project)
        lines = [summary]

        if self._plan_executor is None:
            return "\n".join(lines)

        paused_plans = await adb.get_plans_by_status("paused")
        failed_plans = await adb.get_plans_by_status("failed")
        covered_plan_ids: set[str] = set()

        if paused_plans or failed_plans:
            lines.append("Needs your attention:")
            for plan in paused_plans:
                plan_id = plan["plan_id"]
                title = plan["title"]
                step_index = plan["current_step_index"]
                lines.append(
                    f"  - Plan '{title}' is paused waiting on step {step_index}. "
                    "Say retry, skip, or abort to resume."
                )
                covered_plan_ids.add(plan_id)
            for plan in failed_plans:
                title = plan["title"]
                step_index = plan["current_step_index"]
                lines.append(
                    f"  - Plan '{title}' failed at step {step_index}."
                )
                covered_plan_ids.add(plan["plan_id"])

        attention_rows = await adb.get_unresolved_attention_requests()
        for row in attention_rows:
            if row["attention_type"] != "SUPERVISOR_ESCALATION":
                continue
            if row.get("task_id") in covered_plan_ids:
                continue
            lines.append(f"  - {row['summary']}")

        return "\n".join(lines)

    def _register_all(self) -> None:
        self._register("get_attention", "Get summary of everything needing user attention. The context block already includes 'Pending questions:' and 'Pending permissions:' -- only call this if those sections are missing or you need more detail than they show.", {"type": "object", "properties": {}, "required": []}, self._get_attention)
        self._register("list_tasks", "List all active, waiting, and recent tasks. The context block already includes 'Active tasks:' -- only call this if you need waiting/recent tasks beyond what's already shown there.", {"type": "object", "properties": {}, "required": []}, self._list_tasks)
        self._register("get_task_status", "Get detailed status for a specific task", {"type": "object", "properties": {"task_id": {"type": "string", "description": "Task ID to inspect"}}, "required": ["task_id"]}, self._get_task_status)
        self._register("get_task_result", "Get the substantive result of a completed or failed task -- what the delegated agent actually found or did, not just its status. Use this to answer follow-up questions about finished work instead of starting a new task.", {"type": "object", "properties": {"task_id": {"type": "string", "description": "Task ID to get the result for"}}, "required": ["task_id"]}, self._get_task_result)
        self._register("start_opencode_task", "Start a new OpenCode task in a safe project. NEVER accept or invent filesystem paths — always use the project_alias.", {"type": "object", "properties": {"project_alias": {"type": "string", "description": "Project alias"}, "instruction": {"type": "string", "description": "Instruction for OpenCode"}}, "required": ["project_alias", "instruction"]}, self._start_opencode_task)
        self._register("send_opencode_instruction", "Send a follow-up instruction to an existing OpenCode session", {"type": "object", "properties": {"task_id": {"type": "string", "description": "Task ID"}, "instruction": {"type": "string", "description": "Follow-up instruction"}}, "required": ["task_id", "instruction"]}, self._send_opencode_instruction)
        self._register("answer_question", "Answer a pending question", {"type": "object", "properties": {"question_id": {"type": "string", "description": "Question ID"}, "answer": {"type": "string", "description": "Answer text"}}, "required": ["question_id", "answer"]}, self._answer_question)
        self._register("resolve_permission", "Approve or reject a permission request", {"type": "object", "properties": {"permission_id": {"type": "string", "description": "Permission ID"}, "decision": {"type": "string", "enum": ["approve", "reject"], "description": "Decision"}}, "required": ["permission_id", "decision"]}, self._resolve_permission)
        self._register("cancel_task", "Cancel a running task", {"type": "object", "properties": {"task_id": {"type": "string", "description": "Task ID"}}, "required": ["task_id"]}, self._cancel_task)
        self._register("recent_activity", "Get recent meaningful events across all projects. The context block already includes a 'Recent activity' section -- only call this if you need more events than it shows, or a different count. For 'what happened' / 'catch me up' style questions, use catch_me_up instead -- it summarizes, this just lists raw events.", {"type": "object", "properties": {"count": {"type": "integer", "description": "Number of events (max 50)", "default": 5}}, "required": []}, self._recent_activity)
        self._register("get_projects", "List configured safe project aliases. The context block already includes a 'Safe projects:' line -- only call this if that's missing or you need to double-check an alias.", {"type": "object", "properties": {}, "required": []}, self._get_projects)
        self._register("list_attention_requests", "List all unresolved AttentionRequests (pending/contacting/deferred/resolving)", {"type": "object", "properties": {}, "required": []}, self._list_attention_requests)
        self._register("get_attention_request", "Get full detail for a specific AttentionRequest by ID", {"type": "object", "properties": {"attention_request_id": {"type": "string", "description": "Attention request ID"}}, "required": ["attention_request_id"]}, self._get_attention_request)
        self._register("defer_attention", "Defer an AttentionRequest until a specific ISO-8601 UTC timestamp. Never answers or cancels the underlying source.", {"type": "object", "properties": {"attention_request_id": {"type": "string", "description": "Attention request ID"}, "deferred_until": {"type": "string", "description": "ISO-8601 UTC timestamp to re-contact at"}}, "required": ["attention_request_id", "deferred_until"]}, self._defer_attention)
        self._register("resume_attention", "Mark a deferred AttentionRequest as due now, re-evaluating contact policy immediately", {"type": "object", "properties": {"attention_request_id": {"type": "string", "description": "Attention request ID"}}, "required": ["attention_request_id"]}, self._resume_attention)
        self._register("open_voice_session", "Open a voice session, optionally bound to a specific AttentionRequest for scoped answer resolution", {"type": "object", "properties": {"conversation_id": {"type": "string", "description": "Conversation ID"}, "attention_request_id": {"type": "string", "description": "Optional AttentionRequest ID to bind"}}, "required": ["conversation_id"]}, self._open_voice_session)
        self._register("close_voice_session", "Close an open voice session", {"type": "object", "properties": {"voice_session_id": {"type": "string", "description": "Voice session ID"}}, "required": ["voice_session_id"]}, self._close_voice_session)
        self._register(
            "consult_strategist",
            "Consult the strategist — an AI planning/review advisor — about a "
            "complex question, plan, or decision. Use this when you want a "
            "second opinion before proceeding with something significant, or "
            "need help thinking through a plan.",
            {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question or topic to consult the strategist about"}
                },
                "required": ["question"],
            },
            self._consult_strategist,
        )
        self._register(
            "create_plan",
            "Create a new sequential plan with the given title and steps. Each step needs a description and worker_name; optional on_failure (stop/continue/escalate, default stop) and verification.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Plan title"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string", "description": "What this step should do"},
                                "worker_name": {"type": "string", "enum": ["opencode", "strategist"], "description": "Worker to execute this step"},
                                "on_failure": {"type": "string", "enum": ["continue", "stop", "escalate"], "description": "What to do if this step fails (default: stop)"},
                                "verification": {"type": "string", "description": "Optional verification instruction"},
                            },
                            "required": ["description", "worker_name"],
                        },
                        "description": "Ordered list of steps",
                    },
                },
                "required": ["title", "steps"],
            },
            self._create_plan,
        )
        self._register("start_plan", "Start executing a plan that is currently pending", {"type": "object", "properties": {"plan_id": {"type": "string", "description": "Plan ID to start"}}, "required": ["plan_id"]}, self._start_plan)
        self._register("plan_status", "Get the detailed status of a plan and all its steps", {"type": "object", "properties": {"plan_id": {"type": "string", "description": "Plan ID to inspect"}}, "required": ["plan_id"]}, self._plan_status)
        self._register(
            "resume_plan",
            "Resume a paused plan with one of: retry (re-run the failed step), skip (skip it and continue), or abort (mark plan as failed).",
            {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "Plan ID to resume"},
                    "instruction": {"type": "string", "enum": ["retry", "skip", "abort"], "description": "Resume instruction"},
                },
                "required": ["plan_id", "instruction"],
            },
            self._resume_plan,
        )
        self._register("list_plans", "List recent plans", {"type": "object", "properties": {"count": {"type": "integer", "description": "Number of plans (default 10)", "default": 10}}, "required": []}, self._list_plans)
        self._register(
            "remember_this",
            "Store a fact the user explicitly asked you to remember (e.g. 'remember that...', "
            "'keep in mind that...'). Rephrase what they said as a clear standalone statement "
            "for `content`. If the fact relates to a specific project, set `project` to that "
            "project's alias (see get_projects); otherwise omit it.",
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The standalone fact to remember"},
                    "project": {"type": "string", "description": "Optional project alias if the fact is project-scoped"},
                },
                "required": ["content"],
            },
            self._remember_this,
        )
        self._register(
            "forget_this",
            "Remove a memory the user explicitly asked you to forget. Provide either the exact "
            "memory_id from what_do_you_remember, or a query describing the memory (e.g. 'JWT tokens').",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword/phrase describing the memory to remove"},
                    "memory_id": {"type": "string", "description": "Exact memory id to remove"},
                },
                "required": [],
            },
            self._forget_this,
        )
        self._register(
            "what_do_you_remember",
            "List facts the user explicitly asked to be remembered (via 'remember that...'), optionally filtered to a specific project. This is NOT for project activity or history -- it only returns what the user told you to remember, never task/plan results. For 'tell me about project X' or 'what did we do on project X', use catch_me_up with a project filter instead.",
            {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Optional project alias filter"},
                    "count": {"type": "integer", "description": "Number of memories to return (default 10, max 50)", "default": 10},
                },
                "required": [],
            },
            self._what_do_you_remember,
        )
        self._register(
            "catch_me_up",
            "Summarize what happened recently — completed/failed plans, "
            "finished tasks, and anything needing the user's attention. Use "
            "this when the user says things like 'what happened', 'catch me "
            "up', 'what did I miss', 'status update', or similar. Also use "
            "this (with `project` set) for 'tell me about project X' or "
            "'what's the last thing we did on project X' -- it is the "
            "activity-history tool, not what_do_you_remember. If the user "
            "specifies a time reference (e.g. 'since yesterday', 'while I was "
            "at lunch', 'in the last hour'), resolve it yourself to an ISO "
            "8601 UTC datetime string and pass it as `since`. If you cannot "
            "confidently resolve a time reference, omit `since` entirely — the "
            "tool will default to summarizing since the user's last "
            "conversation.",
            {
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "ISO 8601 UTC datetime to summarize activity from. Omit if the user gave no clear time reference."},
                    "project": {"type": "string", "description": "Optional project alias to scope the summary to."},
                },
                "required": [],
            },
            self._catch_me_up,
            wants_conversation_id=True,
        )


def _format_context_for_strategist(context: dict) -> str:
    parts = []

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
            parts.append(f"  - task={q['task_id']}: {q['question']}")
    return "\n".join(parts) if parts else "No active project context."
