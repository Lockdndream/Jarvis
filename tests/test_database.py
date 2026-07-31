"""Tests for app/database.py query helpers."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db


@pytest.fixture(autouse=True)
def test_db():
    old_path = db.DB_PATH
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = path
    db.init_db()
    yield
    db.DB_PATH = old_path
    if os.path.exists(path):
        os.unlink(path)


def test_get_previous_conversation_boundary_empty_db():
    assert db.get_previous_conversation_boundary("conv_anything") is None
    assert db.get_previous_conversation_boundary(None) is None


def test_get_previous_conversation_boundary_excludes_current_conversation():
    conv_a = "conv_a1234567890"
    conv_b = "conv_b1234567890"
    db.save_conversation_message(conv_a, "user", "hello", trace_id=None)
    db.save_conversation_message(conv_b, "user", "hi", trace_id=None)
    # Ensure a small delay so timestamps are distinct (not strictly required for SQLite).
    import time
    time.sleep(0.01)
    db.save_conversation_message(conv_a, "assistant", "world", trace_id=None)

    boundary = db.get_previous_conversation_boundary(conv_a)
    assert boundary is not None
    # The boundary should be the timestamp of conv_b's only message, not conv_a's latest.
    conv_b_messages = db.get_conversation_messages(conv_b)
    assert len(conv_b_messages) == 1
    assert boundary == conv_b_messages[0]["created_at"]


def test_get_previous_conversation_boundary_global_max_when_none_supplied():
    conv_a = "conv_a1234567890"
    conv_b = "conv_b1234567890"
    db.save_conversation_message(conv_a, "user", "hello", trace_id=None)
    db.save_conversation_message(conv_b, "user", "hi", trace_id=None)

    boundary = db.get_previous_conversation_boundary(None)
    conv_b_messages = db.get_conversation_messages(conv_b)
    assert boundary == conv_b_messages[0]["created_at"]
