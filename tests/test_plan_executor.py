"""Tests for PlanExecutor — sequential plan execution with persistence and restart recovery."""
import asyncio
import json
import os
import sys
import tempfile
import uuid
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.workers.base import WorkerResult, WorkerResultStatus, WorkerStatus
from app.plan_executor import PlanExecutor
import app.memory as memory


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

def make_task_id():
    return f"oc_{uuid.uuid4().hex[:12]}"


async def _wait_plan_done(pe, plan_id, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while plan_id in pe._running_plans:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"plan task stuck after {timeout}s")
        await asyncio.sleep(0.01)


def _make_worker(name, fn):
    from unittest.mock import MagicMock
    w = MagicMock()
    w.name = name
    w.capabilities = ["test"]
    w.invoke = fn
    w.status = _async_return(WorkerStatus.AVAILABLE)
    return w


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f


def seed_plan(title="Test plan", context=None):
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, title, context_json=context)
    return plan_id

def seed_step(plan_id, step_index, description, worker_name="opencode",
              on_failure="stop", verification=None):
    step_id = make_step_id()
    db.create_plan_step_record(step_id, plan_id, step_index, description,
                               worker_name, on_failure=on_failure,
                               verification=verification)
    return step_id

def seed_opencode_task(task_id, status="completed", result_summary=None, project_dir="."):
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    db.create_task_record(task_id, "OpenCode: test", "test instruction")
    db.create_opencode_task_record(task_id, session_id, project_dir)
    if status != "running":
        db.update_opencode_task_status(task_id, status)
    if result_summary:
        db.update_opencode_task_result(task_id, result_summary)

# ── Happy path ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_step_plan_completes():
    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    oc = MagicMock()
    oc.wait_for_completion = _async_return(True)
    oc.fetch_task_result_text = _async_return(None)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=oc,
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Two-step")
    seed_step(plan_id, 0, "Step 1", "opencode")
    seed_step(plan_id, 1, "Step 2", "opencode")

    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    plan = db.get_plan(plan_id)
    assert plan["status"] == "completed"
    steps = db.get_plan_steps(plan_id)
    assert steps[0]["status"] == "succeeded"
    assert steps[0]["result"] == "done"
    assert steps[1]["status"] == "succeeded"
    assert steps[1]["result"] == "done"


@pytest.mark.asyncio
async def test_step_worker_not_registered_fails():
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=None)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Bad worker")
    seed_step(plan_id, 0, "Step 1", "nonexistent")

    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    plan = db.get_plan(plan_id)
    assert plan["status"] == "failed"
    step = db.get_plan_steps(plan_id)[0]
    assert step["status"] == "failed"
    assert "not registered" in step["error"]


@pytest.mark.asyncio
async def test_worker_exception_fails_step():
    async def _raise(desc, **kw):
        raise RuntimeError("boom")

    w = _make_worker("opencode", _raise)
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Exception")
    seed_step(plan_id, 0, "Boom", "opencode")

    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    step = db.get_plan_steps(plan_id)[0]
    assert step["status"] == "failed"
    assert "boom" in step["error"]


# ── DISPATCHED handling ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatched_persists_task_id_before_wait():
    plan_id = seed_plan("Dispatched")
    step_id = seed_step(plan_id, 0, "Do dispatched work", "opencode")
    task_id = make_task_id()
    seed_opencode_task(task_id, status="completed", result_summary="all good")

    capture = []
    async def slow_wait(tid, **kw):
        step = db.get_plan_step(step_id)
        capture.append(step)
        return True

    w = MagicMock()
    w.name = "opencode"
    w.capabilities = ["test"]
    w.invoke = _async_return(WorkerResult(
        status=WorkerResultStatus.DISPATCHED, task_id=task_id))
    w.status = _async_return(WorkerStatus.AVAILABLE)

    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    oc = MagicMock()
    oc.wait_for_completion = slow_wait

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=oc,
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    assert len(capture) == 1
    assert capture[0]["worker_task_id"] == task_id
    assert capture[0]["status"] == "running"

    step = db.get_plan_step(step_id)
    assert step["status"] == "succeeded"
    assert step["result"] == "all good"


@pytest.mark.asyncio
async def test_dispatched_opencode_task_failed():
    plan_id = seed_plan("Dispatched fail")
    seed_step(plan_id, 0, "Do fail work", "opencode", on_failure="stop")
    task_id = make_task_id()
    seed_opencode_task(task_id, status="failed", result_summary="broken")

    w = MagicMock()
    w.name = "opencode"
    w.capabilities = ["test"]
    w.invoke = _async_return(WorkerResult(
        status=WorkerResultStatus.DISPATCHED, task_id=task_id))
    w.status = _async_return(WorkerStatus.AVAILABLE)

    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    oc = MagicMock()
    oc.wait_for_completion = _async_return(True)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=oc,
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    plan = db.get_plan(plan_id)
    assert plan["status"] == "failed"
    step = db.get_plan_steps(plan_id)[0]
    assert step["status"] == "failed"
    assert step["error"] == "broken"


@pytest.mark.asyncio
async def test_dispatched_wait_for_completion_timeout():
    plan_id = seed_plan("Timeout")
    seed_step(plan_id, 0, "Long work", "opencode")
    task_id = make_task_id()

    w = MagicMock()
    w.name = "opencode"
    w.capabilities = ["test"]
    w.invoke = _async_return(WorkerResult(
        status=WorkerResultStatus.DISPATCHED, task_id=task_id))
    w.status = _async_return(WorkerStatus.AVAILABLE)

    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    oc = MagicMock()
    oc.wait_for_completion = _async_return(False)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=oc,
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    step = db.get_plan_steps(plan_id)[0]
    assert step["status"] == "failed"
    assert "timed out" in step["error"]

# -- on_failure policies --

@pytest.mark.asyncio
async def test_on_failure_continue_plan_proceeds():
    plan_id = seed_plan("Continue on fail")
    seed_step(plan_id, 0, "Failing step", "opencode", on_failure="continue")
    seed_step(plan_id, 1, "Good step", "opencode")

    calls = []
    async def inv(desc, **kw):
        calls.append(desc)
        if len(calls) == 1:
            return WorkerResult(status=WorkerResultStatus.FAILED, error="oops")
        return WorkerResult(status=WorkerResultStatus.COMPLETED, output="step2 ok")

    w = MagicMock()
    w.name = "opencode"
    w.capabilities = ["test"]
    w.invoke = inv
    w.status = _async_return(WorkerStatus.AVAILABLE)
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    plan = db.get_plan(plan_id)
    assert plan["status"] == "completed"
    steps = db.get_plan_steps(plan_id)
    assert steps[0]["status"] == "failed"
    assert steps[1]["status"] == "succeeded"



@pytest.mark.asyncio
async def test_on_failure_stop_halts_plan():
    plan_id = seed_plan("Stop on fail")
    seed_step(plan_id, 0, "Fatal step", "opencode", on_failure="stop")
    seed_step(plan_id, 1, "Never runs", "opencode")

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.FAILED, error="fatal")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    plan = db.get_plan(plan_id)
    assert plan["status"] == "failed"
    steps = db.get_plan_steps(plan_id)
    assert steps[0]["status"] == "failed"
    assert steps[1]["status"] == "pending"


@pytest.mark.asyncio
async def test_on_failure_escalate_pauses_and_creates_attention():
    plan_id = seed_plan("Escalate test")
    step_id = seed_step(plan_id, 0, "Needs human judgment", "opencode",
                        on_failure="escalate")

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.FAILED, error="needs human")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    cm = MagicMock()

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=cm, step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    plan = db.get_plan(plan_id)
    assert plan["status"] == "paused"

    attn = db.get_attention_request_by_source("plan_step", step_id)
    assert attn is not None
    assert attn["attention_type"] == "SUPERVISOR_ESCALATION"
    assert attn["task_id"] == plan_id
    assert attn["source_type"] == "plan_step"
    assert attn["source_id"] == step_id
    assert attn["urgency"] == "HIGH"
    ctx = json.loads(attn["context_json"])
    assert ctx["plan_id"] == plan_id
    assert ctx["step_id"] == step_id
    assert ctx["step_description"] == "Needs human judgment"
    assert ctx["error"] == "needs human"
    assert plan_id not in pe._running_plans



@pytest.mark.asyncio
async def test_verification_passed_succeeds_step():
    calls = []
    async def inv(desc, **kw):
        calls.append(desc)
        return WorkerResult(status=WorkerResultStatus.COMPLETED, output=desc + "_result")

    w = MagicMock()
    w.name = "opencode"
    w.capabilities = ["test"]
    w.invoke = inv
    w.status = _async_return(WorkerStatus.AVAILABLE)
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Verify test")
    seed_step(plan_id, 0, "Do work", "opencode", verification="Verify work")

    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    assert len(calls) == 2
    assert calls[0] == "Do work"
    assert calls[1] == "Verify work"
    step = db.get_plan_steps(plan_id)[0]
    assert step["status"] == "succeeded"


@pytest.mark.asyncio
async def test_verification_failed_marks_step_failed():
    count = [0]
    async def inv(desc, **kw):
        count[0] += 1
        if count[0] == 1:
            return WorkerResult(status=WorkerResultStatus.COMPLETED, output="main ok")
        return WorkerResult(status=WorkerResultStatus.FAILED, error="tests failed")

    w = MagicMock()
    w.name = "opencode"
    w.capabilities = ["test"]
    w.invoke = inv
    w.status = _async_return(WorkerStatus.AVAILABLE)
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Verification fail")
    seed_step(plan_id, 0, "Do work", "opencode", verification="Verify work")

    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    step = db.get_plan_steps(plan_id)[0]
    assert step["status"] == "failed"
    assert step["verification_error"] == "tests failed"



@pytest.mark.asyncio
async def test_resume_retry_reruns_same_step():
    plan_id = seed_plan("Retry test")
    step_id = seed_step(plan_id, 0, "Transient fail", "opencode",
                        on_failure="escalate")

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.FAILED, error="temp error")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)
    assert db.get_plan(plan_id)["status"] == "paused"

    w2 = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="retry ok")))
    reg.get_by_name = MagicMock(return_value=w2)
    await pe.resume_plan(plan_id, "retry")
    await _wait_plan_done(pe, plan_id)

    assert db.get_plan(plan_id)["status"] == "completed"
    step = db.get_plan_step(step_id)
    assert step["status"] == "succeeded"
    assert step["result"] == "retry ok"


@pytest.mark.asyncio
async def test_resume_skip_skips_step():
    plan_id = seed_plan("Skip test")
    step1_id = seed_step(plan_id, 0, "Bad step", "opencode", on_failure="escalate")
    step2_id = seed_step(plan_id, 1, "Good step", "opencode")

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.FAILED, error="bad")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)
    assert db.get_plan(plan_id)["status"] == "paused"

    w2 = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="step2 good")))
    reg.get_by_name = MagicMock(return_value=w2)
    await pe.resume_plan(plan_id, "skip")
    await _wait_plan_done(pe, plan_id)

    plan = db.get_plan(plan_id)
    assert plan["status"] == "completed"
    assert db.get_plan_step(step1_id)["status"] == "skipped"
    assert db.get_plan_step(step2_id)["status"] == "succeeded"


@pytest.mark.asyncio
async def test_resume_abort_fails_plan():
    plan_id = seed_plan("Abort test")
    seed_step(plan_id, 0, "Bad step", "opencode", on_failure="escalate")

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.FAILED, error="bad")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)
    assert db.get_plan(plan_id)["status"] == "paused"

    await pe.resume_plan(plan_id, "abort")
    assert db.get_plan(plan_id)["status"] == "failed"


@pytest.mark.asyncio
async def test_resume_invalid_instruction_raises():
    plan_id = seed_plan("Bad resume")
    seed_step(plan_id, 0, "Step", "opencode", on_failure="escalate")
    db.update_plan_status(plan_id, "paused")

    pe = PlanExecutor(worker_registry=MagicMock(), opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    with pytest.raises(ValueError, match="Invalid resume instruction"):
        await pe.resume_plan(plan_id, "jump")


@pytest.mark.asyncio
async def test_resume_not_paused_raises():
    plan_id = seed_plan("Not paused")
    seed_step(plan_id, 0, "Step", "opencode")

    pe = PlanExecutor(worker_registry=MagicMock(), opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    with pytest.raises(ValueError, match="not paused"):
        await pe.resume_plan(plan_id, "retry")



@pytest.mark.asyncio
async def test_double_start_guard():
    plan_id = seed_plan("Double start")
    seed_step(plan_id, 0, "Step 1", "opencode")
    seed_step(plan_id, 1, "Step 2", "opencode")

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    assert await pe.start_plan(plan_id)
    assert not await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    assert len(pe._running_plans) == 0


@pytest.mark.asyncio
async def test_reconcile_completed_opencode_task_succeeds_step():
    plan_id = seed_plan("Recovery: completed")
    step_id = seed_step(plan_id, 0, "Work done", "opencode", on_failure="stop")
    seed_step(plan_id, 1, "Next step", "opencode")

    task_id = make_task_id()
    seed_opencode_task(task_id, status="completed", result_summary="remote success")

    db.update_plan_status(plan_id, "running", current_step_index=0)
    db.update_plan_step(step_id, status="running",
                        worker_task_id=task_id, started_at=db.utcnow())

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    oc = MagicMock()
    oc.wait_for_completion = MagicMock()
    oc.fetch_task_result_text = MagicMock()

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=oc,
                      conn_manager=MagicMock(), step_timeout=5.0)
    await pe.reconcile_on_startup()
    await _wait_plan_done(pe, plan_id)

    step = db.get_plan_step(step_id)
    assert step["status"] == "succeeded"
    assert step["result"] == "remote success"
    oc.wait_for_completion.assert_not_called()
    assert db.get_plan(plan_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_reconcile_degraded_opencode_task_fails_step():
    plan_id = seed_plan("Recovery: degraded")
    step_id = seed_step(plan_id, 0, "Ambiguous work", "opencode",
                        on_failure="continue")
    seed_step(plan_id, 1, "Next step", "opencode")

    task_id = make_task_id()
    seed_opencode_task(task_id, status="running")
    db.update_opencode_task_status(task_id, "degraded")

    db.update_plan_status(plan_id, "running", current_step_index=0)
    db.update_plan_step(step_id, status="running",
                        worker_task_id=task_id, started_at=db.utcnow())

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    await pe.reconcile_on_startup()
    await _wait_plan_done(pe, plan_id)

    step = db.get_plan_step(step_id)
    assert step["status"] == "failed"
    assert "unverifiable" in step["error"].lower()
    assert db.get_plan(plan_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_reconcile_no_worker_task_id_fails_step():
    plan_id = seed_plan("Recovery: no task id")
    step_id = seed_step(plan_id, 0, "Race work", "opencode", on_failure="stop")

    db.update_plan_status(plan_id, "running", current_step_index=0)
    db.update_plan_step(step_id, status="running", started_at=db.utcnow())

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    await pe.reconcile_on_startup()

    step = db.get_plan_step(step_id)
    assert step["status"] == "failed"
    assert "no worker task recorded" in step["error"].lower()


@pytest.mark.asyncio
async def test_reconcile_failed_opencode_task_fails_step():
    plan_id = seed_plan("Recovery: failed")
    step_id = seed_step(plan_id, 0, "Failed work", "opencode", on_failure="stop")

    task_id = make_task_id()
    seed_opencode_task(task_id, status="failed", result_summary="remote failure")

    db.update_plan_status(plan_id, "running", current_step_index=0)
    db.update_plan_step(step_id, status="running",
                        worker_task_id=task_id, started_at=db.utcnow())

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    await pe.reconcile_on_startup()
    await _wait_plan_done(pe, plan_id)

    step = db.get_plan_step(step_id)
    assert step["status"] == "failed"
    assert "remote failure" in step["error"]


@pytest.mark.asyncio
async def test_reconcile_missing_opencode_task_record_fails_step():
    plan_id = seed_plan("Recovery: missing")
    step_id = seed_step(plan_id, 0, "Ghost work", "opencode", on_failure="stop")

    db.update_plan_status(plan_id, "running", current_step_index=0)
    db.update_plan_step(step_id, status="running",
                        worker_task_id="oc_nonexistent", started_at=db.utcnow())

    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    await pe.reconcile_on_startup()

    step = db.get_plan_step(step_id)
    assert step["status"] == "failed"
    assert "not found" in step["error"].lower()


@pytest.mark.asyncio
async def test_reconcile_index_past_last_step_marks_completed():
    """A crash between the last step's index-advance write and the
    plan's own 'completed' write leaves current_step_index == len(steps)
    with status still 'running' -- reconcile must not IndexError on
    steps[current_step_index]."""
    plan_id = seed_plan("Recovery: finished before crash")
    seed_step(plan_id, 0, "Only step", "opencode", on_failure="stop")
    db.update_plan_status(plan_id, "running", current_step_index=1)

    reg = MagicMock()
    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    await pe.reconcile_on_startup()

    plan = db.get_plan(plan_id)
    assert plan["status"] == "completed"
    assert plan_id not in pe._running_plans


@pytest.mark.asyncio
async def test_start_plan_refuses_after_stop():
    reg = MagicMock()
    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    await pe.stop()

    plan_id = seed_plan("Post-shutdown plan")
    seed_step(plan_id, 0, "Step", "opencode")
    assert await pe.start_plan(plan_id) is False


@pytest.mark.asyncio
async def test_resume_plan_refuses_retry_after_stop():
    reg = MagicMock()
    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Paused plan")
    seed_step(plan_id, 0, "Step", "opencode", on_failure="escalate")
    db.update_plan_status(plan_id, "paused")
    await pe.stop()

    with pytest.raises(ValueError, match="shut down"):
        await pe.resume_plan(plan_id, "retry")


@pytest.mark.asyncio
async def test_resume_plan_abort_allowed_after_stop():
    reg = MagicMock()
    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Paused plan")
    seed_step(plan_id, 0, "Step", "opencode", on_failure="escalate")
    db.update_plan_status(plan_id, "paused")
    await pe.stop()

    await pe.resume_plan(plan_id, "abort")
    assert db.get_plan(plan_id)["status"] == "failed"


@pytest.mark.asyncio
async def test_stop_cancels_running_tasks():
    started_event = asyncio.Event()

    async def slow_invoke(desc, **kw):
        started_event.set()
        await asyncio.sleep(10)
        return WorkerResult(status=WorkerResultStatus.COMPLETED, output="done")

    w = MagicMock()
    w.name = "opencode"
    w.capabilities = ["test"]
    w.invoke = slow_invoke
    w.status = _async_return(WorkerStatus.AVAILABLE)
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Slow plan")
    seed_step(plan_id, 0, "Slow step", "opencode")

    assert await pe.start_plan(plan_id)
    await started_event.wait()

    assert plan_id in pe._running_plans
    await pe.stop()
    assert plan_id not in pe._running_plans


# ── Plan-completion memory write (Step 2: F1 memory wiring) ────────────


@pytest.mark.asyncio
async def test_plan_completed_writes_completion_memory():
    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    oc = MagicMock()
    oc.wait_for_completion = _async_return(True)
    oc.fetch_task_result_text = _async_return(None)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=oc,
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Memory write test")
    seed_step(plan_id, 0, "Step 1", "opencode")

    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    assert db.get_plan(plan_id)["status"] == "completed"
    memories = memory.get_memories_by_source("plan_completion", plan_id)
    assert len(memories) == 1
    assert "completed" in memories[0]["content"].lower()
    assert "Memory write test" in memories[0]["content"]


@pytest.mark.asyncio
async def test_plan_failed_writes_completion_memory():
    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.FAILED, error="something broke")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Failed plan memory")
    seed_step(plan_id, 0, "Doomed step", "opencode", on_failure="stop")

    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    assert db.get_plan(plan_id)["status"] == "failed"
    memories = memory.get_memories_by_source("plan_completion", plan_id)
    assert len(memories) == 1
    content = memories[0]["content"]
    assert "failed" in content.lower()
    assert "Doomed step" in content
    assert "something broke" in content


@pytest.mark.asyncio
async def test_plan_paused_writes_no_completion_memory():
    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.FAILED, error="needs human")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    cm = MagicMock()

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=MagicMock(),
                      conn_manager=cm, step_timeout=5.0)
    plan_id = seed_plan("Paused no memory")
    seed_step(plan_id, 0, "Needs human", "opencode", on_failure="escalate")

    assert await pe.start_plan(plan_id)
    await _wait_plan_done(pe, plan_id)

    assert db.get_plan(plan_id)["status"] == "paused"
    memories = memory.get_memories_by_source("plan_completion", plan_id)
    assert len(memories) == 0


@pytest.mark.asyncio
async def test_plan_completion_memory_failure_does_not_block_plan():
    w = _make_worker("opencode", _async_return(WorkerResult(
        status=WorkerResultStatus.COMPLETED, output="done")))
    reg = MagicMock()
    reg.get_by_name = MagicMock(return_value=w)
    oc = MagicMock()
    oc.wait_for_completion = _async_return(True)
    oc.fetch_task_result_text = _async_return(None)

    pe = PlanExecutor(worker_registry=reg, opencode_supervisor=oc,
                      conn_manager=MagicMock(), step_timeout=5.0)
    plan_id = seed_plan("Memory should fail silently")

    original_store = memory.store_memory
    called = [False]

    def failing_store(*args, **kwargs):
        called[0] = True
        raise RuntimeError("simulated DB failure")

    memory.store_memory = failing_store
    try:
        seed_step(plan_id, 0, "Step 1", "opencode")
        assert await pe.start_plan(plan_id)
        await _wait_plan_done(pe, plan_id)
        assert called[0]
        assert db.get_plan(plan_id)["status"] == "completed"
    finally:
        memory.store_memory = original_store
