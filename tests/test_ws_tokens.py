"""Tests for app/integrations/ws_tokens.py — short-lived signed WebSocket tokens."""
import jwt
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.integrations.ws_tokens import (
    DEFAULT_TTL_SECONDS,
    TokenVerificationResult,
    _get_secret,
    issue_ws_token,
    verify_ws_token,
    ws_token_ttl_seconds,
)


def test_issue_and_verify_valid_token():
    result = issue_ws_token("test-user")
    assert "token" in result
    assert "expires_at" in result
    assert "expires_in" in result
    assert result["expires_in"] == DEFAULT_TTL_SECONDS

    vr = verify_ws_token(result["token"])
    assert vr.ok is True
    assert vr.subject == "test-user"
    assert vr.reason is None


def test_expired_token():
    result = issue_ws_token("ephemeral", ttl_seconds=-1)
    vr = verify_ws_token(result["token"])
    assert vr.ok is False
    assert vr.reason == "expired"
    assert vr.subject is None


def test_tampered_token():
    result = issue_ws_token("tamper-me")
    token = result["token"]
    parts = token.split(".")
    # Flip a character in the signature portion
    sig = parts[2]
    tampered_sig = ("X" if sig[0] != "X" else "Y") + sig[1:]
    tampered = parts[0] + "." + parts[1] + "." + tampered_sig
    vr = verify_ws_token(tampered)
    assert vr.ok is False
    assert vr.reason == "invalid"


def test_malformed_garbage_string():
    vr = verify_ws_token("this.is.not.a.jwt")
    assert vr.ok is False
    assert vr.reason == "invalid"


def test_empty_string():
    vr = verify_ws_token("")
    assert vr.ok is False
    assert vr.reason == "invalid"


def test_none_input():
    vr = verify_ws_token(None)  # type: ignore[arg-type]
    assert vr.ok is False
    assert vr.reason == "invalid"


def test_ws_token_ttl_seconds_env_var(monkeypatch):
    monkeypatch.setenv("JARVIS_WS_TOKEN_TTL_SECONDS", "300")
    assert ws_token_ttl_seconds() == 300


def test_ws_token_ttl_seconds_fallback_default(monkeypatch):
    monkeypatch.delenv("JARVIS_WS_TOKEN_TTL_SECONDS", raising=False)
    assert ws_token_ttl_seconds() == DEFAULT_TTL_SECONDS


def test_ws_token_ttl_seconds_malformed_fallback(monkeypatch):
    monkeypatch.setenv("JARVIS_WS_TOKEN_TTL_SECONDS", "not-a-number")
    assert ws_token_ttl_seconds() == DEFAULT_TTL_SECONDS


def test_forged_non_numeric_exp_is_rejected_not_crashed():
    """Independent-review finding (Milestone 9B.2 pilot): a validly-signed
    token whose exp claim is a non-numeric type (forgeable only by whoever
    holds the secret, but must still never crash the server) must be
    rejected as invalid, not raise TypeError from the now > exp comparison.
    Confirmed as a real crash before the fix via direct reproduction."""
    forged = jwt.encode(
        {"sub": "attacker", "iat": int(time.time()), "exp": "not-a-number"},
        _get_secret(),
        algorithm="HS256",
    )
    vr = verify_ws_token(forged)  # must not raise
    assert vr.ok is False
    assert vr.reason == "invalid"


def test_forged_null_exp_is_rejected():
    """A validly-signed token with exp: null must not verify as ok=True
    (would mean a token that never expires)."""
    forged = jwt.encode(
        {"sub": "attacker", "iat": int(time.time()), "exp": None},
        _get_secret(),
        algorithm="HS256",
    )
    vr = verify_ws_token(forged)
    assert vr.ok is False


def test_token_claims_are_minimal(monkeypatch):
    monkeypatch.setenv("JARVIS_WS_TOKEN_SECRET", "test-secret-for-claim-inspection")
    result = issue_ws_token("claims-check")
    token = result["token"]
    # Decode without signature verification to inspect the payload
    payload = jwt.decode(
        token,
        options={"verify_signature": False},
    )
    assert set(payload.keys()) == {"sub", "iat", "exp"}
    assert payload["sub"] == "claims-check"
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    assert payload["exp"] > payload["iat"]