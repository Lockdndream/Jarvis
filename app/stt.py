"""Groq Whisper speech-to-text client.

This is a standalone module. Callers pass raw audio bytes and receive a
``TranscriptResult``; no WebSocket or session management is performed here.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import httpx

from app import config

logger = logging.getLogger(__name__)

GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-large-v3-turbo"
COST_PER_HOUR_USD = 0.04


class GroqTranscriptionError(Exception):
    """Raised when the Groq transcription request cannot be made."""


@dataclass
class TranscriptResult:
    """Result of a successful Groq Whisper transcription."""

    text: str
    duration_seconds: float
    cost_usd: float


def _compute_cost(duration_seconds: float) -> float:
    """Groq Whisper large-v3-turbo pricing: $0.04 per hour of audio."""
    return duration_seconds / 3600 * COST_PER_HOUR_USD


async def transcribe(audio_bytes: bytes, filename: str = "audio.webm") -> TranscriptResult:
    """Transcribe audio bytes using Groq's Whisper endpoint.

    Args:
        audio_bytes: Raw audio data to transcribe.
        filename: Filename to report for the uploaded audio.

    Returns:
        A ``TranscriptResult`` with the transcript, duration, and estimated cost.

    Raises:
        GroqTranscriptionError: If ``GROQ_API_KEY`` is not configured.
        httpx.HTTPStatusError: If Groq returns a non-2xx response.
        httpx.HTTPError: For network-level failures.
    """
    api_key = config.groq_api_key()
    if not api_key:
        raise GroqTranscriptionError("GROQ_API_KEY is not configured")

    files = {
        "file": (filename, io.BytesIO(audio_bytes)),
    }
    data = {
        "model": WHISPER_MODEL,
        "response_format": "verbose_json",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(
            GROQ_TRANSCRIPTIONS_URL,
            headers=headers,
            files=files,
            data=data,
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()

    text = payload.get("text", "")
    duration_seconds = float(payload.get("duration", 0.0))
    cost_usd = _compute_cost(duration_seconds)

    logger.info(
        "groq whisper transcription: %.2fs audio, $%.4f",
        duration_seconds,
        cost_usd,
    )

    return TranscriptResult(
        text=text,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
    )
