"""ADR-023 Phase 6: the console-local Operation history store."""
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import operation_history as oh


@pytest.fixture
def db_path():
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    oh.init_db(db_path=path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_create_operation_starts_queued(db_path):
    op_id = oh.create_operation("jarvis", "start", db_path=db_path)
    rows = oh.get_recent_operations(db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["operation_id"] == op_id
    assert rows[0]["status"] == "QUEUED"
    assert rows[0]["operator"] == "local"
    assert rows[0]["started_at"] is None
    assert rows[0]["finished_at"] is None


def test_mark_running_then_finished_records_duration(db_path):
    op_id = oh.create_operation("opencode", "restart", db_path=db_path)
    oh.mark_running(op_id, db_path=db_path)
    time.sleep(0.05)
    oh.mark_finished(op_id, "SUCCEEDED", db_path=db_path)

    row = oh.get_recent_operations(db_path=db_path)[0]
    assert row["status"] == "SUCCEEDED"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    assert row["duration_ms"] >= 40
    assert row["error"] is None


def test_mark_finished_records_error_on_failure(db_path):
    op_id = oh.create_operation("jarvis", "stop", db_path=db_path)
    oh.mark_running(op_id, db_path=db_path)
    oh.mark_finished(op_id, "FAILED", error="port never released", db_path=db_path)

    row = oh.get_recent_operations(db_path=db_path)[0]
    assert row["status"] == "FAILED"
    assert row["error"] == "port never released"


def test_get_recent_operations_orders_newest_first(db_path):
    first = oh.create_operation("jarvis", "start", db_path=db_path)
    time.sleep(0.01)
    second = oh.create_operation("opencode", "start", db_path=db_path)

    rows = oh.get_recent_operations(db_path=db_path)
    assert [r["operation_id"] for r in rows] == [second, first]


def test_get_recent_operations_filters_by_target(db_path):
    oh.create_operation("jarvis", "start", db_path=db_path)
    oh.create_operation("opencode", "start", db_path=db_path)
    oh.create_operation("opencode", "stop", db_path=db_path)

    rows = oh.get_recent_operations(target="opencode", db_path=db_path)
    assert len(rows) == 2
    assert all(r["target"] == "opencode" for r in rows)


def test_get_recent_operations_filters_by_action_and_status(db_path):
    a = oh.create_operation("jarvis", "restart", db_path=db_path)
    oh.mark_running(a, db_path=db_path)
    oh.mark_finished(a, "SUCCEEDED", db_path=db_path)
    b = oh.create_operation("jarvis", "restart", db_path=db_path)
    oh.mark_running(b, db_path=db_path)
    oh.mark_finished(b, "FAILED", error="boom", db_path=db_path)

    failed_restarts = oh.get_recent_operations(action="restart", status="FAILED", db_path=db_path)
    assert len(failed_restarts) == 1
    assert failed_restarts[0]["operation_id"] == b


def test_get_recent_operations_respects_limit(db_path):
    for _ in range(5):
        oh.create_operation("jarvis", "start", db_path=db_path)
    rows = oh.get_recent_operations(limit=2, db_path=db_path)
    assert len(rows) == 2


def test_detail_round_trips_as_json(db_path):
    oh.create_operation("connectivity_policy", "set_policy", detail={"mode": "wifi_only"}, db_path=db_path)
    row = oh.get_recent_operations(db_path=db_path)[0]
    import json
    assert json.loads(row["detail"]) == {"mode": "wifi_only"}
