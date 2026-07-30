"""End-to-end plan executor test — exercises the full chain:
Supervisor tool calls -> PlanExecutor -> real SQLite DB -> attention_manager,
with strategist subprocess mocked for CI safety.
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.plan_executor import PlanExecutor
from app.supervisor.tools import ToolRegistry
from app.workers.base import WorkerRegistry
from app.workers.strategist_worker import StrategistWorker


@pytest.fixture(autouse=True)
def test_db():
    old_path = db.DB_PATH
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = path
    db.init_db()
    yield
    db.DB_PATH = old_path
    try:
        if os.path.exists(path):
            os.unlink(path)
    except PermissionError:
        pass


async def _wait_plan_done(pe, plan_id, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while plan_id in pe._running_plans:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"plan task stuck after {timeout}s")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_plan_e2e_lifecycle():
    worker_registry = WorkerRegistry()
    worker_registry.register(StrategistWorker())

    conn_manager = MagicMock()
    pe = PlanExecutor(
        worker_registry=worker_registry,
        opencode_supervisor=MagicMock(),
        conn_manager=conn_manager,
        step_timeout=5.0,
    )

    registry = ToolRegistry(
        worker_registry=worker_registry,
        plan_executor=pe,
    )

    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"2+2 equals 4", b""))
    fake_proc.returncode = 0

    with patch(
        "app.workers.strategist_worker.asyncio.create_subprocess_exec",
        return_value=fake_proc,
    ):
        result = await registry.call("create_plan", {
            "title": "E2E Test Plan",
            "steps": [
                {"description": "what is 2+2", "worker_name": "strategist"},
                {"description": "summarize the answer", "worker_name": "strategist"},
                {"description": "this step is designed to fail", "worker_name": "broken_worker", "on_failure": "escalate"},
            ],
        })
        assert "created with 3 steps" in result
        plan_id = result.split("'")[1]

        result = await registry.call("start_plan", {"plan_id": plan_id})
        assert "running in the background" in result

        await _wait_plan_done(pe, plan_id)

        plan = db.get_plan(plan_id)
        assert plan["status"] == "paused"

        steps = db.get_plan_steps(plan_id)
        assert len(steps) == 3

        assert steps[0]["status"] == "succeeded"
        assert steps[0]["result"] and len(steps[0]["result"]) > 0
        assert "2+2" in steps[0]["result"] or "4" in steps[0]["result"]

        assert steps[1]["status"] == "succeeded"
        assert steps[1]["result"] and len(steps[1]["result"]) > 0

        assert steps[2]["status"] == "failed"
        assert "not registered" in steps[2]["error"]

        step3_id = steps[2]["step_id"]
        attn = db.get_attention_request_by_source("plan_step", step3_id)
        assert attn is not None
        assert attn["attention_type"] == "SUPERVISOR_ESCALATION"
        assert attn["task_id"] == plan_id

        result = await registry.call("resume_plan", {
            "plan_id": plan_id,
            "instruction": "skip",
        })
        assert "resumed" in result

        await _wait_plan_done(pe, plan_id)

        plan = db.get_plan(plan_id)
        assert plan["status"] == "completed"

        steps = db.get_plan_steps(plan_id)
        assert steps[2]["status"] == "skipped"

        result = await registry.call("plan_status", {"plan_id": plan_id})
        assert "E2E Test Plan" in result
        assert "strategist" in result
        assert "succeeded" in result
        assert "broken_worker" in result
        assert "skipped" in result
