"""ADR-023 Phase 2/3: connectivity policy write (localhost-only Operations
API) vs. read (existing, LAN-open GET /api/settings) split, and the
observational phone-status endpoint.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db

_ENV_BEFORE_IMPORT = dict(os.environ)
import app.main as _main_module
for _leaked_var in set(os.environ) - set(_ENV_BEFORE_IMPORT):
    if _leaked_var.startswith("JARVIS_LLM") or _leaked_var.startswith("OPENCODE_") or _leaked_var.startswith("JARVIS_VAPID"):
        del os.environ[_leaked_var]


def _client(monkeypatch, host="127.0.0.1"):
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    main_module = _main_module
    sv = main_module.opencode_supervisor

    async def noop(self):
        pass
    monkeypatch.setattr(sv, "start", noop.__get__(sv))
    monkeypatch.setattr(sv, "stop", noop.__get__(sv))

    monkeypatch.delenv("JARVIS_VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("JARVIS_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    from fastapi.testclient import TestClient
    c = TestClient(main_module.app, client=(host, 50000))
    return c, path


@pytest.fixture
def client(monkeypatch):
    c, path = _client(monkeypatch)
    with c:
        yield c
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def remote_client(monkeypatch):
    c, path = _client(monkeypatch, host="10.0.0.5")
    with c:
        yield c
    if os.path.exists(path):
        os.unlink(path)


# ── write path: localhost-only ──────────────────────────────────────

def test_get_policy_rejected_from_non_localhost(remote_client):
    resp = remote_client.get("/api/operations/connectivity/policy")
    assert resp.status_code == 403


def test_set_policy_rejected_from_non_localhost(remote_client):
    resp = remote_client.post("/api/operations/connectivity/policy", json={"mode": "wifi_only"})
    assert resp.status_code == 403


def test_default_policy_is_always(client):
    resp = client.get("/api/operations/connectivity/policy")
    assert resp.status_code == 200
    assert resp.json() == {"mode": "always"}


def test_set_policy_persists_and_reads_back(client):
    resp = client.post("/api/operations/connectivity/policy", json={"mode": "wifi_only"})
    assert resp.status_code == 200
    assert resp.json() == {"mode": "wifi_only"}

    resp = client.get("/api/operations/connectivity/policy")
    assert resp.json() == {"mode": "wifi_only"}


def test_set_policy_rejects_invalid_mode(client):
    resp = client.post("/api/operations/connectivity/policy", json={"mode": "sometimes"})
    assert resp.status_code == 400


def test_set_policy_does_not_require_confirmation(client):
    """Config, not a destructive action -- ADR-023's Decision section."""
    resp = client.post("/api/operations/connectivity/policy", json={"mode": "manual"})
    assert resp.status_code == 200


# ── read path: existing, LAN-open /api/settings ─────────────────────

def test_settings_endpoint_reachable_from_non_localhost(remote_client):
    """The phone is not localhost -- this is the whole point of the
    write/read split."""
    resp = remote_client.get("/api/settings")
    assert resp.status_code == 200


def test_settings_endpoint_reflects_the_policy_set_via_operations(client):
    client.post("/api/operations/connectivity/policy", json={"mode": "wifi_only"})
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["connectivity_mode"] == "wifi_only"


def test_settings_endpoint_defaults_to_always_when_never_set(client):
    resp = client.get("/api/settings")
    assert resp.json()["connectivity_mode"] == "always"


# ── phone-status: observational only ────────────────────────────────

def test_phone_status_rejected_from_non_localhost(remote_client):
    resp = remote_client.get("/api/operations/connectivity/phone-status")
    assert resp.status_code == 403


def test_phone_status_reports_no_device_when_none_connected(client):
    resp = client.get("/api/operations/connectivity/phone-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_status"] is None
    assert body["connections_now"] == 0
