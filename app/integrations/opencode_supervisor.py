"""Orchestration layer: ties OpenCode server, adapter, and task manager together."""
import asyncio
import collections
import json
import logging
import os
import uuid

import app.database as db
from app import notifications
from app import attention_policy
from app.integrations.opencode_server import OpenCodeServerManager
from app.integrations.opencode_adapter import OpenCodeAdapter
from app.integrations.opencode_events import normalize_question, normalize_permission, process_sse_event

logger = logging.getLogger(__name__)

DEFAULT_PORT = int(os.environ.get("JARVIS_OPENCODE_PORT", "4097"))

# Bounded SSE event-id dedup window: OpenCode's SSE reconnect can replay
# recent events, and a flaky connection may deliver the same event twice.
# Terminal-state and question/permission handling must not double-fire.
_SEEN_EVENT_IDS_MAX = 500


class OpenCodeSupervisor:
    """High-level supervisor that manages OpenCode sessions for Jarvis tasks."""

    def __init__(self, connection_manager, task_manager):
        self.cm = connection_manager
        self.tm = task_manager
        self.server = OpenCodeServerManager(port=DEFAULT_PORT)
        self.adapter = OpenCodeAdapter(base_url=self.server.base_url)
        self._sse_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._stopped = False
        # task_id -> asyncio.Event for completion notification
        self._completion_events: dict[str, asyncio.Event] = {}
        # task_id -> whether a session.error was observed since the last
        # prompt was sent; used to distinguish "idle after success" from
        # "idle after failure" (session.idle alone is not sufficient
        # completion evidence — see _handle_session_idle).
        self._error_since_prompt: dict[str, bool] = {}
        self._seen_event_ids: collections.deque = collections.deque(maxlen=_SEEN_EVENT_IDS_MAX)
        self._seen_event_ids_set: set[str] = set()

    async def start(self) -> None:
        """Start the OpenCode server and background supervision loops."""
        await self.server.start()
        self._sse_task = asyncio.create_task(self._sse_loop())
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("OpenCode supervisor started")
        await self.reconcile_on_startup()

    async def reconcile_on_startup(self) -> None:
        """Attempt to upgrade 'degraded' OpenCode tasks (left ambiguous by a
        prior Jarvis restart — see db.mark_running_opencode_tasks_interrupted)
        to a verified status now that a fresh server is up. Sessions persist
        in OpenCode's own database independent of the server process, so a
        fresh `opencode serve` can still be asked about an old session_id.

        Only upgrades on positive, verified evidence. Anything that cannot
        be verified (including the currently-known empty message-history
        endpoint — see SESSION.md) is left 'degraded' rather than guessed.
        """
        degraded = db.get_opencode_degraded_tasks()
        if not degraded:
            return
        logger.info("opencode reconciliation started: %d degraded task(s)", len(degraded))
        upgraded = 0
        for oc_task in degraded:
            task_id = oc_task["task_id"]
            session_id = oc_task["session_id"]
            try:
                session = await self.adapter.get_session(session_id, oc_task["project_dir"])
            except Exception as e:
                logger.info("opencode reconciliation: could not query session %s for task %s: %s", session_id, task_id, e)
                continue
            # No verified terminal signal is currently obtainable from
            # `GET /session/{id}` alone (no status field proven reliable —
            # see Phase 3 investigation). Left degraded until a verified
            # signal (e.g. a working message/status endpoint) is available.
            if session is None:
                continue
        logger.info("opencode reconciliation result: %d/%d upgraded, %d remain degraded",
                    upgraded, len(degraded), len(degraded) - upgraded)

    async def stop(self) -> None:
        self._stopped = True
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self.server.stop()
        logger.info("OpenCode supervisor stopped")

    async def start_session(self, project_dir: str, instruction: str) -> dict:
        """Create a new OpenCode session and link it to a Jarvis task.

        Returns dict with task_id, session_id, and name.
        """
        task_id = f"oc_{uuid.uuid4().hex[:12]}"
        session_id = await self.adapter.create_session(project_dir)
        logger.info("opencode task/session mapping created: task_id=%s session_id=%s", task_id, session_id)

        db.create_task_record(task_id, f"OpenCode: {instruction[:50]}", instruction)
        db.create_opencode_task_record(task_id, session_id, project_dir, instruction)

        await self._notify_broadcast({
            "type": "opencode_task_created",
            "task_id": task_id,
            "session_id": session_id,
            "instruction": instruction,
        })

        self._completion_events[task_id] = asyncio.Event()
        self._error_since_prompt[task_id] = False

        await self.adapter.send_prompt(session_id, project_dir, instruction)
        logger.info("opencode instruction submitted: task_id=%s session_id=%s", task_id, session_id)

        return {"task_id": task_id, "session_id": session_id, "name": f"OpenCode: {instruction[:50]}"}

    async def send_instruction(self, task_id: str, instruction: str) -> None:
        """Send a follow-up instruction to an existing OpenCode session.

        Resets the per-task error-since-prompt flag so a later session.idle
        is correctly evaluated against *this* turn's outcome, not a stale
        failure from an earlier turn.
        """
        oc_task = db.get_opencode_task(task_id)
        if not oc_task:
            raise ValueError(f"Task '{task_id}' is not an OpenCode task")
        self._error_since_prompt[task_id] = False
        await self.adapter.send_prompt(oc_task["session_id"], oc_task["project_dir"], instruction)
        logger.info("opencode instruction submitted: task_id=%s session_id=%s", task_id, oc_task["session_id"])

    async def cancel_session(self, task_id: str) -> str:
        """Cancel an OpenCode session by Jarvis task_id."""
        oc_task = db.get_opencode_task(task_id)
        if not oc_task:
            return f"Task {task_id} not found or not an OpenCode task"
        try:
            await self.adapter.abort_session(oc_task["session_id"], oc_task["project_dir"])
        except Exception as e:
            logger.warning("Abort API call failed: %s", e)
        db.update_task_status(task_id, "cancelled", -1)
        db.update_opencode_task_status(task_id, "cancelled")
        if task_id in self._completion_events:
            self._completion_events[task_id].set()
        await self._notify_broadcast({
            "type": "opencode_task_cancelled",
            "task_id": task_id,
        })
        return f"OpenCode task {task_id} cancelled"

    def _clear_waiting_state(self, task_id: str) -> None:
        """After a question/permission is resolved, resume the task —
        unless another item is still pending for it. Both `tasks.status`
        and `opencode_tasks.status` must move together; letting them drift
        (only one updated) is what made the OpenCode "waiting" attention
        line permanently dead in Milestone 5 (Phase 8 fix)."""
        remaining = [q for q in db.get_pending_questions() if q["task_id"] == task_id]
        if remaining:
            return
        task = db.get_task(task_id)
        if task and task["status"] == "waiting_for_user":
            db.update_task_status(task_id, "running")
        oc_task = db.get_opencode_task(task_id)
        if oc_task and oc_task["status"] == "waiting_for_user":
            db.update_opencode_task_status(task_id, "running")

    async def answer_question(self, question_id: str, answer: str) -> str:
        """Reply to an OpenCode question."""
        record = db.get_question_record(question_id)
        if not record:
            return f"Question {question_id} not found"
        oc_task = db.get_opencode_task(record["task_id"])
        if not oc_task:
            return "Not an OpenCode task"
        await self.adapter.reply_question(question_id, oc_task["project_dir"], answer)
        db.answer_question_record(question_id, answer)
        self._clear_waiting_state(record["task_id"])
        logger.info("opencode answer delivered: task_id=%s question_id=%s", record["task_id"], question_id)
        await self._notify_broadcast({
            "type": "opencode_question_answered",
            "question_id": question_id,
            "task_id": record["task_id"],
        })
        return f"Answered question {question_id}"

    async def reject_question(self, question_id: str) -> str:
        """Reject an OpenCode question."""
        record = db.get_question_record(question_id)
        if not record:
            return f"Question {question_id} not found"
        oc_task = db.get_opencode_task(record["task_id"])
        if not oc_task:
            return "Not an OpenCode task"
        await self.adapter.reject_question(question_id, oc_task["project_dir"])
        db.cancel_question_record(question_id)
        self._clear_waiting_state(record["task_id"])
        await self._notify_broadcast({
            "type": "opencode_question_rejected",
            "question_id": question_id,
            "task_id": record["task_id"],
        })
        return f"Rejected question {question_id}"

    async def approve_permission(self, permission_id: str, approved: bool) -> str:
        """Approve or deny an OpenCode permission request."""
        records = db.get_pending_questions()
        record = next((q for q in records if q.get("question_id") == permission_id), None)
        if not record:
            return f"Permission {permission_id} not found"

        oc_task = db.get_opencode_task(record["task_id"])
        if not oc_task:
            return "Not an OpenCode task"

        await self.adapter.reply_permission(permission_id, oc_task["project_dir"], approved)
        db.answer_question_record(permission_id, "approved" if approved else "denied")
        self._clear_waiting_state(record["task_id"])
        logger.info("opencode permission resolved: task_id=%s permission_id=%s approved=%s", record["task_id"], permission_id, approved)

        await self._notify_broadcast({
            "type": "opencode_permission_handled",
            "permission_id": permission_id,
            "task_id": record["task_id"],
            "approved": approved,
        })
        return f"Permission {permission_id} {'approved' if approved else 'denied'}"

    async def get_status(self) -> dict:
        """Return current status of all OpenCode tasks and server health."""
        server_alive = self.server.is_alive
        running_tasks = db.get_opencode_running_tasks()
        pending_questions = db.get_pending_questions()
        return {
            "server_alive": server_alive,
            "server_url": self.server.base_url,
            "running_tasks": running_tasks,
            "pending_questions": pending_questions,
        }

    async def wait_for_completion(self, task_id: str, timeout: float | None = None) -> bool:
        """Wait for an OpenCode task to complete. Returns True if completed."""
        event = self._completion_events.get(task_id)
        if event is None:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    # ── Internal ──────────────────────────────────────────────────

    async def _sse_loop(self) -> None:
        """Background task consuming SSE events from OpenCode."""
        while not self._stopped:
            try:
                async for event in self.adapter.consume_events("*"):
                    if self._stopped:
                        break
                    await self._handle_sse_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("SSE loop error: %s", e, exc_info=True)
                if not self._stopped:
                    await asyncio.sleep(5)

    async def _poll_loop(self) -> None:
        """Background task polling for questions/permissions (backup for SSE)."""
        polled_dirs = set()
        while not self._stopped:
            try:
                running_tasks = db.get_opencode_running_tasks()
                dirs = {t["project_dir"] for t in running_tasks}
                for directory in dirs:
                    await self._poll_questions(directory)
                    await self._poll_permissions(directory)
            except Exception as e:
                logger.debug("Poll loop error: %s", e)
            await asyncio.sleep(3)

    async def _poll_questions(self, directory: str) -> None:
        try:
            questions = await self.adapter.get_questions(directory)
            for oc_q in questions:
                request_id = oc_q.get("requestID") or oc_q.get("id", "")
                if not request_id:
                    continue
                existing = db.get_question_record(request_id)
                if existing:
                    continue
                oc_task = db.get_opencode_task_by_session(
                    oc_q.get("sessionID", "")
                )
                task_id = oc_task["task_id"] if oc_task else "unknown"
                event = normalize_question(oc_q, task_id)
                if event:
                    await self._emit_question(event)
        except Exception as e:
            logger.debug("Poll questions failed for %s: %s", directory, e)

    async def _poll_permissions(self, directory: str) -> None:
        try:
            permissions = await self.adapter.get_permissions(directory)
            for oc_p in permissions:
                request_id = oc_p.get("requestID") or oc_p.get("id", "")
                if not request_id:
                    continue
                existing = db.get_question_record(request_id)
                if existing:
                    continue
                oc_task = db.get_opencode_task_by_session(
                    oc_p.get("sessionID", "")
                )
                task_id = oc_task["task_id"] if oc_task else "unknown"
                event = normalize_permission(oc_p, task_id)
                if event:
                    await self._emit_permission(event)
        except Exception as e:
            logger.debug("Poll permissions failed for %s: %s", directory, e)

    async def _handle_sse_event(self, sse_event: dict) -> None:
        """Route a raw SSE event to the right handler.

        Malformed/unrecognized events must never raise out of this method —
        a single bad frame must not terminate SSE supervision (Phase 10).
        """
        try:
            envelope = sse_event.get("data", {})
            if not isinstance(envelope, dict):
                return

            payload = envelope.get("payload")
            evt_id = payload.get("id") if isinstance(payload, dict) else None
            if evt_id:
                if evt_id in self._seen_event_ids_set:
                    return  # duplicate delivery (SSE reconnect replay) — ignore
                if len(self._seen_event_ids) == self._seen_event_ids.maxlen:
                    self._seen_event_ids_set.discard(self._seen_event_ids[0])
                self._seen_event_ids.append(evt_id)
                self._seen_event_ids_set.add(evt_id)

            session_id = ""
            if isinstance(payload, dict):
                props = payload.get("properties")
                if isinstance(props, dict):
                    session_id = props.get("sessionID", "")

            oc_task = None
            if session_id:
                oc_task = db.get_opencode_task_by_session(session_id)
            task_id = oc_task["task_id"] if oc_task else "unknown"

            events = process_sse_event(sse_event, task_id)

            for ev in events:
                ev_type = ev.get("event", "")
                if task_id == "unknown" and ev_type != "question_poll_trigger" and ev_type != "permission_poll_trigger":
                    # No known Jarvis task for this session (e.g. a session
                    # created outside Jarvis, like OpenCode Desktop) — nothing
                    # to update. Still safe to let poll triggers no-op below.
                    continue
                if ev_type == "question_poll_trigger":
                    directory = ev.get("directory") or (oc_task["project_dir"] if oc_task else None)
                    if directory:
                        await self._poll_questions(directory)
                elif ev_type == "permission_poll_trigger":
                    directory = ev.get("directory") or (oc_task["project_dir"] if oc_task else None)
                    if directory:
                        await self._poll_permissions(directory)
                elif ev_type == "session_failed":
                    await self._handle_session_failed(ev)
                elif ev_type == "session_idle":
                    await self._handle_session_idle(ev)
                elif ev_type == "activity":
                    await self._handle_activity(ev)
        except Exception:
            logger.exception("Error handling SSE event (ignored, supervision continues): %s", sse_event)

    async def _emit_question(self, event: dict) -> None:
        task_id = event["task_id"]
        request_id = event["request_id"]

        db.create_question_record(
            request_id,
            task_id,
            event["question"],
            event.get("context", ""),
            json.dumps(event.get("options", [])),
        )

        msg = {
            "type": "task_question",
            "task_id": task_id,
            "question_id": request_id,
            "question": event["question"],
            "options": event.get("options", []),
            "context": event.get("context", ""),
            "source": "opencode",
        }
        await self._notify_broadcast(msg)

        logger.info("opencode question received: task_id=%s question_id=%s", task_id, request_id)
        db.update_task_status(task_id, "waiting_for_user")
        db.update_opencode_task_status(task_id, "waiting_for_user")

        task = db.get_task(task_id)
        task_name = task["name"] if task else "A task"
        await notifications.notify(
            self.cm, attention_policy.KIND_QUESTION_CREATED,
            conversation_id=None, task_id=task_id,
            source_type="opencode_question", source_id=request_id,
            title="Jarvis needs your answer",
            body=f"{task_name} is waiting for your answer.",
        )

    async def _emit_permission(self, event: dict) -> None:
        task_id = event["task_id"]
        request_id = event["request_id"]

        db.create_question_record(
            request_id,
            task_id,
            f"Permission: {event['action']} {event.get('path', '')}",
            f"action={event['action']}&path={event.get('path', '')}",
            json.dumps([]),
        )

        msg = {
            "type": "task_permission",
            "task_id": task_id,
            "permission_id": request_id,
            "action": event["action"],
            "path": event.get("path", ""),
            "source": "opencode",
        }
        await self._notify_broadcast(msg)

        logger.info("opencode permission received: task_id=%s permission_id=%s", task_id, request_id)
        db.update_task_status(task_id, "waiting_for_user")
        db.update_opencode_task_status(task_id, "waiting_for_user")

        task = db.get_task(task_id)
        task_name = task["name"] if task else "A task"
        # Phase 15: keep this generic — never include the raw path/action
        # detail here. Full context is already visible in the authenticated
        # app's Needs Your Attention panel (the task_permission WS message
        # above carries the real path/action for that surface).
        await notifications.notify(
            self.cm, attention_policy.KIND_PERMISSION_CREATED,
            conversation_id=None, task_id=task_id,
            source_type="opencode_permission", source_id=request_id,
            title="Jarvis needs a permission decision",
            body=f"{task_name} needs your permission to continue.",
        )

    async def _emit_message(self, event: dict) -> None:
        msg = {
            "type": "opencode_message",
            "task_id": event["task_id"],
            "role": event["role"],
            "content": event["content"],
            "source": "opencode",
        }
        await self._notify_broadcast(msg)

    async def _handle_session_failed(self, event: dict) -> None:
        """session.error is definitive, verified failure evidence."""
        task_id = event["task_id"]
        if task_id == "unknown":
            return
        self._error_since_prompt[task_id] = True
        task = db.get_task(task_id)
        if task and task["status"] in ("completed", "failed", "cancelled"):
            return  # already terminal — ignore late/duplicate error evidence
        logger.info("opencode task terminal state observed: task_id=%s status=failed evidence=session.error", task_id)
        db.update_task_status(task_id, "failed", -1)
        db.update_opencode_task_status(task_id, "failed")
        db.update_opencode_task_evidence(task_id, "session.error")
        if task_id in self._completion_events:
            self._completion_events[task_id].set()
        await self._notify_broadcast({
            "type": "opencode_error",
            "task_id": task_id,
            "raw": event.get("error", {}),
            "source": "opencode",
        })
        await self._notify_broadcast({
            "type": "opencode_task_completed",
            "task_id": task_id,
            "status": "failed",
            "source": "opencode",
        })
        task_name = task["name"] if task else "A task"
        await notifications.notify(
            self.cm, attention_policy.KIND_TASK_FAILED,
            conversation_id=None, task_id=task_id,
            source_type="opencode_task", source_id=task_id,
            title="Jarvis task failed",
            body=f"{task_name} failed.",
        )

    async def _handle_session_idle(self, event: dict) -> None:
        """session.idle means the current turn finished — but idle alone does
        not prove success. It must be combined with: no session.error since
        the last prompt, and no pending native question/permission (which
        would mean the task is waiting on the user, not done)."""
        task_id = event["task_id"]
        if task_id == "unknown":
            return
        task = db.get_task(task_id)
        if task and task["status"] in ("completed", "failed", "cancelled"):
            return  # already terminal

        if self._error_since_prompt.get(task_id):
            return  # a session.error for this turn already took precedence

        pending = [q for q in db.get_pending_questions() if q["task_id"] == task_id]
        if pending:
            return  # genuinely waiting on the user, not complete

        logger.info("opencode task terminal state observed: task_id=%s status=completed evidence=session.idle", task_id)
        db.update_task_status(task_id, "completed", 0)
        db.update_opencode_task_status(task_id, "completed")
        db.update_opencode_task_evidence(task_id, "session.idle")
        if task_id in self._completion_events:
            self._completion_events[task_id].set()
        await self._notify_broadcast({
            "type": "opencode_task_completed",
            "task_id": task_id,
            "status": "completed",
            "source": "opencode",
        })
        task_name = task["name"] if task else "A task"
        await notifications.notify(
            self.cm, attention_policy.KIND_TASK_COMPLETED,
            conversation_id=None, task_id=task_id,
            source_type="opencode_task", source_id=task_id,
            title="Jarvis task completed",
            body=f"{task_name} completed.",
        )

    async def _handle_activity(self, event: dict) -> None:
        """Verified evidence that OpenCode is actively working (a streamed
        message part, a tool call, a step) — never inferred from silence.
        Promotes a task from pending/degraded to running on first evidence
        and records the evidence for observability, without overriding a
        more specific state (waiting_for_user, terminal)."""
        task_id = event["task_id"]
        if task_id == "unknown":
            return
        task = db.get_task(task_id)
        first_activity = task is not None and db.get_opencode_task(task_id) and not db.get_opencode_task(task_id).get("last_evidence_type")
        if first_activity:
            logger.info("opencode first execution event observed: task_id=%s evidence=%s", task_id, event.get("evidence_type"))
        db.update_opencode_task_evidence(task_id, event.get("evidence_type", "activity"))
        if task and task["status"] not in ("waiting_for_user", "completed", "failed", "cancelled"):
            db.update_task_status(task_id, "running")
            db.update_opencode_task_status(task_id, "running")

    async def _notify_broadcast(self, msg: dict) -> None:
        try:
            await self.cm.broadcast(msg)
        except Exception as e:
            logger.warning("Broadcast failed: %s", e)
