"""Tests for the Groq Whisper speech-to-text client."""

import httpx
import pytest

from app.stt import GroqTranscriptionError, transcribe


@pytest.fixture(autouse=True)
def set_groq_key(monkeypatch):
    """Most tests need a configured API key."""
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")


def _mock_async_client(handler: httpx.MockTransport):
    """Return a factory that builds an httpx.AsyncClient over ``handler``.

    Captures the real ``httpx.AsyncClient`` class at import time so the
    factory does not recurse into any monkeypatched replacement.
    """
    real_async_client = httpx.AsyncClient

    def factory(**kwargs):
        return real_async_client(transport=handler)

    return factory


@pytest.mark.asyncio
async def test_transcribe_success(monkeypatch):
    """A successful verbose_json response is parsed and cost is computed."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(
            200,
            json={
                "text": "Hello world",
                "duration": 12.34,
            },
        )

    monkeypatch.setattr(
        "app.stt.httpx.AsyncClient",
        _mock_async_client(httpx.MockTransport(handler)),
    )

    result = await transcribe(b"fake-audio-bytes", filename="audio.webm")

    assert result.text == "Hello world"
    assert result.duration_seconds == 12.34
    assert result.cost_usd == pytest.approx(12.34 / 3600 * 0.04)
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer test-groq-key"
    assert "multipart/form-data" in captured["content_type"]


@pytest.mark.asyncio
async def test_transcribe_missing_api_key_raises_and_makes_no_request(monkeypatch):
    """With GROQ_API_KEY empty, transcribe raises before any network call."""
    monkeypatch.setenv("GROQ_API_KEY", "")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No HTTP request should be made without an API key")

    monkeypatch.setattr(
        "app.stt.httpx.AsyncClient",
        _mock_async_client(httpx.MockTransport(handler)),
    )

    with pytest.raises(GroqTranscriptionError):
        await transcribe(b"fake-audio-bytes")


@pytest.mark.asyncio
async def test_transcribe_api_error_propagates(monkeypatch):
    """A non-2xx response from Groq raises through httpx."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid API key"})

    monkeypatch.setattr(
        "app.stt.httpx.AsyncClient",
        _mock_async_client(httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await transcribe(b"fake-audio-bytes")
