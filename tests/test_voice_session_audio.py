"""Laptop-side WebSocket audio-frame handling with Groq Whisper STT wired in.

Tests for the binary-frame transcription path, the text-frame fallback path
(via the shared ``_process_transcript`` helper), and the bare-frame-warning
guard.
"""
import logging
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.stt import TranscriptResult, GroqTranscriptionError

_ENV_BEFORE_IMPORT = dict(os.environ)
import app.main as _main_module
for _leaked_var in set(os.environ) - set(_ENV_BEFORE_IMPORT):
    if _leaked_var.startswith("JARVIS_LLM") or _leaked_var.startswith("OPENCODE_"):
        del os.environ[_leaked_var]


@pytest.fixture
def client(monkeypatch):
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    main_module = _main_module

    async def noop_start(self):
        pass

    async def noop_stop(self):
        pass

    monkeypatch.setattr(main_module.opencode_supervisor, "start", noop_start.__get__(main_module.opencode_supervisor))
    monkeypatch.setattr(main_module.opencode_supervisor, "stop", noop_stop.__get__(main_module.opencode_supervisor))

    async def fake_get_status():
        return {"server_alive": False, "server_url": "", "running_tasks": [], "pending_questions": []}
    monkeypatch.setattr(main_module.opencode_supervisor, "get_status", fake_get_status)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        yield c

    if os.path.exists(path):
        os.unlink(path)


def _drain_initial(ws):
    seen_types = set()
    for _ in range(6):
        try:
            msg = ws.receive_json()
        except Exception:
            break
        seen_types.add(msg.get("type"))
        if "opencode_status" in seen_types:
            break
    return seen_types


def test_binary_frame_with_stt_transcribes_and_feeds_pipeline(client, monkeypatch):
    """Header + binary frame → stt.transcribe returns text → _process_transcript called, voice_session_response sent."""
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({"type": "voice_session_open", "conversation_id": None, "attention_request_id": None})
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        vsid = opened["voice_session_id"]

        async def fake_handle_transcript(voice_session_id, transcript):
            assert transcript == "hello world"
            return {
                "response": "stt response",
                "conversation_id": None,
                "attention_request_id": None,
                "voice_session_state": "listening",
                "trace_id": "trace-stt-1",
            }

        monkeypatch.setattr(
            _main_module.voice_session_manager,
            "handle_transcript",
            fake_handle_transcript,
        )

        async def fake_transcribe(audio_bytes, filename="audio.webm"):
            return TranscriptResult(
                text="hello world",
                duration_seconds=1.5,
                cost_usd=0.00002,
            )

        monkeypatch.setattr(_main_module.stt, "transcribe", fake_transcribe)

        audio = b"RIFF" + b"\x00" * 100
        ws.send_json({"type": "voice_session_audio", "voice_session_id": vsid})
        ws.send_bytes(audio)

        reply = ws.receive_json()
        assert reply["type"] == "voice_session_response"
        assert reply["voice_session_id"] == vsid
        assert reply["response"] == "stt response"


def test_binary_frame_stt_transcription_error_sends_error(client, monkeypatch):
    """stt.transcribe raises GroqTranscriptionError → voice_session_error sent, handle_transcript NOT called."""
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({"type": "voice_session_open", "conversation_id": None, "attention_request_id": None})
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        vsid = opened["voice_session_id"]

        handle_called = False

        async def fake_handle_transcript(voice_session_id, transcript):
            nonlocal handle_called
            handle_called = True
            return {}

        monkeypatch.setattr(
            _main_module.voice_session_manager,
            "handle_transcript",
            fake_handle_transcript,
        )

        async def fake_transcribe_raise(audio_bytes, filename="audio.webm"):
            raise GroqTranscriptionError("no api key")

        monkeypatch.setattr(_main_module.stt, "transcribe", fake_transcribe_raise)

        audio = b"RIFF" + b"\x00" * 100
        ws.send_json({"type": "voice_session_audio", "voice_session_id": vsid})
        ws.send_bytes(audio)

        reply = ws.receive_json()
        assert reply["type"] == "voice_session_error"
        assert reply["voice_session_id"] == vsid
        assert "Transcription failed" in reply["error"]
        assert not handle_called


def test_binary_frame_stt_empty_text_sends_no_speech_error(client, monkeypatch):
    """stt.transcribe returns whitespace-only text → voice_session_error 'No speech detected', handle_transcript NOT called."""
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({"type": "voice_session_open", "conversation_id": None, "attention_request_id": None})
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        vsid = opened["voice_session_id"]

        handle_called = False

        async def fake_handle_transcript(voice_session_id, transcript):
            nonlocal handle_called
            handle_called = True
            return {}

        monkeypatch.setattr(
            _main_module.voice_session_manager,
            "handle_transcript",
            fake_handle_transcript,
        )

        async def fake_transcribe_empty(audio_bytes, filename="audio.webm"):
            return TranscriptResult(text="  \n ", duration_seconds=0.0, cost_usd=0.0)

        monkeypatch.setattr(_main_module.stt, "transcribe", fake_transcribe_empty)

        audio = b"RIFF" + b"\x00" * 100
        ws.send_json({"type": "voice_session_audio", "voice_session_id": vsid})
        ws.send_bytes(audio)

        reply = ws.receive_json()
        assert reply["type"] == "voice_session_error"
        assert reply["voice_session_id"] == vsid
        assert "No speech detected" in reply["error"]
        assert not handle_called


def test_bare_binary_frame_without_header_logs_warning(client, caplog):
    """A binary frame without a preceding voice_session_audio header is dropped."""
    audio = b"\x01\x02\x03\x04"
    with caplog.at_level(logging.WARNING, logger="jarvis"):
        with client.websocket_connect("/ws") as ws:
            _drain_initial(ws)
            ws.send_bytes(audio)

    matches = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "received audio frame with no pending voice_session_audio header" in r.message
    ]
    assert len(matches) == 1


def test_voice_session_transcript_round_trip_unchanged_after_loop_restructure(client, monkeypatch):
    """The existing text-frame receive loop still handles voice_session_transcript."""
    with client.websocket_connect("/ws") as ws:
        _drain_initial(ws)

        ws.send_json({"type": "voice_session_open", "conversation_id": None, "attention_request_id": None})
        opened = ws.receive_json()
        assert opened["type"] == "voice_session_opened"
        vsid = opened["voice_session_id"]

        async def fake_handle_transcript(voice_session_id, transcript):
            return {
                "response": "fake response",
                "conversation_id": None,
                "attention_request_id": None,
                "voice_session_state": "listening",
                "trace_id": "trace-123",
            }

        monkeypatch.setattr(
            _main_module.voice_session_manager,
            "handle_transcript",
            fake_handle_transcript,
        )

        ws.send_json({"type": "voice_session_transcript", "voice_session_id": vsid, "transcript": "hello"})
        reply = ws.receive_json()
        assert reply["type"] == "voice_session_response"
        assert reply["voice_session_id"] == vsid
        assert reply["response"] == "fake response"
