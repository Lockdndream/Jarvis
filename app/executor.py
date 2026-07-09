import asyncio
import datetime
import os
import platform
import subprocess


class Executor:
    def __init__(self, task_manager=None, opencode_supervisor=None):
        self._task_manager = task_manager
        self._oc = opencode_supervisor

    async def execute(self, command: str):
        parts = command.strip().split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "/status":
            yield "command_started", None
            lines = [
                f"Hostname: {platform.node()}",
                f"OS: {platform.system()} {platform.release()}",
                f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Python: {platform.python_version()}",
            ]
            yield "command_output", "\n".join(lines)
            yield "command_completed", None

        elif cmd == "/processes":
            yield "command_started", None
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [
                            "powershell",
                            "-NoProfile",
                            "-Command",
                            "Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name, Id, @{N='CPU(s)';E={$_.CPU.ToString('F1')}} | Format-Table -AutoSize | Out-String -Width 4096",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    ),
                )
                output = result.stdout.strip()
                yield "command_output", output or "No output"
                yield "command_completed", None
            except subprocess.TimeoutExpired:
                yield "command_failed", "Command timed out"
            except FileNotFoundError:
                yield "command_failed", "PowerShell not found"
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/pwd":
            yield "command_started", None
            yield "command_output", os.getcwd()
            yield "command_completed", None

        elif cmd == "/demo-task":
            yield "command_started", "Starting demo task..."
            if not self._task_manager:
                yield "command_failed", "Task manager not available"
                return
            try:
                result = await self._task_manager.start_demo()
                yield "command_output", (
                    f"Task started: {result['name']} ({result['task_id'][:8]})"
                )
                yield "command_completed", "Task is running in background"
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/tasks":
            yield "command_started", None
            if not self._task_manager:
                yield "command_failed", "Task manager not available"
                return
            yield "command_output", self._task_manager.get_tasks_summary()
            yield "command_completed", None

        elif cmd == "/cancel":
            if not args.strip():
                yield "command_failed", "Usage: /cancel <task_id>"
                return
            task_id = args.strip()
            yield "command_started", f"Cancelling task {task_id[:8]}..."
            if not self._task_manager:
                yield "command_failed", "Task manager not available"
                return
            try:
                msg = await self._task_manager.cancel(task_id)
                yield "command_output", msg
                yield "command_completed", None
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/mock-agent":
            yield "command_started", "Starting mock agent..."
            if not self._task_manager:
                yield "command_failed", "Task manager not available"
                return
            try:
                result = await self._task_manager.start_mock_agent()
                yield "command_output", (
                    f"Mock agent started: {result['name']} ({result['task_id'][:8]})"
                )
                yield "command_completed", "Agent is running in background"
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/answer":
            if not args.strip():
                yield "command_failed", "Usage: /answer <question_id> <answer>"
                return
            sub = args.strip().split(maxsplit=1)
            if len(sub) < 2:
                yield "command_failed", "Usage: /answer <question_id> <answer>"
                return
            question_id = sub[0]
            answer = sub[1]
            yield "command_started", f"Sending answer to question {question_id[:8]}..."
            if not self._task_manager:
                yield "command_failed", "Task manager not available"
                return
            try:
                msg = await self._task_manager.answer_question(question_id, answer)
                yield "command_output", msg
                yield "command_completed", None
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/attention":
            yield "command_started", None
            if not self._task_manager:
                yield "command_failed", "Task manager not available"
                return
            yield "command_output", self._task_manager.get_attention_summary()
            yield "command_completed", None

        elif cmd == "/opencode-status":
            yield "command_started", None
            if not self._oc:
                yield "command_failed", "OpenCode supervisor not available"
                return
            try:
                status = await self._oc.get_status()
                lines = [
                    f"Server: {'alive' if status['server_alive'] else 'dead'} ({status['server_url']})",
                    f"Running tasks: {len(status['running_tasks'])}",
                    f"Pending questions: {len(status['pending_questions'])}",
                ]
                for t in status["running_tasks"]:
                    lines.append(f"  {t['task_id'][:12]} -> session {t['session_id'][:16]} ({t['status']})")
                for q in status["pending_questions"]:
                    lines.append(f"  Q: {q['question'][:60]} (id={q['question_id'][:12]})")
                yield "command_output", "\n".join(lines)
                yield "command_completed", None
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/opencode-start":
            yield "command_started", "Starting OpenCode session..."
            if not self._oc:
                yield "command_failed", "OpenCode supervisor not available"
                return
            if not args.strip():
                yield "command_failed", "Usage: /opencode-start <project_directory> <instruction>"
                return
            sub = args.strip().split(maxsplit=1)
            if len(sub) < 2:
                yield "command_failed", "Usage: /opencode-start <project_directory> <instruction>"
                return
            project_dir = sub[0]
            instruction = sub[1]
            try:
                result = await self._oc.start_session(project_dir, instruction)
                yield "command_output", (
                    f"OpenCode session started: task_id={result['task_id'][:12]}, "
                    f"session_id={result['session_id'][:16]}"
                )
                yield "command_completed", "Session running in background"
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/opencode-cancel":
            if not args.strip():
                yield "command_failed", "Usage: /opencode-cancel <task_id>"
                return
            task_id = args.strip()
            yield "command_started", f"Cancelling OpenCode task {task_id[:12]}..."
            if not self._oc:
                yield "command_failed", "OpenCode supervisor not available"
                return
            try:
                msg = await self._oc.cancel_session(task_id)
                yield "command_output", msg
                yield "command_completed", None
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/opencode-answer":
            if not args.strip():
                yield "command_failed", "Usage: /opencode-answer <question_id> <answer>"
                return
            sub = args.strip().split(maxsplit=1)
            if len(sub) < 2:
                yield "command_failed", "Usage: /opencode-answer <question_id> <answer>"
                return
            question_id = sub[0]
            answer = sub[1]
            yield "command_started", f"Answering OpenCode question {question_id[:12]}..."
            if not self._oc:
                yield "command_failed", "OpenCode supervisor not available"
                return
            try:
                msg = await self._oc.answer_question(question_id, answer)
                yield "command_output", msg
                yield "command_completed", None
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/opencode-reject":
            if not args.strip():
                yield "command_failed", "Usage: /opencode-reject <question_id>"
                return
            question_id = args.strip()
            yield "command_started", f"Rejecting OpenCode question {question_id[:12]}..."
            if not self._oc:
                yield "command_failed", "OpenCode supervisor not available"
                return
            try:
                msg = await self._oc.reject_question(question_id)
                yield "command_output", msg
                yield "command_completed", None
            except Exception as e:
                yield "command_failed", str(e)

        elif cmd == "/opencode-permit":
            if not args.strip():
                yield "command_failed", "Usage: /opencode-permit <permission_id> [approve|deny]"
                return
            sub = args.strip().split(maxsplit=1)
            permission_id = sub[0]
            approve = True
            if len(sub) > 1:
                approve = sub[1].lower() in ("approve", "yes", "true", "1")
            yield "command_started", f"Responding to OpenCode permission {permission_id[:12]}..."
            if not self._oc:
                yield "command_failed", "OpenCode supervisor not available"
                return
            try:
                msg = await self._oc.approve_permission(permission_id, approve)
                yield "command_output", msg
                yield "command_completed", None
            except Exception as e:
                yield "command_failed", str(e)

        else:
            yield "command_failed", (
                f"Unknown command: {command}. "
                f"Supported: /status, /processes, /pwd, /demo-task, /tasks, /cancel, "
                f"/mock-agent, /answer, /attention, /opencode-start, /opencode-cancel, "
                f"/opencode-answer, /opencode-reject, /opencode-permit, /opencode-status"
            )
