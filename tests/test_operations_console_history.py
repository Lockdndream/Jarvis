"""ADR-023 Phase 6: the console's Operation-tracking wiring -- verifies
Jarvis actions and (proxied) OpenCode actions both produce real Operation
history rows, filterable via GET /api/console/operations.

JARVIS_OPERATIONS_DB is set to an isolated temp path before importing
app.operations_console, so this never touches a real operator's history.
"""
import os
import sys
import tempfile
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)
os.environ["JARVIS_OPERATIONS_DB"] = _DB_PATH

import app.operations_console as console_module
from app import operation_history as oh
from app.operational_state import OperationalState


@pytest.fixture(autouse=True)
def clean_history():
    oh.init_db(db_path=_DB_PATH)
    conn = oh._connect(_DB_PATH)
    conn.execute("DELETE FROM operations")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    with TestClient(console_module.app, client=("127.0.0.1", 50000)) as c:
        yield c


def _wait_for_operations(target, action, count=1, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = oh.get_recent_operations(target=target, action=action, db_path=_DB_PATH)
        if len(rows) >= count and all(r["status"] in ("SUCCEEDED", "FAILED") for r in rows[:count]):
            return rows
        time.sleep(0.05)
    raise AssertionError(f"operations for {target}/{action} never settled: {oh.get_recent_operations(db_path=_DB_PATH)}")


def test_jarvis_start_creates_a_settled_operation_record(client, monkeypatch):
    async def fake_run_start():
        return True
    monkeypatch.setattr(console_module.jarvis_manager, "claim_start", lambda: True)
    monkeypatch.setattr(console_module.jarvis_manager, "run_claimed_start", fake_run_start)
    monkeypatch.setattr(console_module.jarvis_manager, "state", OperationalState.STARTING)
    monkeypatch.setattr(console_module.jarvis_manager, "last_operation_error", None)

    async def fake_snapshot():
        return {"state": "STARTING", "health": "UNKNOWN", "last_operation": "start",
                "last_operation_result": None, "last_operation_error": None, "last_updated": None}
    monkeypatch.setattr(console_module.jarvis_manager, "snapshot_status", fake_snapshot)

    resp = client.post("/api/console/jarvis/start")
    assert resp.status_code == 202

    rows = _wait_for_operations("jarvis", "start")
    assert rows[0]["status"] == "SUCCEEDED"
    assert rows[0]["operator"] == "local"
    assert rows[0]["duration_ms"] is not None


def test_jarvis_start_failure_is_recorded_with_error(client, monkeypatch):
    async def fake_run_start_fail():
        return False
    monkeypatch.setattr(console_module.jarvis_manager, "claim_start", lambda: True)
    monkeypatch.setattr(console_module.jarvis_manager, "run_claimed_start", fake_run_start_fail)
    monkeypatch.setattr(console_module.jarvis_manager, "last_operation_error", "port conflict")

    async def fake_snapshot():
        return {"state": "FAILED", "health": "UNKNOWN", "last_operation": "start",
                "last_operation_result": "failure", "last_operation_error": "port conflict", "last_updated": None}
    monkeypatch.setattr(console_module.jarvis_manager, "snapshot_status", fake_snapshot)

    resp = client.post("/api/console/jarvis/start")
    assert resp.status_code == 202

    rows = _wait_for_operations("jarvis", "start")
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["error"] == "port conflict"


def test_jarvis_start_conflict_never_creates_an_operation_record(client, monkeypatch):
    monkeypatch.setattr(console_module.jarvis_manager, "claim_start", lambda: False)
    monkeypatch.setattr(console_module.jarvis_manager, "state", "RUNNING")

    resp = client.post("/api/console/jarvis/start")
    assert resp.status_code == 409

    rows = oh.get_recent_operations(target="jarvis", action="start", db_path=_DB_PATH)
    assert rows == [], "a rejected (409) request must not create an Operation row"


def test_opencode_start_via_proxy_creates_a_settled_operation_record(client, monkeypatch):
    call_count = {"n": 0}

    async def fake_proxy(method, path, json=None):
        if path == "/api/operations/opencode/start":
            return 202, {"action": "start", "target": "opencode", "state": "STARTING",
                          "last_operation": "start", "last_operation_result": None}
        call_count["n"] += 1
        if call_count["n"] < 2:
            return 200, {"state": "STARTING", "last_operation": "start", "last_operation_result": None}
        return 200, {"state": "RUNNING", "last_operation": "start", "last_operation_result": "success",
                     "last_operation_error": None}

    monkeypatch.setattr(console_module, "_proxy", fake_proxy)

    resp = client.post("/api/console/opencode/start")
    assert resp.status_code == 202

    rows = _wait_for_operations("opencode", "start")
    assert rows[0]["status"] == "SUCCEEDED"


def test_connectivity_policy_set_via_console_proxy_creates_operation_record(client, monkeypatch):
    async def fake_proxy(method, path, json=None):
        assert path == "/api/operations/connectivity/policy"
        assert json == {"mode": "wifi_only"}
        return 200, {"mode": "wifi_only"}
    monkeypatch.setattr(console_module, "_proxy", fake_proxy)

    resp = client.post("/api/console/connectivity/policy", json={"mode": "wifi_only"})
    assert resp.status_code == 200
    assert resp.json() == {"mode": "wifi_only"}

    rows = oh.get_recent_operations(target="connectivity_policy", action="set_policy", db_path=_DB_PATH)
    assert len(rows) == 1
    assert rows[0]["status"] == "SUCCEEDED"
    import json as json_module
    assert json_module.loads(rows[0]["detail"]) == {"mode": "wifi_only"}


def test_connectivity_policy_rejection_does_not_create_operation_record(client, monkeypatch):
    async def fake_proxy(method, path, json=None):
        return 400, {"detail": "mode must be one of [...]"}
    monkeypatch.setattr(console_module, "_proxy", fake_proxy)

    resp = client.post("/api/console/connectivity/policy", json={"mode": "bogus"})
    assert resp.status_code == 400

    rows = oh.get_recent_operations(target="connectivity_policy", db_path=_DB_PATH)
    assert rows == []


def test_dashboard_aggregates_all_subsystems_when_jarvis_reachable(client, monkeypatch):
    async def fake_proxy(method, path, json=None):
        if path == "/api/operations/status":
            return 200, {"opencode": {"state": "RUNNING"}, "supervisor": {"state": "idle"}, "websocket": {"connections_now": 1}}
        if path == "/api/operations/connectivity/policy":
            return 200, {"mode": "wifi_only"}
        if path == "/api/operations/connectivity/phone-status":
            return 200, {"device_status": {"battery": 80}}
        raise AssertionError(f"unexpected path {path}")
    monkeypatch.setattr(console_module, "_proxy", fake_proxy)

    async def fake_snapshot():
        return {"state": "RUNNING", "health": "HEALTHY"}
    monkeypatch.setattr(console_module.jarvis_manager, "snapshot_status", fake_snapshot)

    resp = client.get("/api/console/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jarvis"]["state"] == "RUNNING"
    assert body["opencode"]["state"] == "RUNNING"
    assert body["supervisor"]["state"] == "idle"
    assert body["websocket"]["connections_now"] == 1
    assert body["connectivity_mode"] == "wifi_only"
    assert body["phone"]["device_status"]["battery"] == 80


def test_dashboard_degrades_gracefully_when_jarvis_unreachable(client, monkeypatch):
    async def fake_proxy(method, path, json=None):
        raise HTTPException(status_code=502, detail="Cannot reach Jarvis")
    monkeypatch.setattr(console_module, "_proxy", fake_proxy)

    async def fake_snapshot():
        return {"state": "STOPPED", "health": "UNKNOWN"}
    monkeypatch.setattr(console_module.jarvis_manager, "snapshot_status", fake_snapshot)

    resp = client.get("/api/console/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jarvis"]["state"] == "STOPPED"
    assert body["opencode"] is None
    assert body["supervisor"] is None
    assert body["websocket"] is None
    assert body["connectivity_mode"] is None
    assert body["phone"] is None


def test_operations_history_endpoint_filters(client):
    oh.create_operation("jarvis", "start", db_path=_DB_PATH)
    op2 = oh.create_operation("opencode", "restart", db_path=_DB_PATH)
    oh.mark_running(op2, db_path=_DB_PATH)
    oh.mark_finished(op2, "FAILED", error="boom", db_path=_DB_PATH)

    resp = client.get("/api/console/operations", params={"target": "opencode"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["operation_id"] == op2
    assert body[0]["status"] == "FAILED"
