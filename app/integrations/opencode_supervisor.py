"""Orchestration layer: ties OpenCode server, adapter, and task manager together."""
import asyncio
import collections
import json
import logging
import uuid
from datetime import datetime, timezone

import app.database as db
from app import db_async as adb
from app import config
from app import notifications
from app import attention_policy
from app import worker_events
from app import attention_manager
from app import trace
from app.integrations.opencode_server import OpenCodeServerManager
from app.integrations.opencode_adapter import OpenCodeAdapter
from app.integrations.opencode_events import normalize_question, normalize_permission, process_sse_event
from app.operational_state import OperationalState

logger = logging.getLogger(__name__)

DEFAULT_PORT = config.opencode_port()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_result_text(messages: list) -> str | None:
    """Delegated Observation and Reporting milestone: pure extraction from
    OpenCodeAdapter.get_messages()'s response shape (verified directly
    against a live completed session — each message has info.role and a
    parts list; a part's type is "text", "tool", "reasoning", "step-start",
    or "step-finish"). The delegated agent's own final natural-language
    answer is the last text part of the last assistant message — that's
    what a person would actually want read back, not internal reasoning
    or raw tool output. Falls back to the last tool call's raw output only
    if the agent never wrote a closing text part (e.g. it stopped right
    after a tool call) — still substantive, just less readable. Returns
    None if there's nothing usable; never raises on an unexpected shape."""
    last_text = None
    last_tool_output = None
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("info", {}).get("role") != "assistant":
            continue
        for part in msg.get("parts", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and part.get("text"):
                last_text = part["text"]
            elif part.get("type") == "tool":
                output = part.get("state", {}).get("output")
                if output:
                    last_tool_output = output
    return last_text or last_tool_output

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

        # ADR-022 Phase 3/4: explicit lifecycle state, separate from the
        # server's own is_alive/check_health (which answer "is the process
        # up" and "is it responding", not "what did the last operation do").
        self.state: str = OperationalState.STOPPED
        self.last_operation: str | None = None
        self.last_operation_result: str | None = None  # "success" | "failure" | None (in progress)
        self.last_operation_error: str | None = None
        self.last_updated: str | None = None

    def _claim(self, operation: str, from_states: set, to_state: str) -> bool:
        """Synchronous, no-await state-transition gate. Because this never
        awaits, two concurrent callers can never both see the same
        pre-transition state — whichever coroutine's synchronous code runs
        first wins the claim, and the loser sees the already-updated state.
        This is the entire concurrency-safety mechanism for start/stop/
        restart; the async bodies below assume the claim already happened
        and never re-check state themselves."""
        if self.state not in from_states:
            return False
        self.state = to_state
        self.last_operation = operation
        self.last_operation_result = None
        self.last_operation_error = None
        self.last_updated = _now_iso()
        return True

    def claim_start(self) -> bool:
        return self._claim("start", {OperationalState.STOPPED, OperationalState.FAILED}, OperationalState.STARTING)

    def claim_stop(self) -> bool:
        return self._claim("stop", {OperationalState.RUNNING, OperationalState.FAILED}, OperationalState.STOPPING)

    def claim_restart(self) -> bool:
        return self._claim("restart", {OperationalState.RUNNING, OperationalState.FAILED}, OperationalState.STOPPING)

    async def _do_start(self) -> tuple[bool, str | None]:
        """Mechanics only -- deliberately does not touch
        last_operation_result on the success path. A restart's stop
        sub-phase completing must never make last_operation_result briefly
        read "success" for the still-in-progress overall restart (found
        via live validation: a poll landing between the stop and start
        phases saw exactly that). Only run_claimed_start()/
        run_claimed_restart() finalize the user-visible result, once the
        *whole* requested operation is done.

        TD-032 / MILESTONE_F1 F1.8: the failure path DOES set
        last_operation_error before transitioning to FAILED, so that any
        observer who sees state == FAILED also sees the error populated.
        _finalize() handles last_operation_result (success path); only
        last_operation_error is set here (failure path).

        Caller must already hold a successful claim_start() (state ==
        STARTING) before calling this -- this is also the fix for the
        confirmed defect where start() never reset self._stopped, so
        background loops recreated after a stop() would see self._stopped
        already True and exit immediately."""
        try:
            self._stopped = False
            await self.server.start()
            self._sse_task = asyncio.create_task(self._sse_loop())
            self._poll_task = asyncio.create_task(self._poll_loop())
            await self.reconcile_on_startup()
            self.state = OperationalState.RUNNING
            logger.info("OpenCode supervisor started")
            return True, None
        except Exception as e:
            self.last_operation_error = str(e)
            self.state = OperationalState.FAILED
            logger.error("OpenCode supervisor failed to start: %s", e, exc_info=True)
            return False, str(e)

    async def _do_stop(self) -> tuple[bool, str | None]:
        """Mechanics only -- see _do_start()'s docstring for why the success
        path does not touch last_operation_result. The failure path sets
        last_operation_error before transitioning to FAILED. Caller must
        already hold a successful claim_stop() (state == STOPPING)."""
        try:
            self._stopped = True
            if self._sse_task:
                self._sse_task.cancel()
                try:
                    await self._sse_task
                except asyncio.CancelledError:
                    pass
                self._sse_task = None
            if self._poll_task:
                self._poll_task.cancel()
                try:
                    await self._poll_task
                except asyncio.CancelledError:
                    pass
                self._poll_task = None
            await self.server.stop()
            self.state = OperationalState.STOPPED
            logger.info("OpenCode supervisor stopped")
            return True, None
        except Exception as e:
            self.last_operation_error = str(e)
            self.state = OperationalState.FAILED
            logger.error("OpenCode supervisor failed to stop: %s", e, exc_info=True)
            return False, str(e)

    def _finalize(self, ok: bool, error: str | None) -> bool:
        self.last_operation_result = "success" if ok else "failure"
        self.last_operation_error = error
        self.last_updated = _now_iso()
        return ok

    async def run_claimed_start(self) -> bool:
        ok, error = await self._do_start()
        return self._finalize(ok, error)

    async def run_claimed_stop(self) -> bool:
        ok, error = await self._do_stop()
        return self._finalize(ok, error)

    async def run_claimed_restart(self) -> bool:
        """Caller must already hold a successful claim_restart() (state ==
        STOPPING) before calling this. Stop and start are sequenced, not
        atomic: if stop fails, start is never attempted and the true state
        (FAILED) is left in place rather than reporting a false restart."""
        stopped_ok, stop_error = await self._do_stop()
        if not stopped_ok:
            return self._finalize(False, stop_error)
        # _do_stop() already set state to STOPPED; advance straight to
        # STARTING with no intervening await, so no other caller can
        # observe or act on the momentary STOPPED state.
        self.state = OperationalState.STARTING
        self.last_updated = _now_iso()
        started_ok, start_error = await self._do_start()
        return self._finalize(started_ok, start_error)

    async def start(self) -> None:
        """Idempotent entry point for internal callers (app lifespan). A
        no-op if already running or starting."""
        if not self.claim_start():
            return
        await self.run_claimed_start()

    async def snapshot_status(self) -> dict:
        """Live status: state + last-operation bookkeeping (sync fields)
        plus a real health check (ADR-022 Phase 4) — state and health are
        deliberately different questions. Health is only meaningful while
        RUNNING; a server that is STOPPED or mid-transition has no health
        to report, not "unhealthy"."""
        if self.state == OperationalState.RUNNING:
            health = "HEALTHY" if await self.server.check_health() else "UNHEALTHY"
        else:
            health = "UNKNOWN"
        return {
            "state": self.state,
            "health": health,
            "last_operation": self.last_operation,
            "last_operation_result": self.last_operation_result,
            "last_operation_error": self.last_operation_error,
            "last_updated": self.last_updated,
        }

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
        degraded = await adb.get_opencode_degraded_tasks()
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
        """Idempotent entry point for internal callers (app lifespan). A
        no-op if already stopped or stopping."""
        if not self.claim_stop():
            return
        await self.run_claimed_stop()

    async def start_session(
        self, project_dir: str, instruction: str,
        provider_id: str | None = None, model_id: str | None = None,
    ) -> dict:
        """Create a new OpenCode session and link it to a Jarvis task.

        provider_id/model_id optionally override the default pinned
        free-only model (still validated free-only) — e.g. to spread
        concurrent delegated sessions across different free-tier models
        instead of contending for the same rate limit.

        Returns dict with task_id, session_id, and name.
        """
        task_id = f"oc_{uuid.uuid4().hex[:12]}"
        session_id = await self.adapter.create_session(project_dir)
        logger.info("opencode task/session mapping created: task_id=%s session_id=%s", task_id, session_id, extra={"task_id": task_id, "session_id": session_id})

        # ADR-020: start_session() is reached from Supervisor.process_message()
        # via ToolRegistry.call() -> _start_opencode_task(), several layers
        # below the Supervisor -- read the current turn's trace_id from the
        # ContextVar rather than threading a new parameter through that whole
        # chain. This is the hard case ADR-020 exists to solve: OpenCode's
        # own completion arrives asynchronously, potentially long after this
        # turn has already returned, so trace_id must be persisted on the
        # row now, at creation, not passed through a live callback later.
        trace_id = trace.current_trace_id()
        await adb.create_task_record(task_id, f"OpenCode: {instruction[:50]}", instruction, trace_id=trace_id)
        await adb.create_opencode_task_record(task_id, session_id, project_dir, instruction, trace_id=trace_id)

        await self._notify_broadcast({
            "type": "opencode_task_created",
            "task_id": task_id,
            "session_id": session_id,
            "instruction": instruction,
            "trace_id": trace_id,
        })

        self._completion_events[task_id] = asyncio.Event()
        self._error_since_prompt[task_id] = False

        await self.adapter.send_prompt(session_id, project_dir, instruction, provider_id=provider_id, model_id=model_id)
        logger.info("opencode instruction submitted: task_id=%s session_id=%s", task_id, session_id, extra={"task_id": task_id, "session_id": session_id})

        return {"task_id": task_id, "session_id": session_id, "name": f"OpenCode: {instruction[:50]}"}

    async def send_instruction(self, task_id: str, instruction: str) -> None:
        """Send a follow-up instruction to an existing OpenCode session.

        Resets the per-task error-since-prompt flag so a later session.idle
        is correctly evaluated against *this* turn's outcome, not a stale
        failure from an earlier turn.
        """
        oc_task = await adb.get_opencode_task(task_id)
        if not oc_task:
            raise ValueError(f"Task '{task_id}' is not an OpenCode task")
        self._error_since_prompt[task_id] = False
        await self.adapter.send_prompt(oc_task["session_id"], oc_task["project_dir"], instruction)
        logger.info("opencode instruction submitted: task_id=%s session_id=%s", task_id, oc_task["session_id"], extra={"task_id": task_id, "session_id": oc_task["session_id"]})

    async def cancel_session(self, task_id: str) -> str:
        """Cancel an OpenCode session by Jarvis task_id."""
        oc_task = await adb.get_opencode_task(task_id)
        if not oc_task:
            return f"Task {task_id} not found or not an OpenCode task"
        try:
            await self.adapter.abort_session(oc_task["session_id"], oc_task["project_dir"])
        except Exception as e:
            logger.warning("Abort API call failed: %s", e)
        await adb.update_task_status(task_id, "cancelled", -1)
        await adb.update_opencode_task_status(task_id, "cancelled")
        if task_id in self._completion_events:
            self._completion_events[task_id].set()
        await self._notify_broadcast({
            "type": "opencode_task_cancelled",
            "task_id": task_id,
            "trace_id": oc_task.get("trace_id") if oc_task else None,
        })
        # Milestone 8 Phase 20: invalidate any still-unresolved
        # AttentionRequests tied to this task — never keep contacting
        # about a source that no longer exists.
        await attention_manager.cancel_for_task(task_id)
        return f"OpenCode task {task_id} cancelled"

    async def fetch_task_result_text(self, task_id: str) -> str | None:
        """Delegated Observation and Reporting milestone: fetches the
        delegated agent's own final answer for a task, on demand, from
        OpenCode's own message history — adapter.get_messages() already
        existed (Milestone 9A) but was never called anywhere; OpenCode
        retains the real conversation itself, Jarvis only needed to ask
        for it rather than only watching the SSE activity stream for
        "something happened" evidence. Returns None (never raises) if the
        task is unknown, OpenCode is unreachable, or there's no text
        content to extract — callers decide how to degrade."""
        oc_task = await adb.get_opencode_task(task_id)
        if not oc_task:
            return None
        try:
            messages = await self.adapter.get_messages(oc_task["session_id"], oc_task["project_dir"])
        except Exception:
            logger.warning("Failed to fetch OpenCode messages for task_id=%s", task_id, exc_info=True)
            return None
        return _extract_result_text(messages)

    async def _capture_task_result(self, task_id: str) -> None:
        """Best-effort persistence at terminal-state time (see
        _handle_session_idle/_handle_session_failed) — a capture failure
        must never affect the terminal state transition that already
        happened by the time this runs; get_task_result (app/supervisor/
        tools.py) falls back to a live fetch if nothing was persisted."""
        try:
            text = await self.fetch_task_result_text(task_id)
            if text:
                await adb.update_opencode_task_result(task_id, text[:4000])
            oc_task = await adb.get_opencode_task(task_id)
            if oc_task:
                task = await adb.get_task(task_id)
                final_status = oc_task.get("status", "unknown")
                description = task["name"] if task else "Unknown task"
                result_summary = (text or oc_task.get("result_summary", "") or "")[:300]
                content = f"OpenCode task {final_status}: {description}. Result: {result_summary}"
                await adb.store_memory(
                    category="episodic",
                    content=content,
                    source="task_completion",
                    source_id=task_id,
                )
        except Exception:
            logger.warning("Failed to capture result for task_id=%s", task_id, exc_info=True)

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
        record = await adb.get_question_record(question_id)
        if not record:
            return f"Question {question_id} not found"
        oc_task = await adb.get_opencode_task(record["task_id"])
        if not oc_task:
            return "Not an OpenCode task"
        await self.adapter.reply_question(question_id, oc_task["project_dir"], answer)
        await adb.answer_question_record(question_id, answer)
        await asyncio.to_thread(self._clear_waiting_state, record["task_id"])
        logger.info("opencode answer delivered: task_id=%s question_id=%s", record["task_id"], question_id)
        await self._notify_broadcast({
            "type": "opencode_question_answered",
            "question_id": question_id,
            "task_id": record["task_id"],
        })
        # Milestone 8: resolve only after adapter.reply_question() above
        # has already succeeded without raising — never before.
        await attention_manager.resolve_for_source("opencode_question", question_id, "answered", answer)
        return f"Answered question {question_id}"

    async def reject_question(self, question_id: str) -> str:
        """Reject an OpenCode question."""
        record = await adb.get_question_record(question_id)
        if not record:
            return f"Question {question_id} not found"
        oc_task = await adb.get_opencode_task(record["task_id"])
        if not oc_task:
            return "Not an OpenCode task"
        await self.adapter.reject_question(question_id, oc_task["project_dir"])
        await adb.cancel_question_record(question_id)
        await asyncio.to_thread(self._clear_waiting_state, record["task_id"])
        await self._notify_broadcast({
            "type": "opencode_question_rejected",
            "question_id": question_id,
            "task_id": record["task_id"],
        })
        # A reject is a real user decision (native delivery already
        # succeeded above), not a source invalidation — resolve, don't
        # cancel (Phase 20: "answered elsewhere -> RESOLVED or CANCELLED
        # according to semantics").
        await attention_manager.resolve_for_source("opencode_question", question_id, "rejected", None)
        return f"Rejected question {question_id}"

    async def approve_permission(self, permission_id: str, approved: bool) -> str:
        """Approve or deny an OpenCode permission request."""
        records = await adb.get_pending_questions()
        record = next((q for q in records if q.get("question_id") == permission_id), None)
        if not record:
            return f"Permission {permission_id} not found"

        oc_task = await adb.get_opencode_task(record["task_id"])
        if not oc_task:
            return "Not an OpenCode task"

        await self.adapter.reply_permission(permission_id, oc_task["project_dir"], approved)
        await adb.answer_question_record(permission_id, "approved" if approved else "denied")
        await asyncio.to_thread(self._clear_waiting_state, record["task_id"])
        logger.info("opencode permission resolved: task_id=%s permission_id=%s approved=%s", record["task_id"], permission_id, approved)

        await self._notify_broadcast({
            "type": "opencode_permission_handled",
            "permission_id": permission_id,
            "task_id": record["task_id"],
            "approved": approved,
        })
        # Milestone 8: resolve only after adapter.reply_permission() above
        # has already succeeded without raising — never before.
        await attention_manager.resolve_for_source(
            "opencode_permission", permission_id, "approved" if approved else "denied", None,
        )
        return f"Permission {permission_id} {'approved' if approved else 'denied'}"

    async def get_status(self) -> dict:
        """Return current status of all OpenCode tasks and server health."""
        server_alive = self.server.is_alive
        running_tasks = await adb.get_opencode_running_tasks()
        pending_questions = await adb.get_pending_questions()
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
        while not self._stopped:
            try:
                running_tasks = await adb.get_opencode_running_tasks()
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
                existing = await adb.get_question_record(request_id)
                if existing:
                    continue
                oc_task = await adb.get_opencode_task_by_session(
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
                existing = await adb.get_question_record(request_id)
                if existing:
                    continue
                oc_task = await adb.get_opencode_task_by_session(
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
                oc_task = await adb.get_opencode_task_by_session(session_id)
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

        await adb.create_question_record(
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
        await adb.update_task_status(task_id, "waiting_for_user")
        await adb.update_opencode_task_status(task_id, "waiting_for_user")

        task = await adb.get_task(task_id)
        task_name = task["name"] if task else "A task"
        await worker_events.create_attention(
            self.cm,
            worker_events.WorkerAttentionEvent(
                worker_type="opencode", worker_task_id=task_id,
                event_type=worker_events.QUESTION_REQUIRED, source_id=request_id,
                summary=f"{task_name} is waiting for your answer.", urgency="HIGH",
            ),
        )

    async def _emit_permission(self, event: dict) -> None:
        task_id = event["task_id"]
        request_id = event["request_id"]

        await adb.create_question_record(
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
        logger.warning(
            "CONTAINMENT: OpenCode task %s requested permission action=%s path=%s — awaiting user approval",
            task_id, event["action"], event.get("path", ""),
        )
        await adb.update_task_status(task_id, "waiting_for_user")
        await adb.update_opencode_task_status(task_id, "waiting_for_user")

        task = await adb.get_task(task_id)
        task_name = task["name"] if task else "A task"
        try:
            await adb.store_memory(
                category="episodic",
                content=f"OpenCode requested {event['action']} permission for {event.get('path', '')} during task '{task_name}' ({task_id}).",
                source="opencode_permission",
                source_id=request_id,
            )
        except Exception:
            logger.warning("Failed to store episodic memory for permission %s", request_id)
        await self._notify_broadcast({
            "type": "thinking_update",
            "action": "opencode_permission",
            "status": "started",
            "summary": f"OpenCode requested {event['action']} permission for {event.get('path', '')}",
            "detail": None,
            "conversation_id": None,
            "trace_id": task.get("trace_id") if task else None,
            "timestamp": db.utcnow(),
        })
        # Phase 15: keep this generic — never include the raw path/action
        # detail here. Full context is already visible in the authenticated
        # app's Needs Your Attention panel (the task_permission WS message
        # above carries the real path/action for that surface).
        await worker_events.create_attention(
            self.cm,
            worker_events.WorkerAttentionEvent(
                worker_type="opencode", worker_task_id=task_id,
                event_type=worker_events.PERMISSION_REQUIRED, source_id=request_id,
                summary=f"{task_name} needs your permission to continue.", urgency="HIGH",
            ),
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
        task = await adb.get_task(task_id)
        if task and task["status"] in ("completed", "failed", "cancelled"):
            return  # already terminal — ignore late/duplicate error evidence
        oc_task = await adb.get_opencode_task(task_id)
        duration = (datetime.now(timezone.utc) - datetime.fromisoformat(oc_task["created_at"].replace("Z", "+00:00"))).total_seconds() if oc_task else 0.0
        # M-OX.3 live-validation finding: this handler runs on the SSE
        # loop's own asyncio Task, not the turn's -- trace.current_trace_id()
        # is always None here. Recover it from the row instead, exactly the
        # mechanism ADR-020 built start_session()'s persist-at-creation for.
        terminal_trace_id = oc_task.get("trace_id") if oc_task else None
        # M-OX.3 live-validation finding (Phase 4, incorrect severity): a
        # genuine task failure was logged at INFO -- severity-based
        # filtering/alerting for real errors would silently miss every
        # OpenCode task failure. This is the one call site in this method
        # that reports an actual failure; WARNING is correct here even
        # though the method's other logging (activity, non-terminal
        # states) legitimately stays at INFO.
        logger.warning(
            "opencode task terminal state observed: task_id=%s status=failed evidence=session.error", task_id,
            extra={"task_id": task_id, "status": "failed", "duration_seconds": duration, "trace_id": terminal_trace_id},
        )
        await adb.update_task_status(task_id, "failed", -1)
        await adb.update_opencode_task_status(task_id, "failed")
        await adb.update_opencode_task_evidence(task_id, "session.error")
        # Delegated Observation and Reporting milestone: a failed task can
        # still have a useful partial result (e.g. the agent's own
        # explanation of what went wrong) -- same capture path as success.
        await self._capture_task_result(task_id)
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
            "trace_id": terminal_trace_id,
        })
        task_name = task["name"] if task else "A task"
        await worker_events.create_attention(
            self.cm,
            worker_events.WorkerAttentionEvent(
                worker_type="opencode", worker_task_id=task_id,
                event_type=worker_events.TASK_FAILED, source_id=task_id,
                summary=f"{task_name} failed.", urgency="HIGH",
            ),
        )

    async def _handle_session_idle(self, event: dict) -> None:
        """session.idle means the current turn finished — but idle alone does
        not prove success. It must be combined with: no session.error since
        the last prompt, and no pending native question/permission (which
        would mean the task is waiting on the user, not done)."""
        task_id = event["task_id"]
        if task_id == "unknown":
            return
        task = await adb.get_task(task_id)
        if task and task["status"] in ("completed", "failed", "cancelled"):
            return  # already terminal

        if self._error_since_prompt.get(task_id):
            return  # a session.error for this turn already took precedence

        pending = [q for q in await adb.get_pending_questions() if q["task_id"] == task_id]
        if pending:
            return  # genuinely waiting on the user, not complete

        oc_task = await adb.get_opencode_task(task_id)
        duration = (datetime.now(timezone.utc) - datetime.fromisoformat(oc_task["created_at"].replace("Z", "+00:00"))).total_seconds() if oc_task else 0.0
        # M-OX.3 live-validation finding: see the matching comment in
        # _handle_session_failed() -- same asyncio-Task-boundary issue.
        terminal_trace_id = oc_task.get("trace_id") if oc_task else None
        logger.info(
            "opencode task terminal state observed: task_id=%s status=completed evidence=session.idle", task_id,
            extra={"task_id": task_id, "status": "completed", "duration_seconds": duration, "trace_id": terminal_trace_id},
        )
        await adb.update_task_status(task_id, "completed", 0)
        await adb.update_opencode_task_status(task_id, "completed")
        await adb.update_opencode_task_evidence(task_id, "session.idle")
        # Delegated Observation and Reporting milestone: capture the
        # delegated agent's own final answer *before* announcing
        # completion, so a follow-up question asked right after "task
        # completed" already has something to answer from — not a second,
        # separate round trip the user has to wait through.
        await self._capture_task_result(task_id)
        if task_id in self._completion_events:
            self._completion_events[task_id].set()
        await self._notify_broadcast({
            "type": "opencode_task_completed",
            "task_id": task_id,
            "status": "completed",
            "source": "opencode",
            "trace_id": terminal_trace_id,
        })
        task_name = task["name"] if task else "A task"
        oc_task = await adb.get_opencode_task(task_id)
        result_summary = (oc_task.get("result_summary") or "")[:80] if oc_task else ""
        body = f"{task_name} completed" + (f": {result_summary}" if result_summary else ".")
        await notifications.notify(
            self.cm, attention_policy.KIND_TASK_COMPLETED,
            conversation_id=None, task_id=task_id,
            source_type="opencode_task", source_id=task_id,
            title="Jarvis task completed",
            body=body,
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
        task = await adb.get_task(task_id)
        oc_task_for_log = await adb.get_opencode_task(task_id)
        first_activity = task is not None and oc_task_for_log and not oc_task_for_log.get("last_evidence_type")
        # M-OX.3 live-validation finding: these two lines carried neither
        # task_id nor trace_id in structured output, unlike their sibling
        # terminal-state log lines in the same class -- an obviously
        # incomplete fix if left inconsistent. Same row-recovery mechanism
        # as _handle_session_failed()/_handle_session_idle() (this handler
        # also runs on the SSE loop's own asyncio Task, no ambient binding).
        activity_extra = {
            "task_id": task_id,
            "trace_id": oc_task_for_log.get("trace_id") if oc_task_for_log else None,
            "evidence_type": event.get("evidence_type"),
        }
        if first_activity:
            logger.info("opencode first execution event observed: task_id=%s evidence=%s", task_id, event.get("evidence_type"), extra=activity_extra)
        else:
            # Demo visibility (Milestone 9B.10): the first-event log above
            # was the only console output for an entire task's execution --
            # every subsequent real activity event updated the DB silently.
            # This line makes the already-real, already-streaming OpenCode
            # activity visible continuously instead of one line then a long
            # silent gap. Purely additive logging -- no control flow change.
            logger.info("opencode activity: task_id=%s evidence=%s", task_id, event.get("evidence_type"), extra=activity_extra)
        await adb.update_opencode_task_evidence(task_id, event.get("evidence_type", "activity"))
        if task and task["status"] not in ("waiting_for_user", "completed", "failed", "cancelled"):
            await adb.update_task_status(task_id, "running")
            await adb.update_opencode_task_status(task_id, "running")

    async def _notify_broadcast(self, msg: dict) -> None:
        try:
            await self.cm.broadcast(msg)
        except Exception as e:
            logger.warning("Broadcast failed: %s", e)
