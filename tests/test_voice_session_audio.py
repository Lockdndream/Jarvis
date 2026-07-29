"""Laptop-side WebSocket stub for incoming audio frames (Groq Whisper STT).

This tests ONLY observability: the receive loop now accepts binary frames
when preceded by a ``voice_session_audio`` text header, and logs them. Real
transcription wiring (calling app/stt.py and feeding the result into
VoiceSessionManager.handle_transcript) is a later task, not covered here.
"""
import logging
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db

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


def test_voice_session_audio_header_then_binary_frame_logs_size(client, caplog):
    """A voice_session_audio text header followed by a binary frame is observed."""
    audio = b"RIFF" + b"\x00" * 100
    with caplog.at_level(logging.INFO, logger="jarvis"):
        with client.websocket_connect("/ws") as ws:
            _drain_initial(ws)
            ws.send_json({"type": "voice_session_audio", "voice_session_id": "vs-audio-1"})
            ws.send_bytes(audio)

    matches = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO
        and "received voice session audio: session=vs-audio-1 bytes=104" in r.message
    ]
    assert len(matches) == 1


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

        # Use a real voice session so the session id is valid.
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
