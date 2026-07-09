import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from .connection_manager import ConnectionManager
from .database import (
    save_event,
    create_task_record,
    update_task_status,
    get_task,
    get_recent_tasks,
    create_question_record,
    answer_question_record,
    cancel_question_record,
    get_question_record,
    get_pending_questions,
)
from .protocol import parse_line
from . import notifications
from . import attention_policy

logger = logging.getLogger("jarvis")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TaskManager:
    def __init__(self, conn_manager: ConnectionManager):
        self._conn_manager = conn_manager
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._stdins: dict[str, asyncio.StreamWriter] = {}
        self._readers: dict[str, list[asyncio.Task]] = {}
        self._cancelled: set[str] = set()
        self._pending_questions: dict[str, str] = {}  # question_id -> task_id

    # ── start methods ──────────────────────────────────────────────

    async def start_demo(self) -> dict:
        task_id = str(uuid.uuid4())
        name = "Demo Task"

        code = (
            "import sys, time\n"
            "print('Starting demo task', flush=True)\n"
            "for i in range(1, 16):\n"
            "    print(f'Progress {i}/15', flush=True)\n"
            "    time.sleep(1)\n"
            "print('Demo task complete', flush=True)\n"
        )

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )

        self._processes[task_id] = proc
        create_task_record(task_id, name, "/demo-task")

        await self._emit_task_event("task_started", task_id, name)

        stdout_task = asyncio.create_task(
            self._read_stream(task_id, name, proc.stdout, "task_stdout")
        )
        stderr_task = asyncio.create_task(
            self._read_stream(task_id, name, proc.stderr, "task_stderr")
        )
        self._readers[task_id] = [stdout_task, stderr_task]

        asyncio.create_task(self._monitor_exit(task_id, name, proc))

        return {"task_id": task_id, "name": name}

    async def start_mock_agent(self, test_mode: bool = False) -> dict:
        task_id = str(uuid.uuid4())
        name = "Mock Agent"

        worker_path = os.path.join(os.path.dirname(__file__), "workers", "mock_worker.py")
        env = os.environ.copy()
        if test_mode:
            env["JARVIS_TEST_MODE"] = "1"

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(worker_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
            env=env,
        )

        self._processes[task_id] = proc
        self._stdins[task_id] = proc.stdin
        create_task_record(task_id, name, "/mock-agent")

        await self._emit_task_event("task_started", task_id, name)

        stdout_task = asyncio.create_task(
            self._read_stream(task_id, name, proc.stdout, "task_stdout")
        )
        stderr_task = asyncio.create_task(
            self._read_stream(task_id, name, proc.stderr, "task_stderr")
        )
        self._readers[task_id] = [stdout_task, stderr_task]

        asyncio.create_task(self._monitor_exit(task_id, name, proc))

        return {"task_id": task_id, "name": name}

    # ── stream / protocol detection ────────────────────────────────

    async def _read_stream(self, task_id: str, name: str, stream, event_type: str):
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")

                if event_type == "task_stdout":
                    msg = parse_line(text)
                    if msg and msg.get("type") == "question":
                        await self._handle_question(task_id, name, msg)
                        continue

                await self._emit_task_event(event_type, task_id, name, line=text)
        except Exception:
            pass

    async def _handle_question(self, task_id: str, name: str, msg: dict):
        question_id = msg["question_id"]
        question = msg["question"]
        options = msg.get("options", [])
        context = msg.get("context", "")

        options_json = json.dumps(options) if options else "[]"
        create_question_record(question_id, task_id, question, context, options_json)
        update_task_status(task_id, "waiting_for_user")

        self._pending_questions[question_id] = task_id

        payload = {
            "question_id": question_id,
            "task_id": task_id,
            "task_name": name,
            "question": question,
            "context": context,
            "options": options,
        }
        await self._emit_event("question_asked", payload)
        logger.info("Question %s detected from task %s", question_id[:8], task_id[:8])

        await notifications.notify(
            self._conn_manager, attention_policy.KIND_QUESTION_CREATED,
            conversation_id=None, task_id=task_id,
            source_type="local_question", source_id=question_id,
            title="Jarvis needs your answer",
            body=f"{name} is waiting for your answer.",
        )

    # ── exit monitoring ────────────────────────────────────────────

    async def _monitor_exit(self, task_id: str, name: str, proc):
        try:
            readers = self._readers.pop(task_id, [])
            exit_code = await proc.wait()
            await asyncio.gather(*readers, return_exceptions=True)

            if task_id in self._cancelled:
                self._cancelled.discard(task_id)
                return

            if task_id not in self._processes:
                return

            if exit_code == 0:
                status = "completed"
                ev_type = "task_completed"
            else:
                status = "failed"
                ev_type = "task_failed"

            update_task_status(task_id, status, exit_code)
            await self._emit_task_event(ev_type, task_id, name, exit_code=exit_code)
            kind = attention_policy.KIND_TASK_FAILED if status == "failed" else attention_policy.KIND_TASK_COMPLETED
            await notifications.notify(
                self._conn_manager, kind,
                conversation_id=None, task_id=task_id,
                source_type="local_task", source_id=task_id,
                title=f"Jarvis task {status}",
                body=f"{name} {status}.",
            )
        except Exception as e:
            logger.error("Monitor error for task %s: %s", task_id, e)
        finally:
            self._processes.pop(task_id, None)
            self._stdins.pop(task_id, None)

    # ── cancel ─────────────────────────────────────────────────────

    async def cancel(self, task_id: str) -> str:
        proc = self._processes.get(task_id)
        task = get_task(task_id)

        if not proc:
            if task:
                return f"Task {task_id[:8]} is already {task['status']}"
            return f"Task {task_id[:8]} not found"

        task_name = task["name"] if task else "Unknown"

        # Cancel any pending questions for this task
        if task and task["status"] == "waiting_for_user":
            for qid, tid in list(self._pending_questions.items()):
                if tid == task_id:
                    cancel_question_record(qid)
                    self._pending_questions.pop(qid, None)
                    await self._emit_event("question_cancelled", {
                        "question_id": qid, "task_id": task_id,
                    })
                    logger.info("Question %s cancelled (task cancelled)", qid[:8])

        self._cancelled.add(task_id)
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

            update_task_status(task_id, "cancelled", -1)
            await self._emit_task_event("task_cancelled", task_id, task_name, exit_code=-1)

            self._processes.pop(task_id, None)
            self._stdins.pop(task_id, None)
            for t in self._readers.pop(task_id, []):
                if not t.done():
                    t.cancel()

            logger.info("Task %s cancelled", task_id[:8])
            return f"Task {task_id[:8]} cancelled"
        except Exception as e:
            logger.error("Error cancelling task %s: %s", task_id, e)
            return f"Error cancelling task: {e}"

    # ── answer_question ────────────────────────────────────────────

    async def answer_question(self, question_id: str, answer: str) -> str:
        question = get_question_record(question_id)
        if not question:
            return f"Question {question_id[:8]} not found"
        if question["status"] != "pending":
            return f"Question {question_id[:8]} is already {question['status']}"

        task_id = question["task_id"]
        task = get_task(task_id)
        if not task:
            return f"Task for question {question_id[:8]} not found"
        if task["status"] != "waiting_for_user":
            return f"Task {task_id[:8]} is not waiting (status: {task['status']})"

        stdin = self._stdins.get(task_id)
        if not stdin or stdin.is_closing():
            return f"Task {task_id[:8]} has no stdin available"

        stdin.write((answer + "\n").encode("utf-8"))
        await stdin.drain()

        answer_question_record(question_id, answer)
        update_task_status(task_id, "running")
        self._pending_questions.pop(question_id, None)

        await self._emit_event("question_answered", {
            "question_id": question_id,
            "task_id": task_id,
            "answer": answer,
        })

        logger.info("Question %s answered, task %s resumed", question_id[:8], task_id[:8])
        return f"Answer delivered to task {task_id[:8]}"

    # ── info queries ───────────────────────────────────────────────

    def get_running_tasks_info(self) -> list[dict]:
        result = []
        now = datetime.now(timezone.utc)
        for task_id in list(self._processes.keys()):
            task = get_task(task_id)
            if task:
                try:
                    started = datetime.fromisoformat(
                        task["started_at"].replace("Z", "+00:00")
                    )
                    elapsed = int((now - started).total_seconds())
                except (ValueError, TypeError):
                    elapsed = 0
                result.append({
                    "task_id": task_id,
                    "name": task["name"],
                    "status": task["status"],
                    "elapsed": elapsed,
                })
        return result

    def get_pending_questions_info(self) -> list[dict]:
        result = []
        for q in get_pending_questions():
            task = get_task(q["task_id"])
            result.append({
                "question_id": q["question_id"],
                "task_id": q["task_id"],
                "task_name": task["name"] if task else "Unknown",
                "question": q["question"],
                "context": q["context"] or "",
                "options": json.loads(q["options_json"]) if q.get("options_json") else [],
                "asked_at": q["asked_at"],
            })
        return result

    def get_tasks_summary(self) -> str:
        running = self.get_running_tasks_info()
        recent = get_recent_tasks(20)

        lines = ["*Active Tasks:*"]
        if running:
            for t in running:
                m, s = divmod(t["elapsed"], 60)
                icon = "\u23f3" if t["status"] == "waiting_for_user" else "\u25b6"
                lines.append(f"  `{t['task_id'][:8]}`  {t['name']}  {icon} {t['status']}  ({m}:{s:02d})")
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("*Recent Tasks:*")
        icons = {
            "running": "\u25b6",
            "waiting_for_user": "\u23f3",
            "completed": "\u2713",
            "failed": "\u2717",
            "cancelled": "\u2298",
        }
        for t in recent:
            icon = icons.get(t["status"], "?")
            exit_str = f"exit={t['exit_code']}" if t.get("exit_code") is not None else ""
            lines.append(f"  `{t['task_id'][:8]}`  {t['name']}  {icon} {t['status']}  {exit_str}")

        return "\n".join(lines)

    def get_attention_summary(self) -> str:
        running = self.get_running_tasks_info()
        waiting = [t for t in running if t["status"] == "waiting_for_user"]
        pending = get_pending_questions()

        lines = [
            f"Running tasks: {len(running)}",
            f"Tasks waiting for user: {len(waiting)}",
            f"Pending questions: {len(pending)}",
        ]
        if pending:
            lines.append("")
            lines.append("Pending Questions:")
            for q in pending:
                task = get_task(q["task_id"])
                tn = task["name"] if task else "Unknown"
                lines.append(f"  `{q['question_id'][:8]}`  {tn}: {q['question']}")
        return "\n".join(lines)

    # ── shutdown ───────────────────────────────────────────────────

    async def shutdown(self):
        count = len(self._processes)
        logger.info("Shutting down TaskManager, cancelling %d tasks", count)
        for task_id in list(self._processes.keys()):
            await self.cancel(task_id)

    # ── event emission ─────────────────────────────────────────────

    async def _emit_task_event(self, ev_type: str, task_id: str, name: str,
                                line: str | None = None,
                                exit_code: int | None = None):
        payload: dict = {"task_id": task_id, "name": name}
        if line is not None:
            payload["line"] = line
        if exit_code is not None:
            payload["exit_code"] = exit_code
        await self._emit_event(ev_type, payload)

    async def _emit_event(self, ev_type: str, payload: dict):
        content = json.dumps(payload)
        timestamp = _now()
        save_event(ev_type, content)
        await self._conn_manager.broadcast(
            {"type": ev_type, "timestamp": timestamp, "content": content}
        )
