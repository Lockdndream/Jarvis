"""OpenRouter account-credit guardrail for the standing delegated-agent
workforce (Milestone 9B.2, ADR-013). ADR-009's per-use re-approval no
longer applies to delegated OpenCode work — this module is the
replacement safety mechanism: before launching a delegation batch, check
real remaining credit via OpenRouter's own `GET /api/v1/key` endpoint
(verified against the real API, not assumed from documentation alone —
see ADR-013) and refuse to say "proceed" if it's at or below a
configurable threshold.

This module only answers "should we pause," it never itself blocks or
retries a paid call — the caller (Chief-Engineer-side delegation
orchestration) decides what to do with that answer.
"""
import os

import httpx

DEFAULT_CREDIT_THRESHOLD_USD = 1.0


class CreditCheckError(Exception):
    """Raised when the OpenRouter balance cannot be determined. Callers
    must treat this as "cannot safely proceed," not "assume fine and
    continue" — a guardrail that fails silently isn't one."""


def credit_threshold_usd() -> float:
    """JARVIS_OPENCODE_CREDIT_THRESHOLD_USD, defaulting to $1.00 — chosen
    as roughly 20% of this project's current $5 OpenRouter key limit
    (real balance checked during ADR-013's design), leaving enough margin
    to finish an in-flight delegation task rather than pausing mid-way."""
    raw = os.environ.get("JARVIS_OPENCODE_CREDIT_THRESHOLD_USD")
    if not raw:
        return DEFAULT_CREDIT_THRESHOLD_USD
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_CREDIT_THRESHOLD_USD


def get_remaining_credit_usd(api_key: str | None = None) -> float | None:
    """Remaining OpenRouter credit in USD for the configured key, or None
    if the key has no spending limit set (`limit` is null — unlimited,
    nothing to guard against). Raises CreditCheckError on any failure to
    reach or parse the endpoint."""
    key = api_key or os.environ.get("JARVIS_LLM_API_KEY")
    if not key:
        raise CreditCheckError("JARVIS_LLM_API_KEY is not set — cannot check OpenRouter credit")
    try:
        response = httpx.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()["data"]
    except Exception as e:
        raise CreditCheckError(f"Failed to check OpenRouter credit: {e}") from e
    return data.get("limit_remaining")


def should_pause_for_credit(
    threshold_usd: float | None = None,
    api_key: str | None = None,
) -> tuple[bool, float | None]:
    """(should_pause, remaining_usd). should_pause is True only when a
    real spending limit exists and remaining credit is at or below the
    threshold — a key with no limit (remaining=None) never pauses, since
    there is no ceiling to guard against."""
    threshold = threshold_usd if threshold_usd is not None else credit_threshold_usd()
    remaining = get_remaining_credit_usd(api_key)
    if remaining is None:
        return False, None
    return remaining <= threshold, remaining
