"""Tests for .env configuration loading."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.supervisor.llm import validate_free_only_model, ModelNotAllowedError

_ENV_KEYS = [
    "JARVIS_LLM_PROVIDER",
    "JARVIS_LLM_BASE_URL",
    "JARVIS_LLM_MODEL",
    "JARVIS_LLM_FREE_ONLY",
    "JARVIS_LLM_API_KEY",
]


def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def env_file():
    """Create a temporary .env file."""
    content = """
JARVIS_LLM_PROVIDER=openrouter
JARVIS_LLM_BASE_URL=https://openrouter.ai/api/v1
JARVIS_LLM_MODEL=openrouter/free
JARVIS_LLM_FREE_ONLY=true
JARVIS_LLM_API_KEY=
"""
    f, path = tempfile.mkstemp(suffix=".env")
    os.close(f)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_dotenv_importable():
    """python-dotenv is installed and importable."""
    import dotenv
    assert hasattr(dotenv, "load_dotenv")


def test_dotenv_loads(env_file, monkeypatch):
    """load_dotenv reads values from .env file."""
    _clean_env(monkeypatch)
    from dotenv import load_dotenv
    loaded = load_dotenv(env_file)
    assert loaded, "load_dotenv should return True"
    assert os.environ.get("JARVIS_LLM_PROVIDER") == "openrouter"
    assert os.environ.get("JARVIS_LLM_BASE_URL") == "https://openrouter.ai/api/v1"
    assert os.environ.get("JARVIS_LLM_MODEL") == "openrouter/free"
    assert os.environ.get("JARVIS_LLM_FREE_ONLY") == "true"
    assert os.environ.get("JARVIS_LLM_API_KEY") == ""


def test_dotenv_does_not_override_existing(env_file, monkeypatch):
    """Existing environment variables take precedence over .env values."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "custom_provider")
    from dotenv import load_dotenv
    load_dotenv(env_file)
    assert os.environ["JARVIS_LLM_PROVIDER"] == "custom_provider"
    # Other vars from .env should still be loaded
    assert os.environ.get("JARVIS_LLM_BASE_URL") == "https://openrouter.ai/api/v1"


def test_free_only_works_after_dotenv(env_file, monkeypatch):
    """Free-only validation works correctly after dotenv loading."""
    _clean_env(monkeypatch)
    from dotenv import load_dotenv
    load_dotenv(env_file)
    # Should allow openrouter/free
    validate_free_only_model("openrouter/free")
    # Should allow :free suffix
    validate_free_only_model("google/gemini-2.0-flash:free")
    # Should reject paid
    with pytest.raises(ModelNotAllowedError):
        validate_free_only_model("gpt-4o")


def test_free_only_accepts_after_dotenv_with_key(env_file, monkeypatch):
    """Free-only guard accepts model and is_configured when API key present."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-test-key")
    from dotenv import load_dotenv
    load_dotenv(env_file)
    from app.supervisor.llm import LLMProvider
    provider = LLMProvider()
    assert provider.is_configured
    assert provider._rejection_reason is None


def test_free_only_accepts_after_dotenv(env_file, monkeypatch):
    """Free-only guard accepts openrouter/free loaded from .env."""
    _clean_env(monkeypatch)
    from dotenv import load_dotenv
    load_dotenv(env_file)
    from app.supervisor.llm import LLMProvider
    provider = LLMProvider()
    # Model is accepted (no rejection), but is_configured is False because API key is empty
    assert provider._rejection_reason is None
