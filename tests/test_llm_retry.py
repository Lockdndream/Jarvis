"""Interaction Layer v1 (Goal 4): LLMProvider.chat_completion must retry a
bounded number of times on both transport failures and non-200 responses,
and must never silently mask a persistent failure as a normal assistant
reply (the pre-fix behavior for non-200, and the direct cause of a real
voice-turn collapsing with no recovery — see the Level 4 finding this
milestone traces back to).
"""
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.supervisor.llm import LLMProvider


def _configured_provider(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("JARVIS_LLM_FREE_ONLY", raising=False)
    return LLMProvider()


def _patch_client(monkeypatch, transport: httpx.MockTransport):
    # llm.py does `import httpx` lazily, inside chat_completion() — that
    # local import still resolves via sys.modules, so patching the
    # AsyncClient attribute on the real, shared httpx module here reaches
    # it regardless of where it's imported.
    real_async_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)


@pytest.mark.asyncio
async def test_recovers_after_transient_500(monkeypatch):
    provider = _configured_provider(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(500, text="upstream hiccup")
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "recovered"}}],
        })

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = await provider.chat_completion([{"role": "user", "content": "hi"}])

    assert calls["n"] == 2
    assert result["content"] == "recovered"


@pytest.mark.asyncio
async def test_persistent_500_raises_instead_of_masquerading(monkeypatch):
    provider = _configured_provider(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="still down")

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.chat_completion([{"role": "user", "content": "hi"}])

    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_recovers_after_transient_connect_error(monkeypatch):
    provider = _configured_provider(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectTimeout("simulated timeout", request=request)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "recovered"}}],
        })

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = await provider.chat_completion([{"role": "user", "content": "hi"}])

    assert calls["n"] == 2
    assert result["content"] == "recovered"


@pytest.mark.asyncio
async def test_persistent_connect_error_raises_after_bounded_attempts(monkeypatch):
    provider = _configured_provider(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(httpx.ConnectTimeout):
        await provider.chat_completion([{"role": "user", "content": "hi"}])

    assert calls["n"] == 3
