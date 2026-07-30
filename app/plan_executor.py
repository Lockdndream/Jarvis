"""Plan executor — chains multiple worker dispatches into a sequential,
persisted, restart-resilient workflow running in the background."""
import asyncio
import json
import logging

from app import config
from app import db_async as adb
from app import attention_manager

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    from app.database import utcnow
    return utcnow()


class PlanExecutor:
    def __init__(
        self,
        worker_registry,
        opencode_supervisor,
        conn_manager,
        step_timeout: float | None = None,
        clock=None,
    ):
        self._workers = worker_registry
        self._oc = opencode_supervisor
        self._cm = conn_manager
        self._step_timeout = step_timeout if step_timeout is not None else float(
            config.plan_step_timeout_seconds()
        )
        self._clock = clock
        self._running_plans: dict[str, asyncio.Task] = {}
        self._stopped = False

    def _now(self) -> str | None:
        return self._clock() if self._clock else None

    async def start(self) -> None:
        self._stopped = False
        await self.reconcile_on_startup()
        logger.info("plan executor started (step_timeout=%ss)", self._step_timeout)

    async def stop(self) -> None:
        self._stopped = True
        tasks = list(self._running_plans.values())
        for t in tasks:
            t.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if r is not None and not isinstance(r, asyncio.CancelledError):
                logger.warning("plan executor task raised: %s", r)
        self._running_plans.clear()
        logger.info("plan executor stopped")

    # ── Start / execution ──────────────────────────────────────────────

    async def start_plan(self, plan_id: str) -> bool:
        if self._stopped:
            return False
        if not await adb.claim_plan_start(plan_id):
            return False
        logger.info("plan started: plan_id=%s", plan_id)
        task = asyncio.create_task(self._run_plan(plan_id))
        self._running_plans[plan_id] = task
        return True

    async def _run_plan(self, plan_id: str) -> None:
        plan = await adb.get_plan(plan_id)
        if not plan:
            logger.error("plan disappeared mid-execution: plan_id=%s", plan_id)
            self._running_plans.pop(plan_id, None)
            return
        steps = await adb.get_plan_steps(plan_id)
        try:
            start_idx = plan["current_step_index"]
            for step in steps[start_idx:]:
                if step["status"] in ("succeeded", "skipped"):
                    continue
                if step["status"] == "failed":
                    if step["on_failure"] == "stop":
                        await adb.update_plan_status(plan_id, "failed")
                        logger.info(
                            "plan failed (stop on failure): plan_id=%s step_id=%s",
                            plan_id, step["step_id"],
                        )
                        self._running_plans.pop(plan_id, None)
                        return
                    if step["on_failure"] == "escalate":
                        await adb.update_plan_status(plan_id, "paused")
                        await self._escalate(plan_id, step)
                        logger.info(
                            "plan paused for escalation: plan_id=%s step_id=%s",
                            plan_id, step["step_id"],
                        )
                        self._running_plans.pop(plan_id, None)
                        return
                else:
                    await self._run_step(plan_id, step)
                    refreshed = await adb.get_plan_step(step["step_id"])
                    if refreshed is None:
                        logger.error(
                            "plan step disappeared mid-execution: plan_id=%s step_id=%s",
                            plan_id, step["step_id"],
                        )
                        await adb.update_plan_status(plan_id, "failed")
                        self._running_plans.pop(plan_id, None)
                        return
                    step = refreshed
                    if step["status"] == "failed":
                        if step["on_failure"] == "stop":
                            await adb.update_plan_status(plan_id, "failed")
                            logger.info(
                                "plan failed (stop on failure): plan_id=%s step_id=%s",
                                plan_id, step["step_id"],
                            )
                            self._running_plans.pop(plan_id, None)
                            return
                        if step["on_failure"] == "escalate":
                            await adb.update_plan_status(plan_id, "paused")
                            await self._escalate(plan_id, step)
                            logger.info(
                                "plan paused for escalation: plan_id=%s step_id=%s",
                                plan_id, step["step_id"],
                            )
                            self._running_plans.pop(plan_id, None)
                            return
                await adb.update_plan_status(
                    plan_id, "running",
                    current_step_index=step["step_index"] + 1,
                )
            await adb.update_plan_status(plan_id, "completed")
            logger.info("plan completed: plan_id=%s", plan_id)
        finally:
            self._running_plans.pop(plan_id, None)

    async def _run_step(self, plan_id: str, step: dict) -> None:
        step_id = step["step_id"]
        worker = self._workers.get_by_name(step["worker_name"])
        if worker is None:
            await adb.update_plan_step(
                step_id, status="failed",
                error=f"worker '{step['worker_name']}' not registered",
                completed_at=_utcnow(),
            )
            return

        await adb.update_plan_step(
            step_id, status="running", started_at=_utcnow(),
        )

        try:
            result = await worker.invoke(step["description"])
        except Exception as e:
            await adb.update_plan_step(
                step_id, status="failed",
                error=str(e), completed_at=_utcnow(),
            )
            return

        output = None
        if result.status.value == "failed":
            await adb.update_plan_step(
                step_id, status="failed",
                error=result.error, completed_at=_utcnow(),
            )
            return
        elif result.status.value == "dispatched":
            await adb.update_plan_step(
                step_id, worker_task_id=result.task_id,
            )
            completed = await self._oc.wait_for_completion(
                result.task_id, timeout=self._step_timeout,
            )
            if not completed:
                await adb.update_plan_step(
                    step_id, status="failed",
                    error=f"OpenCode task timed out after {self._step_timeout}s",
                    completed_at=_utcnow(),
                )
                return
            oc_task = await adb.get_opencode_task(result.task_id)
            if oc_task is None or oc_task["status"] == "failed":
                error_msg = (
                    oc_task.get("result_summary") or "OpenCode task failed"
                    if oc_task else "OpenCode task record not found"
                )
                await adb.update_plan_step(
                    step_id, status="failed",
                    error=error_msg, completed_at=_utcnow(),
                )
                return
            output = oc_task.get("result_summary") or await self._oc.fetch_task_result_text(
                result.task_id,
            )
        elif result.status.value == "completed":
            output = result.output
        else:
            await adb.update_plan_step(
                step_id, status="failed",
                error=f"unexpected worker result status: {result.status.value}",
                completed_at=_utcnow(),
            )
            return

        if step.get("verification"):
            try:
                ver_result = await worker.invoke(step["verification"])
            except Exception as e:
                await adb.update_plan_step(
                    step_id, status="failed",
                    error=f"verification failed: {e}",
                    completed_at=_utcnow(),
                )
                return

            if ver_result.status.value == "dispatched":
                await adb.update_plan_step(
                    step_id, verification_task_id=ver_result.task_id,
                )
                ver_completed = await self._oc.wait_for_completion(
                    ver_result.task_id, timeout=self._step_timeout,
                )
                if not ver_completed:
                    await adb.update_plan_step(
                        step_id, status="failed",
                        error=f"verification timed out after {self._step_timeout}s",
                        completed_at=_utcnow(),
                    )
                    return
                ver_oc_task = await adb.get_opencode_task(ver_result.task_id)
                if ver_oc_task is None or ver_oc_task["status"] == "failed":
                    await adb.update_plan_step(
                        step_id, status="failed",
                        error="verification OpenCode task failed",
                        verification_error="verification OpenCode task failed",
                        completed_at=_utcnow(),
                    )
                    return
                ver_output = ver_oc_task.get("result_summary") or await self._oc.fetch_task_result_text(
                    ver_result.task_id,
                )
                await adb.update_plan_step(
                    step_id, verification_result=ver_output,
                )
            elif ver_result.status.value == "failed":
                await adb.update_plan_step(
                    step_id, status="failed",
                    error=f"verification failed: {ver_result.error}",
                    verification_error=ver_result.error,
                    completed_at=_utcnow(),
                )
                return
            else:
                await adb.update_plan_step(
                    step_id, verification_result=ver_result.output,
                )

        await adb.update_plan_step(
            step_id, status="succeeded",
            result=output, completed_at=_utcnow(),
        )

    # ── Escalation ──────────────────────────────────────────────────────

    async def _escalate(self, plan_id: str, step: dict) -> None:
        await attention_manager.get_or_create(
            self._cm,
            conversation_id=None,
            task_id=plan_id,
            source_type="plan_step",
            source_id=step["step_id"],
            attention_type="SUPERVISOR_ESCALATION",
            urgency="HIGH",
            summary=f"Plan step failed: {step['description'][:100]}",
            context_json=json.dumps({
                "plan_id": plan_id,
                "step_id": step["step_id"],
                "step_description": step["description"],
                "error": step.get("error"),
            }),
        )

    # ── Resume ───────────────────────────────────────────────────────────

    async def resume_plan(self, plan_id: str, instruction: str) -> None:
        if instruction not in ("retry", "skip", "abort"):
            raise ValueError(
                f"Invalid resume instruction: '{instruction}'. "
                "Must be one of: retry, skip, abort"
            )
        if self._stopped and instruction != "abort":
            raise ValueError(
                "Plan executor is shut down; only 'abort' is accepted"
            )
        plan = await adb.get_plan(plan_id)
        if not plan or plan["status"] != "paused":
            raise ValueError(
                f"Plan '{plan_id}' is not paused (status={plan['status'] if plan else 'not found'})"
            )

        if instruction == "abort":
            await adb.update_plan_status(plan_id, "failed")
            logger.info("plan aborted: plan_id=%s", plan_id)
            return

        if instruction == "skip":
            steps = await adb.get_plan_steps(plan_id)
            current = steps[plan["current_step_index"]]
            await adb.update_plan_step(current["step_id"], status="skipped")
            await adb.update_plan_status(
                plan_id, "running",
                current_step_index=plan["current_step_index"] + 1,
            )
            logger.info("plan step skipped, resuming: plan_id=%s step_id=%s",
                        plan_id, current["step_id"])
        elif instruction == "retry":
            steps = await adb.get_plan_steps(plan_id)
            current = steps[plan["current_step_index"]]
            await adb.update_plan_step(current["step_id"], status="pending")
            await adb.update_plan_status(plan_id, "running")
            logger.info("plan step retrying, resuming: plan_id=%s step_id=%s",
                        plan_id, current["step_id"])

        task = asyncio.create_task(self._run_plan(plan_id))
        self._running_plans[plan_id] = task

    # ── Restart recovery ────────────────────────────────────────────────

    async def reconcile_on_startup(self) -> None:
        running_plans = await adb.get_plans_by_status("running")
        if not running_plans:
            return
        logger.info("plan executor recovery: %d running plan(s) found at startup", len(running_plans))
        for plan in running_plans:
            plan_id = plan["plan_id"]
            steps = await adb.get_plan_steps(plan_id)
            if plan["current_step_index"] >= len(steps):
                # Crash/kill landed between the last step's index-advance
                # write and the plan's own "completed" write -- the work
                # itself is done, only the terminal status write is missing.
                await adb.update_plan_status(plan_id, "completed")
                logger.info(
                    "plan executor recovery: plan_id=%s had already finished "
                    "all steps before restart, marking completed", plan_id,
                )
                continue
            current = steps[plan["current_step_index"]]
            if current["status"] == "running":
                if not current["worker_task_id"]:
                    await adb.update_plan_step(
                        current["step_id"], status="failed",
                        error="step state unknown after restart (no worker task recorded)",
                    )
                else:
                    oc_task = await adb.get_opencode_task(current["worker_task_id"])
                    if oc_task is None:
                        await adb.update_plan_step(
                            current["step_id"], status="failed",
                            error="OpenCode task record not found after restart",
                        )
                    elif oc_task["status"] == "completed":
                        await adb.update_plan_step(
                            current["step_id"], status="succeeded",
                            result=oc_task.get("result_summary"),
                            completed_at=_utcnow(),
                        )
                    elif oc_task["status"] == "failed":
                        await adb.update_plan_step(
                            current["step_id"], status="failed",
                            error=oc_task.get("result_summary") or "OpenCode task failed",
                            completed_at=_utcnow(),
                        )
                    else:
                        await adb.update_plan_step(
                            current["step_id"], status="failed",
                            error=f"OpenCode task state unverifiable after restart (status={oc_task['status']})",
                        )
            task = asyncio.create_task(self._run_plan(plan_id))
            self._running_plans[plan_id] = task
