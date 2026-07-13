"""Short-lived signed WebSocket tokens (HS256 JWTs) — Milestone 9B.2, ADR-014.

Provides a unified WebSocket auth mechanism that works for browsers, which
cannot set custom headers on a WebSocket handshake (unlike the Android
companion's existing Authorization-header approach).  Tokens are passed as
a query parameter (?token=...) and verified server-side.

Default TTL is 900 seconds (15 minutes):
- Short enough to bound exposure if a token leaks into a log line.
- Long enough that normal reconnect cycles (the Android client backs off up
  to 60 seconds between attempts) don't require a fresh token on every
  single reconnect.
"""

import os
import secrets
import time

import jwt

DEFAULT_TTL_SECONDS = 900

_SECRET: str | None = None


def _get_secret() -> str:
    global _SECRET
    if _SECRET is not None:
        return _SECRET
    env_secret = os.environ.get("JARVIS_WS_TOKEN_SECRET")
    if env_secret:
        _SECRET = env_secret
    else:
        # Generate once per process lifetime.  Tokens won't survive a process
        # restart, but that's fine — WebSocket connections don't either.
        _SECRET = secrets.token_urlsafe(32)
    return _SECRET


def ws_token_ttl_seconds() -> int:
    raw = os.environ.get("JARVIS_WS_TOKEN_TTL_SECONDS")
    if raw is not None:
        try:
            return int(raw)
        except (ValueError, TypeError):
            pass
    return DEFAULT_TTL_SECONDS


class TokenVerificationResult:
    __slots__ = ("ok", "subject", "reason")

    def __init__(self, ok: bool, subject: str | None = None, reason: str | None = None):
        self.ok = ok
        self.subject = subject
        self.reason = reason


def issue_ws_token(subject: str, ttl_seconds: int | None = None) -> dict:
    if ttl_seconds is None:
        ttl_seconds = ws_token_ttl_seconds()
    now = int(time.time())
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    token = jwt.encode(payload, _get_secret(), algorithm="HS256")
    return {
        "token": token,
        "expires_at": payload["exp"],
        "expires_in": ttl_seconds,
    }


def verify_ws_token(token: str) -> TokenVerificationResult:
    try:
        payload = jwt.decode(
            token,
            _get_secret(),
            algorithms=["HS256"],
            options={
                "require": ["sub", "iat", "exp"],
                "verify_exp": False,
            },
        )
    except Exception:
        return TokenVerificationResult(ok=False, reason="invalid")

    # Independent-review finding (Milestone 9B.2 pilot): a validly-signed
    # token whose `exp` claim is present but not a real number (e.g. a
    # string, forged by whoever holds the secret) reached the `now > exp`
    # comparison and raised an uncaught TypeError — confirmed by direct
    # test, not just asserted. `require: [...]` only checks the claim
    # *key* exists, not its type. Guard explicitly rather than trust the
    # claim's shape.
    now = int(time.time())
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return TokenVerificationResult(ok=False, reason="invalid")
    if now > exp:
        return TokenVerificationResult(ok=False, reason="expired")

    return TokenVerificationResult(ok=True, subject=payload.get("sub"))
