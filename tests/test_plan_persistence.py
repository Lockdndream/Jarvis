"""Tests for Plan/PlanStep data model persistence (CRUD only)."""
import json
import os
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db


@pytest.fixture(autouse=True)
def test_db():
    old_path = db.DB_PATH
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = path
    db.init_db()
    yield
    db.DB_PATH = old_path
    if os.path.exists(path):
        os.unlink(path)


def make_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:12]}"


def make_step_id() -> str:
    return f"step_{uuid.uuid4().hex[:12]}"


# ── Basic CRUD ──────────────────────────────────────────────────────


def test_create_and_get_plan():
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, "Test plan", context_json='{"key":"val"}')

    plan = db.get_plan(plan_id)
    assert plan is not None
    assert plan["plan_id"] == plan_id
    assert plan["title"] == "Test plan"
    assert plan["status"] == "pending"
    assert plan["current_step_index"] == 0
    assert plan["context_json"] == '{"key":"val"}'
    assert plan["created_at"] == plan["updated_at"]


def test_get_plan_not_found():
    assert db.get_plan("plan_nonexistent") is None


def test_create_and_get_plan_steps_ordered():
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, "Multi-step plan")

    s3 = make_step_id()
    s1 = make_step_id()
    s2 = make_step_id()

    db.create_plan_step_record(s1, plan_id, 0, "First step", "opencode")
    db.create_plan_step_record(s2, plan_id, 1, "Second step", "strategist", on_failure="escalate")
    db.create_plan_step_record(s3, plan_id, 2, "Third step", "opencode", verification="run pytest")

    steps = db.get_plan_steps(plan_id)
    assert len(steps) == 3
    assert [s["step_id"] for s in steps] == [s1, s2, s3]
    assert [s["step_index"] for s in steps] == [0, 1, 2]
    assert steps[1]["worker_name"] == "strategist"
    assert steps[1]["on_failure"] == "escalate"
    assert steps[1]["on_failure"] == "escalate"
    assert steps[2]["verification"] == "run pytest"


def test_get_plan_step():
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, "Single step plan")
    step_id = make_step_id()
    db.create_plan_step_record(step_id, plan_id, 0, "Do something", "opencode")

    step = db.get_plan_step(step_id)
    assert step is not None
    assert step["step_id"] == step_id
    assert step["plan_id"] == plan_id
    assert step["status"] == "pending"


def test_get_plan_step_not_found():
    assert db.get_plan_step("step_nonexistent") is None


# ── Updates ─────────────────────────────────────────────────────────


def test_update_plan_status():
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, "Status test")
    original = db.get_plan(plan_id)

    db.update_plan_status(plan_id, "running", current_step_index=1)
    updated = db.get_plan(plan_id)

    assert updated["status"] == "running"
    assert updated["current_step_index"] == 1
    assert updated["updated_at"] != original["updated_at"]
    assert updated["created_at"] == original["created_at"]


def test_update_plan_status_without_step_index():
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, "Status test")
    db.update_plan_status(plan_id, "completed")

    plan = db.get_plan(plan_id)
    assert plan["status"] == "completed"
    assert plan["current_step_index"] == 0  # unchanged


def test_update_plan_step_partial():
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, "Partial update test")
    step_id = make_step_id()
    db.create_plan_step_record(step_id, plan_id, 0, "Test step", "opencode")

    db.update_plan_step(step_id, status="running", worker_task_id="oc_abc123")
    step = db.get_plan_step(step_id)
    assert step["status"] == "running"
    assert step["worker_task_id"] == "oc_abc123"
    assert step["result"] is None
    assert step["error"] is None
    assert step["started_at"] is None
    assert step["completed_at"] is None

    db.update_plan_step(step_id, status="succeeded", result="All good")
    step = db.get_plan_step(step_id)
    assert step["status"] == "succeeded"
    assert step["worker_task_id"] == "oc_abc123"  # unchanged
    assert step["result"] == "All good"


def test_update_plan_step_empty_noop():
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, "Noop test")
    step_id = make_step_id()
    db.create_plan_step_record(step_id, plan_id, 0, "Noop step", "opencode")

    db.update_plan_step(step_id)  # no kwargs — no-op
    step = db.get_plan_step(step_id)
    assert step["status"] == "pending"


# ── Recent plans ────────────────────────────────────────────────────


def test_get_recent_plans_ordering_and_limit():
    ids = []
    for i in range(5):
        pid = make_plan_id()
        db.create_plan_record(pid, f"Plan {i}")
        ids.append(pid)

    recent = db.get_recent_plans(limit=3)
    assert len(recent) == 3
    assert recent[0]["plan_id"] == ids[-1]
    assert recent[1]["plan_id"] == ids[-2]
    assert recent[2]["plan_id"] == ids[-3]


# ── JSON round-trip ─────────────────────────────────────────────────


def test_json_round_trip_context_and_depends_on():
    plan_id = make_plan_id()
    context = json.dumps({"project": "Jarvis", "constraints": ["no cloud", "local-first"]})
    db.create_plan_record(plan_id, "JSON test", context_json=context)

    step_id = make_step_id()
    depends = json.dumps(["step_aaa111", "step_bbb222"])
    db.create_plan_step_record(
        step_id, plan_id, 0, "JSON step", "opencode", depends_on_json=depends,
    )

    plan = db.get_plan(plan_id)
    assert plan["context_json"] == context

    step = db.get_plan_step(step_id)
    assert step["depends_on_json"] == depends

    # They store as opaque strings — no parsing required
    assert isinstance(plan["context_json"], str)
    assert isinstance(step["depends_on_json"], str)


# ── Plan step defaults ──────────────────────────────────────────────


def test_plan_step_defaults():
    plan_id = make_plan_id()
    db.create_plan_record(plan_id, "Defaults test")
    step_id = make_step_id()
    db.create_plan_step_record(step_id, plan_id, 0, "Default step", "opencode")

    step = db.get_plan_step(step_id)
    assert step["status"] == "pending"
    assert step["on_failure"] == "stop"
    assert step["verification"] is None
    assert step["depends_on_json"] is None
    assert step["worker_task_id"] is None
    assert step["result"] is None
    assert step["error"] is None
    assert step["started_at"] is None
    assert step["completed_at"] is None
