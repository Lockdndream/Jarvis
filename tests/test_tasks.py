"""Tests for Jarvis task management (Milestone 2)."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app.task_manager import TaskManager
from tests.conftest import _wait_until


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


def _count_task_events(tid: str) -> int:
    count = 0
    for ev in db.get_recent_events(200):
        if not ev["content"]:
            continue
        try:
            c = json.loads(ev["content"])
            if isinstance(c, dict) and c.get("task_id") == tid:
                count += 1
        except (json.JSONDecodeError, TypeError):
            pass
    return count


@pytest.mark.asyncio
async def test_task_crud():
    """Test database task CRUD operations."""
    db.create_task_record("crud-1", "Test Task", "echo hello")
    task = db.get_task("crud-1")
    assert task is not None
    assert task["task_id"] == "crud-1"
    assert task["status"] == "running"

    db.update_task_status("crud-1", "completed", 0)
    task = db.get_task("crud-1")
    assert task["status"] == "completed"
    assert task["exit_code"] == 0
    assert task["completed_at"] is not None

    tasks = db.get_recent_tasks(10)
    assert len(tasks) >= 1


@pytest.mark.asyncio
async def test_demo_task_starts():
    """Demo task starts and receives a task_id."""
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_demo(steps=2)

    assert "task_id" in result
    assert result["name"] == "Demo Task"
    tid = result["task_id"]

    task = db.get_task(tid)
    assert task is not None
    assert task["status"] == "running"

    await _wait_until(lambda: db.get_task(tid)["status"] == "completed")

    task = db.get_task(tid)
    assert task["status"] == "completed"
    assert task["exit_code"] == 0


@pytest.mark.asyncio
async def test_stdout_captured():
    """Stdout is captured and saved as events."""
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_demo(steps=2)
    tid = result["task_id"]

    await _wait_until(lambda: _count_task_events(tid) >= 1)

    count = _count_task_events(tid)
    assert count >= 1, "Should have at least one task event"

    await _wait_until(lambda: db.get_task(tid)["status"] == "completed")


@pytest.mark.asyncio
async def test_demo_task_completion_recorded():
    """Successful completion is recorded correctly."""
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_demo(steps=2)
    tid = result["task_id"]

    await _wait_until(lambda: db.get_task(tid)["status"] == "completed")

    task = db.get_task(tid)
    assert task["status"] == "completed"
    assert task["exit_code"] == 0

    events = db.get_recent_events(100)
    completed_events = [
        e
        for e in events
        if e["type"] == "task_completed"
        and e["content"]
        and json.loads(e["content"]).get("task_id") == tid
    ]
    assert len(completed_events) >= 1


@pytest.mark.asyncio
async def test_cancellation():
    """Cancellation works and updates state correctly."""
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_demo()
    tid = result["task_id"]

    await _wait_until(lambda: _count_task_events(tid) >= 1)

    msg = await tm.cancel(tid)
    assert "cancelled" in msg.lower()

    await _wait_until(lambda: db.get_task(tid)["status"] == "cancelled")

    task = db.get_task(tid)
    assert task["status"] == "cancelled"

    events = db.get_recent_events(100)
    cancelled_events = [
        e
        for e in events
        if e["type"] == "task_cancelled"
        and e["content"]
        and json.loads(e["content"]).get("task_id") == tid
    ]
    assert len(cancelled_events) >= 1

    # Should not have task_completed or task_failed for this task
    terminal = [
        e
        for e in events
        if e["type"] in ("task_completed", "task_failed")
        and e["content"]
        and json.loads(e["content"]).get("task_id") == tid
    ]
    assert len(terminal) == 0, "Cancelled task must not emit completion/failure"


@pytest.mark.asyncio
async def test_invalid_cancellation():
    """Invalid cancellation is handled cleanly."""
    cm = ConnectionManager()
    tm = TaskManager(cm)

    msg = await tm.cancel("nonexistent-id")
    assert "not found" in msg.lower()

    msg = await tm.cancel("")
    assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_mark_interrupted():
    """mark_running_tasks_interrupted updates stuck tasks."""
    db.create_task_record("interrupted-1", "Stuck Task", "test")
    affected = db.mark_running_tasks_interrupted()
    assert "interrupted-1" in affected

    task = db.get_task("interrupted-1")
    assert task["status"] == "failed"
    assert task["exit_code"] == -1
    assert task["completed_at"] is not None

    # Already-completed tasks should be unaffected
    db.create_task_record("interrupted-2", "Done Task", "test")
    db.update_task_status("interrupted-2", "completed", 0)
    affected2 = db.mark_running_tasks_interrupted()
    assert "interrupted-2" not in affected2
    task2 = db.get_task("interrupted-2")
    assert task2["status"] == "completed"
    assert task2["exit_code"] == 0


@pytest.mark.asyncio
async def test_cancel_running_from_task_manager():
    """Task manager lists running tasks correctly."""
    cm = ConnectionManager()
    tm = TaskManager(cm)

    running = tm.get_running_tasks_info()
    assert len(running) == 0

    result = await tm.start_demo()
    tid = result["task_id"]

    running = tm.get_running_tasks_info()
    assert len(running) == 1
    assert running[0]["task_id"] == tid
    assert running[0]["status"] == "running"
    assert running[0]["elapsed"] >= 0

    await tm.cancel(tid)
    await _wait_until(lambda: len(tm.get_running_tasks_info()) == 0)

    running = tm.get_running_tasks_info()
    assert len(running) == 0


@pytest.mark.asyncio
async def test_demo_task_completion_notification_includes_exit_code():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_demo(steps=2)
    tid = result["task_id"]

    await _wait_until(lambda: db.get_task(tid)["status"] == "completed")

    def _has_completion_notification():
        notifs = [n for n in db.get_recent_notifications(50)
                  if n["task_id"] == tid and n["source_type"] == "local_task"]
        return notifs if len(notifs) == 1 else None

    notifs = await _wait_until(_has_completion_notification)
    assert len(notifs) == 1
    assert notifs[0]["title"] == "Jarvis task completed"
    assert "exit code 0" in notifs[0]["body"]

