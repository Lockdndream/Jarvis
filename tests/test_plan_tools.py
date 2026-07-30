import os
import sys
import tempfile
import uuid
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.supervisor.tools import ToolRegistry


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


def make_plan_id():
    return f"plan_{uuid.uuid4().hex[:12]}"


def make_step_id():
    return f"step_{uuid.uuid4().hex[:12]}"


def make_mock_plan_executor():
    pe = MagicMock()
    pe.start_plan = AsyncMock()
    pe.resume_plan = AsyncMock()
    return pe


def make_registry(plan_executor=None):
    return ToolRegistry(plan_executor=plan_executor)


def seed_plan(plan_id=None, title="Test plan", status="pending"):
    if plan_id is None:
        plan_id = make_plan_id()
    db.create_plan_record(plan_id, title)
    if status != "pending":
        db.update_plan_status(plan_id, status)
    return plan_id


def seed_step(plan_id, step_index, description, worker_name="opencode",
              on_failure="stop", verification=None, status=None, error=None):
    step_id = make_step_id()
    db.create_plan_step_record(step_id, plan_id, step_index, description,
                               worker_name, on_failure=on_failure,
                               verification=verification)
    if status:
        db.update_plan_step(step_id, status=status, error=error)
    return step_id


# ── create_plan ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_plan_creates_records():
    registry = make_registry(make_mock_plan_executor())
    result = await registry.call("create_plan", {
        "title": "Deploy",
        "steps": [
            {"description": "Run tests", "worker_name": "opencode"},
            {"description": "Check lint", "worker_name": "strategist", "on_failure": "escalate"},
            {"description": "Verify deploy", "worker_name": "opencode", "verification": "curl health"},
        ],
    })
    assert "created with 3 steps" in result
    plan_id = result.split("'")[1]

    plan = db.get_plan(plan_id)
    assert plan is not None
    assert plan["title"] == "Deploy"
    assert plan["status"] == "pending"

    steps = db.get_plan_steps(plan_id)
    assert len(steps) == 3
    assert steps[0]["description"] == "Run tests"
    assert steps[0]["worker_name"] == "opencode"
    assert steps[0]["on_failure"] == "stop"
    assert steps[1]["description"] == "Check lint"
    assert steps[1]["worker_name"] == "strategist"
    assert steps[1]["on_failure"] == "escalate"
    assert steps[2]["verification"] == "curl health"


@pytest.mark.asyncio
async def test_create_plan_defaults_on_failure():
    registry = make_registry(make_mock_plan_executor())
    result = await registry.call("create_plan", {
        "title": "Simple",
        "steps": [{"description": "Do it", "worker_name": "opencode"}],
    })
    plan_id = result.split("'")[1]
    steps = db.get_plan_steps(plan_id)
    assert steps[0]["on_failure"] == "stop"


@pytest.mark.asyncio
async def test_create_plan_no_executor():
    registry = make_registry(plan_executor=None)
    result = await registry.call("create_plan", {
        "title": "X", "steps": [],
    })
    assert "plan executor not available" in result


@pytest.mark.asyncio
async def test_create_plan_rejects_invalid_on_failure():
    registry = make_registry(make_mock_plan_executor())
    result = await registry.call("create_plan", {
        "title": "Bad plan",
        "steps": [{"description": "Do it", "worker_name": "opencode",
                   "on_failure": "halt"}],
    })
    assert "invalid on_failure" in result
    assert "halt" in result
    # No plan record should have been created — validation happens
    # before any DB writes.
    assert db.get_recent_plans(10) == []


# ── start_plan ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_plan_success():
    pe = make_mock_plan_executor()
    pe.start_plan.return_value = True
    registry = make_registry(pe)

    plan_id = seed_plan()
    result = await registry.call("start_plan", {"plan_id": plan_id})
    assert "running in the background" in result
    pe.start_plan.assert_called_once_with(plan_id)


@pytest.mark.asyncio
async def test_start_plan_already_running():
    pe = make_mock_plan_executor()
    pe.start_plan.return_value = False
    registry = make_registry(pe)

    plan_id = seed_plan()
    result = await registry.call("start_plan", {"plan_id": plan_id})
    assert "Could not start" in result
    pe.start_plan.assert_called_once_with(plan_id)


@pytest.mark.asyncio
async def test_start_plan_no_executor():
    registry = make_registry(plan_executor=None)
    result = await registry.call("start_plan", {"plan_id": "plan_xxx"})
    assert "plan executor not available" in result


# ── plan_status ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_status_with_steps():
    registry = make_registry(make_mock_plan_executor())
    plan_id = seed_plan("Deploy app")
    seed_step(plan_id, 0, "Run unit tests", "opencode")
    seed_step(plan_id, 1, "Deploy", "opencode")

    result = await registry.call("plan_status", {"plan_id": plan_id})
    assert plan_id in result
    assert "Deploy app" in result
    assert "Run unit tests" in result
    assert "Deploy" in result
    assert "pending" in result


@pytest.mark.asyncio
async def test_plan_status_with_failed_step():
    registry = make_registry(make_mock_plan_executor())
    plan_id = seed_plan()
    seed_step(plan_id, 0, "First step", "opencode", status="failed",
              error="connection refused")

    result = await registry.call("plan_status", {"plan_id": plan_id})
    assert "failed" in result
    assert "connection refused" in result


@pytest.mark.asyncio
async def test_plan_status_does_not_truncate_long_error():
    registry = make_registry(make_mock_plan_executor())
    plan_id = seed_plan()
    long_error = "Traceback (most recent call last):\n" + ("x" * 200) + "\nAssertionError: boom"
    seed_step(plan_id, 0, "First step", "opencode", status="failed",
              error=long_error)

    result = await registry.call("plan_status", {"plan_id": plan_id})
    assert long_error in result


@pytest.mark.asyncio
async def test_plan_status_not_found():
    registry = make_registry(make_mock_plan_executor())
    result = await registry.call("plan_status", {"plan_id": "plan_nonexistent"})
    assert "not found" in result


@pytest.mark.asyncio
async def test_plan_status_no_executor():
    registry = make_registry(plan_executor=None)
    result = await registry.call("plan_status", {"plan_id": "plan_xxx"})
    assert "plan executor not available" in result


# ── resume_plan ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_plan_success():
    pe = make_mock_plan_executor()
    pe.resume_plan.return_value = None
    registry = make_registry(pe)

    result = await registry.call("resume_plan", {
        "plan_id": "plan_xxx", "instruction": "skip",
    })
    assert "resumed" in result
    pe.resume_plan.assert_called_once_with("plan_xxx", "skip")


@pytest.mark.asyncio
async def test_resume_plan_invalid_instruction():
    pe = make_mock_plan_executor()
    pe.resume_plan.side_effect = ValueError("Invalid resume instruction: 'jump'")
    registry = make_registry(pe)

    result = await registry.call("resume_plan", {
        "plan_id": "plan_xxx", "instruction": "jump",
    })
    assert "Invalid resume instruction" in result
    assert "Error executing" not in result


@pytest.mark.asyncio
async def test_resume_plan_not_paused():
    pe = make_mock_plan_executor()
    pe.resume_plan.side_effect = ValueError(
        "Plan 'plan_xxx' is not paused (status=completed)"
    )
    registry = make_registry(pe)

    result = await registry.call("resume_plan", {
        "plan_id": "plan_xxx", "instruction": "retry",
    })
    assert "not paused" in result
    assert "Error executing" not in result


@pytest.mark.asyncio
async def test_resume_plan_no_executor():
    registry = make_registry(plan_executor=None)
    result = await registry.call("resume_plan", {
        "plan_id": "plan_xxx", "instruction": "retry",
    })
    assert "plan executor not available" in result


# ── list_plans ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_plans_returns_recent():
    registry = make_registry(make_mock_plan_executor())
    p1 = seed_plan(make_plan_id(), "Alpha")
    p2 = seed_plan(make_plan_id(), "Beta")

    result = await registry.call("list_plans", {"count": 10})
    assert p1 in result
    assert p2 in result
    assert "Alpha" in result
    assert "Beta" in result


@pytest.mark.asyncio
async def test_list_plans_empty():
    registry = make_registry(make_mock_plan_executor())
    result = await registry.call("list_plans", {"count": 10})
    assert "No plans found" in result


@pytest.mark.asyncio
async def test_list_plans_no_executor():
    registry = make_registry(plan_executor=None)
    result = await registry.call("list_plans", {"count": 10})
    assert "plan executor not available" in result


# ── Tool definitions include plan tools ──────────────────────────────


def test_plan_tools_in_definitions():
    registry = make_registry(make_mock_plan_executor())
    defs = registry.list_definitions()
    names = {d["name"] for d in defs}
    assert "create_plan" in names
    assert "start_plan" in names
    assert "plan_status" in names
    assert "resume_plan" in names
    assert "list_plans" in names
