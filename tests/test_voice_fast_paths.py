"""Milestone 7 Phase 10: deterministic voice/text command resolution.

Voice transcripts enter the exact same supervisor path as typed text
(principle 1), so these tests exercise _resolve_deterministic_command with
plain text — a real voice transcript would be indistinguishable by the time
it reaches this function. Never guesses under ambiguity: with more than one
matching pending item, no tool is called and nothing is modified.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.supervisor.supervisor import _resolve_deterministic_command


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


class FakeTools:
    """Records calls; returns a canned response. Standing in for
    ToolRegistry so these tests stay focused on the deterministic
    resolution/ambiguity logic itself, not OpenCode/TaskManager plumbing
    (already covered by test_opencode_lifecycle.py / test_tasks.py)."""

    def __init__(self, response="OK"):
        self.calls = []
        self.response = response

    async def call(self, name, args):
        self.calls.append((name, args))
        return self.response


def _question(qid, task_id="oc_task1"):
    db.create_task_record(task_id, "Test Task", "do the thing")
    db.create_question_record(qid, task_id, "Which approach?", "", '["A", "B"]')


def _permission(pid, task_id="oc_task1"):
    db.create_task_record(task_id, "Test Task", "do the thing")
    db.create_question_record(pid, task_id, "Permission: write file.txt", "action=write&path=file.txt", "[]")


def _running_task(task_id):
    db.create_task_record(task_id, "Test Task", "do the thing")
    db.update_task_status(task_id, "running")


# ── "Answer B" ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_answer_single_pending_question_resolves():
    _question("q1")
    tools = FakeTools()
    result = await _resolve_deterministic_command("Answer B.", tools)
    assert tools.calls == [("answer_question", {"question_id": "q1", "answer": "B"})]
    assert "B" in result
    assert "couldn't" not in result.lower()


@pytest.mark.asyncio
async def test_answer_two_pending_questions_asks_and_modifies_none():
    _question("q1", task_id="oc_task1")
    _question("q2", task_id="oc_task2")
    tools = FakeTools()
    result = await _resolve_deterministic_command("Answer B.", tools)
    assert tools.calls == []
    assert "2" in result
    assert "which" in result.lower()


@pytest.mark.asyncio
async def test_answer_zero_pending_defers_to_llm():
    tools = FakeTools()
    result = await _resolve_deterministic_command("Answer B.", tools)
    assert result is None
    assert tools.calls == []


@pytest.mark.asyncio
async def test_answer_only_confirms_after_native_success():
    _question("q1")
    tools = FakeTools(response="Error: adapter unreachable")
    result = await _resolve_deterministic_command("Answer B.", tools)
    assert "couldn't" in result.lower()


# ── Natural phrasing without the literal word "answer" ──────────────
# Real-phone finding (Milestone 7 Phase 18, 2026-07-09): a real spoken
# transcript said "approach a", not "Answer A" — _ANSWER_RE never matched
# it, so it silently fell through to the LLM with no idea a question was
# even pending. Still fully deterministic: matches only when exactly one
# of the pending question's own options is mentioned as a distinct word.

@pytest.mark.asyncio
async def test_natural_phrasing_matches_single_mentioned_option():
    _question("q1")  # options ["A", "B"]
    tools = FakeTools()
    result = await _resolve_deterministic_command("approach a", tools)
    assert tools.calls == [("answer_question", {"question_id": "q1", "answer": "A"})]
    assert "A" in result


@pytest.mark.asyncio
async def test_natural_phrasing_variant_go_with_b():
    _question("q1")
    tools = FakeTools()
    await _resolve_deterministic_command("let's go with option B please", tools)
    assert tools.calls == [("answer_question", {"question_id": "q1", "answer": "B"})]


@pytest.mark.asyncio
async def test_natural_phrasing_zero_matching_options_defers():
    _question("q1")
    tools = FakeTools()
    result = await _resolve_deterministic_command("I'm not sure yet", tools)
    assert result is None
    assert tools.calls == []


@pytest.mark.asyncio
async def test_natural_phrasing_multiple_matching_options_defers():
    _question("q1")
    tools = FakeTools()
    result = await _resolve_deterministic_command("should I go with a or b", tools)
    assert result is None
    assert tools.calls == []


@pytest.mark.asyncio
async def test_natural_phrasing_only_applies_with_exactly_one_pending_question():
    _question("q1", task_id="oc_task1")
    _question("q2", task_id="oc_task2")
    tools = FakeTools()
    result = await _resolve_deterministic_command("approach a", tools)
    assert result is None  # ambiguous which task's question is meant — not a literal "answer" phrasing either
    assert tools.calls == []


@pytest.mark.asyncio
async def test_literal_answer_prefix_still_takes_priority():
    _question("q1")
    tools = FakeTools()
    await _resolve_deterministic_command("Answer B.", tools)
    assert tools.calls == [("answer_question", {"question_id": "q1", "answer": "B"})]


@pytest.mark.asyncio
async def test_answer_ignores_pending_permissions():
    _permission("p1")  # a permission, not a plain question
    tools = FakeTools()
    result = await _resolve_deterministic_command("Answer B.", tools)
    assert result is None  # zero *questions* pending — defers, doesn't touch the permission
    assert tools.calls == []


# ── "Approve it" / "Reject it" ───────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_single_pending_permission_resolves():
    _permission("p1")
    tools = FakeTools()
    result = await _resolve_deterministic_command("Approve it.", tools)
    assert tools.calls == [("resolve_permission", {"permission_id": "p1", "decision": "approve"})]
    assert "approved" in result.lower()


@pytest.mark.asyncio
async def test_reject_single_pending_permission_resolves():
    _permission("p1")
    tools = FakeTools()
    result = await _resolve_deterministic_command("Reject it.", tools)
    assert tools.calls == [("resolve_permission", {"permission_id": "p1", "decision": "reject"})]
    assert "rejected" in result.lower()


@pytest.mark.asyncio
async def test_approve_multiple_permissions_asks_and_modifies_none():
    _permission("p1", task_id="oc_task1")
    _permission("p2", task_id="oc_task2")
    tools = FakeTools()
    result = await _resolve_deterministic_command("Approve it.", tools)
    assert tools.calls == []
    assert "which" in result.lower()


@pytest.mark.asyncio
async def test_approve_zero_pending_defers_to_llm():
    tools = FakeTools()
    result = await _resolve_deterministic_command("Approve it.", tools)
    assert result is None
    assert tools.calls == []


# ── "Stop it" ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_single_cancellable_task_cancels():
    _running_task("oc_task1")
    tools = FakeTools()
    result = await _resolve_deterministic_command("Stop it.", tools)
    assert tools.calls == [("cancel_task", {"task_id": "oc_task1"})]
    assert "stopped" in result.lower()


@pytest.mark.asyncio
async def test_stop_multiple_tasks_asks_and_modifies_none():
    _running_task("oc_task1")
    _running_task("oc_task2")
    tools = FakeTools()
    result = await _resolve_deterministic_command("Stop it.", tools)
    assert tools.calls == []
    assert "which" in result.lower()


@pytest.mark.asyncio
async def test_stop_zero_cancellable_defers_to_llm():
    tools = FakeTools()
    result = await _resolve_deterministic_command("Stop it.", tools)
    assert result is None
    assert tools.calls == []


@pytest.mark.asyncio
async def test_stop_only_confirms_after_native_success():
    _running_task("oc_task1")
    tools = FakeTools(response="Error: task not found")
    result = await _resolve_deterministic_command("Stop it.", tools)
    assert "couldn't" in result.lower()


# ── Preserve prior behavior for generic phrasing with nothing pending ──
# (matches tests/test_supervisor.py::test_fast_path_non_slash)

@pytest.mark.asyncio
async def test_generic_phrasing_with_nothing_pending_defers():
    tools = FakeTools()
    assert await _resolve_deterministic_command("answer the question", tools) is None
    assert await _resolve_deterministic_command("stop the task", tools) is None
    assert tools.calls == []
