"""Tests for the centralized configuration layer."""
import os

import pytest

from app import config


class TestBoolHelpers:
    def test_common_truthy_values(self, monkeypatch):
        monkeypatch.setenv("JARVIS_NOTIFY_ON_COMPLETION", "true")
        assert config.notify_on_completion() is True
        monkeypatch.setenv("JARVIS_NOTIFY_ON_COMPLETION", "1")
        assert config.notify_on_completion() is True
        monkeypatch.setenv("JARVIS_NOTIFY_ON_COMPLETION", "yes")
        assert config.notify_on_completion() is True
        monkeypatch.setenv("JARVIS_NOTIFY_ON_COMPLETION", "FALSE")
        assert config.notify_on_completion() is False

    def test_supervisor_enabled_only_accepts_one(self, monkeypatch):
        monkeypatch.setenv("JARVIS_SUPERVISOR_ENABLED", "1")
        assert config.supervisor_enabled() is True
        monkeypatch.setenv("JARVIS_SUPERVISOR_ENABLED", "true")
        assert config.supervisor_enabled() is False
        monkeypatch.setenv("JARVIS_SUPERVISOR_ENABLED", "yes")
        assert config.supervisor_enabled() is False

    def test_operations_verify_tls_only_accepts_true(self, monkeypatch):
        monkeypatch.setenv("JARVIS_OPERATIONS_VERIFY_TLS", "true")
        assert config.operations_verify_tls_default() is True
        monkeypatch.setenv("JARVIS_OPERATIONS_VERIFY_TLS", "1")
        assert config.operations_verify_tls_default() is False


class TestIntHelpers:
    def test_int_invalid_raises_by_default(self, monkeypatch):
        monkeypatch.setenv("JARVIS_OPENCODE_PORT", "not-a-number")
        with pytest.raises(ValueError):
            config.opencode_port()

    def test_int_invalid_fallback_when_requested(self, monkeypatch):
        monkeypatch.setenv("JARVIS_WS_TOKEN_TTL_SECONDS", "not-a-number")
        assert config.ws_token_ttl_seconds() == 900


class TestCallTimeReads:
    """Accessors must read os.environ at call time, not import time."""

    def test_accessor_reads_env_at_call_time(self, monkeypatch):
        monkeypatch.delenv("JARVIS_LLM_MODEL", raising=False)
        assert config.llm_model() == "gpt-4o-mini"
        monkeypatch.setenv("JARVIS_LLM_MODEL", "custom-model")
        assert config.llm_model() == "custom-model"


class TestPathDefaults:
    def test_projects_file_default_is_repo_relative(self):
        path = config.projects_file_default()
        assert path.endswith("projects.json")
        assert os.path.isabs(path)

    def test_opencode_exe_default(self, monkeypatch):
        monkeypatch.delenv("JARVIS_OPENCODE_EXE", raising=False)
        assert config.opencode_exe_default() == r"C:\Users\Admin\.bun\bin\opencode.exe"


class TestVapid:
    def test_vapid_configured_requires_both_keys(self, monkeypatch):
        monkeypatch.delenv("JARVIS_VAPID_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("JARVIS_VAPID_PRIVATE_KEY", raising=False)
        assert config.vapid_configured() is False
        monkeypatch.setenv("JARVIS_VAPID_PUBLIC_KEY", "pub")
        assert config.vapid_configured() is False
        monkeypatch.setenv("JARVIS_VAPID_PRIVATE_KEY", "priv")
        assert config.vapid_configured() is True


class TestSnooze:
    def test_default_snooze_minutes_invalid_returns_none(self, monkeypatch):
        monkeypatch.setenv("JARVIS_DEFAULT_SNOOZE_MINUTES", "not-a-number")
        assert config.default_snooze_minutes() is None

    def test_default_snooze_minutes_non_positive_returns_none(self, monkeypatch):
        monkeypatch.setenv("JARVIS_DEFAULT_SNOOZE_MINUTES", "0")
        assert config.default_snooze_minutes() is None


class TestSubprocessEnv:
    def test_os_essential_env_uses_allowlist(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "should-not-appear")
        env = config.os_essential_subprocess_env()
        assert "OPENAI_API_KEY" not in env
        assert "PATH" in env

    def test_subprocess_env_includes_extra(self, monkeypatch):
        env = config.subprocess_env({"EXTRA": "value"})
        assert env["EXTRA"] == "value"
        assert "PATH" in env
