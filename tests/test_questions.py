"""Tests for Jarvis question protocol and interactive worker (Milestone 3)."""
import asyncio
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.connection_manager import ConnectionManager
from app.task_manager import TaskManager
from app.protocol import parse_line


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


def _find_event(events, ev_type, key, value):
    for e in events:
        if e["type"] != ev_type:
            continue
        if not e["content"]:
            continue
        try:
            c = json.loads(e["content"])
            if c.get(key) == value:
                return e
        except (json.JSONDecodeError, TypeError):
            pass
    return None


# ── Protocol parser ───────────────────────────────────────────────


def test_protocol_parses_valid_question():
    line = 'JARVIS_QUESTION:{"question_id":"abc-123","question":"Test?","options":["X","Y"],"context":"Some context"}'
    result = parse_line(line)
    assert result is not None
    assert result["type"] == "question"
    assert result["question_id"] == "abc-123"
    assert result["question"] == "Test?"
    assert result["options"] == ["X", "Y"]
    assert result["context"] == "Some context"


def test_protocol_returns_none_for_normal_line():
    assert parse_line("Starting mock agent...") is None
    assert parse_line("") is None
    assert parse_line("JARVIS_QUESTION") is None  # missing colon + json


def test_protocol_handles_malformed_json():
    line = "JARVIS_QUESTION:{invalid}"
    result = parse_line(line)
    assert result is None


def test_protocol_handles_missing_fields():
    line = 'JARVIS_QUESTION:{"foo":"bar"}'
    result = parse_line(line)
    assert result is None


# ── Database ──────────────────────────────────────────────────────


def test_question_crud():
    db.create_question_record("q-1", "t-1", "Test question?", "context", '["A","B"]')
    q = db.get_question_record("q-1")
    assert q is not None
    assert q["question_id"] == "q-1"
    assert q["status"] == "pending"

    pending = db.get_pending_questions()
    assert len(pending) == 1

    db.answer_question_record("q-1", "B")
    q = db.get_question_record("q-1")
    assert q["status"] == "answered"
    assert q["answer"] == "B"

    db.cancel_question_record("q-1")
    q = db.get_question_record("q-1")
    assert q["status"] == "answered"  # cancel_question only cancels pending


def test_question_cancel_only_pending():
    db.create_question_record("q-2", "t-2", "Q?", "", "[]")
    db.answer_question_record("q-2", "A")
    db.cancel_question_record("q-2")
    q = db.get_question_record("q-2")
    assert q["status"] == "answered"  # should not change from answered


# ── Mock agent lifecycle ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_agent_starts():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_mock_agent(test_mode=True)

    assert "task_id" in result
    assert result["name"] == "Mock Agent"

    task = db.get_task(result["task_id"])
    assert task is not None
    assert task["status"] == "running"

    # Let it run briefly and check it doesn't crash
    await asyncio.sleep(1)
    assert tm._processes.get(result["task_id"]) is not None


@pytest.mark.asyncio
async def test_stdout_streams_before_question():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_mock_agent(test_mode=True)
    tid = result["task_id"]

    await asyncio.sleep(1)

    events = db.get_recent_events(50)
    stdout_events = [e for e in events if e["type"] == "task_stdout"]
    assert len(stdout_events) >= 1
    content = json.loads(stdout_events[0]["content"])
    assert content.get("task_id") == tid


@pytest.mark.asyncio
async def test_question_detected():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_mock_agent(test_mode=True)
    tid = result["task_id"]

    # Wait for question (test mode: 3 steps * 0.1s ≈ 0.3s, plus margin)
    await asyncio.sleep(2)

    task = db.get_task(tid)
    assert task["status"] == "waiting_for_user", f"Expected waiting_for_user, got {task['status']}"

    events = db.get_recent_events(50)
    q_asked = _find_event(events, "question_asked", "task_id", tid)
    assert q_asked is not None, "question_asked event should exist"
    content = json.loads(q_asked["content"])
    assert "question_id" in content
    assert content["question"] == "Which approach should I use?"

    # Question should be persisted
    pending = db.get_pending_questions()
    assert len(pending) >= 1

    # Raw protocol line should NOT appear in timeline
    raw = [e for e in events if e["type"] == "task_stdout" and "JARVIS_QUESTION" in (e.get("content") or "")]
    assert len(raw) == 0, "Raw protocol line must not appear as task_stdout"


@pytest.mark.asyncio
async def test_answer_delivered():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_mock_agent(test_mode=True)
    tid = result["task_id"]

    await asyncio.sleep(2)

    task = db.get_task(tid)
    assert task["status"] == "waiting_for_user"

    # Find the question
    questions = db.get_pending_questions()
    q = questions[0]
    qid = q["question_id"]

    # Answer
    msg = await tm.answer_question(qid, "B")
    assert "delivered" in msg.lower()

    await asyncio.sleep(1)

    # Task should be running again (or may have completed very fast in test mode)
    task = db.get_task(tid)
    assert task["status"] in ("running", "completed"), f"Expected running or completed, got {task['status']}"

    # Question should be answered
    q_record = db.get_question_record(qid)
    assert q_record["status"] == "answered"
    assert q_record["answer"] == "B"

    # Wait for completion
    await asyncio.sleep(4)
    task = db.get_task(tid)
    assert task["status"] == "completed", f"Expected completed, got {task['status']}"
    assert task["exit_code"] == 0


@pytest.mark.asyncio
async def test_duplicate_answer_rejected():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    await tm.start_mock_agent(test_mode=True)

    await asyncio.sleep(2)

    questions = db.get_pending_questions()
    qid = questions[0]["question_id"]

    msg1 = await tm.answer_question(qid, "B")
    assert "delivered" in msg1.lower()

    msg2 = await tm.answer_question(qid, "A")
    assert "already" in msg2.lower() or "answered" in msg2.lower()


@pytest.mark.asyncio
async def test_invalid_question_id_rejected():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    msg = await tm.answer_question("nonexistent", "B")
    assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_cancellation_while_waiting():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    result = await tm.start_mock_agent(test_mode=True)
    tid = result["task_id"]

    await asyncio.sleep(2)

    task = db.get_task(tid)
    assert task["status"] == "waiting_for_user"

    questions = db.get_pending_questions()
    qid = questions[0]["question_id"]

    msg = await tm.cancel(tid)
    assert "cancelled" in msg.lower()

    await asyncio.sleep(1)

    task = db.get_task(tid)
    assert task["status"] == "cancelled"

    # Question should be cancelled
    q = db.get_question_record(qid)
    assert q["status"] == "cancelled"

    # Should have question_cancelled event
    events = db.get_recent_events(50)
    qc = _find_event(events, "question_cancelled", "question_id", qid)
    assert qc is not None


@pytest.mark.asyncio
async def test_pending_questions_info():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    await tm.start_mock_agent(test_mode=True)

    await asyncio.sleep(2)

    info = tm.get_pending_questions_info()
    assert len(info) >= 1
    assert info[0]["question"] == "Which approach should I use?"
    assert info[0]["options"] == ["A", "B"]


@pytest.mark.asyncio
async def test_attention_summary():
    cm = ConnectionManager()
    tm = TaskManager(cm)
    await tm.start_mock_agent(test_mode=True)

    await asyncio.sleep(2)

    summary = tm.get_attention_summary()
    assert "waiting" in summary.lower()
    assert "pending" in summary.lower()


@pytest.mark.asyncio
async def test_two_workers_isolation():
    """Two simultaneous workers receive correct respective answers."""
    cm = ConnectionManager()
    tm = TaskManager(cm)

    r1 = await tm.start_mock_agent(test_mode=True)
    r2 = await tm.start_mock_agent(test_mode=True)
    tid1, tid2 = r1["task_id"], r2["task_id"]

    # Wait for both to emit questions (test mode: ~1s each)
    await asyncio.sleep(3)

    t1 = db.get_task(tid1)
    t2 = db.get_task(tid2)

    # Both should be waiting (or one may have progressed faster)
    assert t1["status"] in ("waiting_for_user", "running"), f"t1 status: {t1['status']}"
    assert t2["status"] in ("waiting_for_user", "running"), f"t2 status: {t2['status']}"

    # Find pending questions for each
    all_pending = db.get_pending_questions()
    q_for_t1 = [q for q in all_pending if q["task_id"] == tid1]
    q_for_t2 = [q for q in all_pending if q["task_id"] == tid2]

    # Answer whichever questions are pending
    if q_for_t1:
        msg1 = await tm.answer_question(q_for_t1[0]["question_id"], "A")
        assert "delivered" in msg1.lower()
    if q_for_t2:
        msg2 = await tm.answer_question(q_for_t2[0]["question_id"], "B")
        assert "delivered" in msg2.lower()

    await asyncio.sleep(1)

    # Verify answers were recorded correctly
    for q in q_for_t1:
        qr = db.get_question_record(q["question_id"])
        if qr["status"] == "answered":
            assert qr["answer"] == "A", f"Worker 1 expected A, got {qr['answer']}"

    for q in q_for_t2:
        qr = db.get_question_record(q["question_id"])
        if qr["status"] == "answered":
            assert qr["answer"] == "B", f"Worker 2 expected B, got {qr['answer']}"

    # Let both complete
    await asyncio.sleep(4)

    task1 = db.get_task(tid1)
    task2 = db.get_task(tid2)
    # Both should be completed or cancelled
    for t in [task1, task2]:
        assert t["status"] in ("completed", "cancelled"), f"Task {t['task_id'][:8]} status: {t['status']}"
