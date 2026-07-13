"""Milestone 9B.2 (ADR-013): the credit-threshold guardrail that replaces
per-use re-approval for the standing DeepSeek delegation workforce. Every
test mocks the real network call — this module talks to the real
OpenRouter API in production, but a unit test must never depend on
network access or a real account balance.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.integrations import openrouter_credits as oc


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_credit_threshold_defaults_to_one_dollar(monkeypatch):
    monkeypatch.delenv("JARVIS_OPENCODE_CREDIT_THRESHOLD_USD", raising=False)
    assert oc.credit_threshold_usd() == 1.0


def test_credit_threshold_reads_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_OPENCODE_CREDIT_THRESHOLD_USD", "2.50")
    assert oc.credit_threshold_usd() == 2.50


def test_credit_threshold_falls_back_on_malformed_env(monkeypatch):
    monkeypatch.setenv("JARVIS_OPENCODE_CREDIT_THRESHOLD_USD", "not-a-number")
    assert oc.credit_threshold_usd() == 1.0


def test_get_remaining_credit_parses_real_response_shape(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert url == "https://openrouter.ai/api/v1/key"
        assert headers["Authorization"] == "Bearer test-key"
        return FakeResponse({"data": {"limit": 5, "limit_remaining": 4.97}})

    monkeypatch.setattr(oc.httpx, "get", fake_get)
    assert oc.get_remaining_credit_usd(api_key="test-key") == 4.97


def test_get_remaining_credit_none_when_unlimited(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse({"data": {"limit": None, "limit_remaining": None}})

    monkeypatch.setattr(oc.httpx, "get", fake_get)
    assert oc.get_remaining_credit_usd(api_key="test-key") is None


def test_get_remaining_credit_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("JARVIS_LLM_API_KEY", raising=False)
    with pytest.raises(oc.CreditCheckError):
        oc.get_remaining_credit_usd(api_key=None)


def test_get_remaining_credit_raises_on_http_error(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse({}, status_code=401)

    monkeypatch.setattr(oc.httpx, "get", fake_get)
    with pytest.raises(oc.CreditCheckError):
        oc.get_remaining_credit_usd(api_key="test-key")


def test_get_remaining_credit_raises_on_network_failure(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise ConnectionError("network down")

    monkeypatch.setattr(oc.httpx, "get", fake_get)
    with pytest.raises(oc.CreditCheckError):
        oc.get_remaining_credit_usd(api_key="test-key")


def test_should_pause_true_when_remaining_at_or_below_threshold(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse({"data": {"limit": 5, "limit_remaining": 1.0}})

    monkeypatch.setattr(oc.httpx, "get", fake_get)
    should_pause, remaining = oc.should_pause_for_credit(threshold_usd=1.0, api_key="test-key")
    assert should_pause is True
    assert remaining == 1.0


def test_should_pause_false_when_remaining_above_threshold(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse({"data": {"limit": 5, "limit_remaining": 4.97}})

    monkeypatch.setattr(oc.httpx, "get", fake_get)
    should_pause, remaining = oc.should_pause_for_credit(threshold_usd=1.0, api_key="test-key")
    assert should_pause is False
    assert remaining == 4.97


def test_should_pause_false_when_key_has_no_limit(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse({"data": {"limit": None, "limit_remaining": None}})

    monkeypatch.setattr(oc.httpx, "get", fake_get)
    should_pause, remaining = oc.should_pause_for_credit(threshold_usd=1.0, api_key="test-key")
    assert should_pause is False
    assert remaining is None
