"""ADR-021 (Owner Experience M-OX.2): structured logging.

Covers: component taxonomy mapping, JsonlFormatter output schema,
JarvisContextFilter trace_id injection, configure_structured_logging
idempotency, real file output, and get_logging_status().
"""
import io
import json
import logging
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.structured_logging import (
    JsonlFormatter,
    JarvisContextFilter,
    _component_for,
    configure_structured_logging,
    get_logging_status,
)
from app import trace


@pytest.fixture(autouse=True)
def test_db():
    old_path = None
    try:
        import app.database as db
        old_path = db.DB_PATH
        f, path = tempfile.mkstemp(suffix=".db")
        os.close(f)
        db.DB_PATH = path
        db.init_db()
        yield
    finally:
        if old_path is not None:
            db.DB_PATH = old_path
        if path and os.path.exists(path):
            os.unlink(path)


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """configure_structured_logging() mutates the process-global root
    logger (clears its handlers, attaches new ones bound to whatever
    JARVIS_LOG_DIR/sys.stderr happened to be at call time). pytest runs
    every test file in one process, so without this, a later test file's
    ordinary logger.info() calls would silently write into a handler left
    pointing at this file's already-deleted tmp_path, or at a StringIO
    from a monkeypatch that has since reverted -- a real, if quiet,
    regression (found via independent review, not asserted by any test
    here, since it would never surface as a failure in *this* file)."""
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    yield
    root.handlers.clear()
    for h in handlers_before:
        root.addHandler(h)
    root.setLevel(level_before)


# ── _component_for ──────────────────────────────────────────────────


def test_component_for_supervisor():
    assert _component_for("app.supervisor.supervisor") == "backend.supervisor"


def test_component_for_opencode_supervisor():
    assert _component_for("app.integrations.opencode_supervisor") == "backend.opencode"


def test_component_for_unmapped_returns_raw_name():
    assert _component_for("some.unmapped.module") == "some.unmapped.module"


def test_component_for_submodule_under_supervisor():
    assert _component_for("app.supervisor.tools") == "backend.supervisor"


def test_component_for_exact_main():
    assert _component_for("app.main") == "backend.server"


def test_component_for_connection_manager():
    assert _component_for("app.connection_manager") == "backend.websocket"


def test_component_for_attention_manager():
    assert _component_for("app.attention_manager") == "backend.attention"


def test_component_for_notifications():
    assert _component_for("app.notifications") == "backend.notifications"


# ── JsonlFormatter: plain record ────────────────────────────────────


def test_jsonl_formatter_produces_valid_json_with_all_keys():
    record = logging.LogRecord(
        name="app.supervisor.supervisor",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="test message",
        args=(),
        exc_info=None,
    )
    fmt = JsonlFormatter()
    line = fmt.format(record)
    obj = json.loads(line)

    assert "timestamp" in obj
    assert "severity" in obj
    assert "component" in obj
    assert "message" in obj
    assert "trace_id" in obj
    assert "conversation_id" in obj
    assert "task_id" in obj
    assert "runtime_task" in obj
    assert "fields" in obj

    assert obj["severity"] == "INFO"
    assert obj["message"] == "test message"
    assert obj["component"] == "backend.supervisor"
    assert obj["trace_id"] is None
    assert obj["conversation_id"] is None
    assert obj["task_id"] is None
    assert obj["fields"] == {}


def test_jsonl_formatter_timestamp_from_record_created():
    from datetime import datetime, timezone

    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="ts check",
        args=(),
        exc_info=None,
    )
    fmt = JsonlFormatter()
    obj = json.loads(fmt.format(record))
    parsed = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00"))
    expected = datetime.fromtimestamp(record.created, tz=timezone.utc)
    assert abs((parsed - expected).total_seconds()) < 0.01


# ── JsonlFormatter: extra attributes ────────────────────────────────


def test_jsonl_formatter_with_extra_fields(tmp_path):
    logger = logging.getLogger("app.test_extra")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonlFormatter())
    logger.addHandler(handler)

    logger.info("hello %s", "world", extra={"conversation_id": "conv_x", "task_id": "t1", "custom_field": 42})
    logger.handlers.clear()

    line = buf.getvalue().strip()
    obj = json.loads(line)

    assert obj["conversation_id"] == "conv_x"
    assert obj["task_id"] == "t1"
    assert obj["fields"] == {"custom_field": 42}


def test_jsonl_formatter_session_id_goes_into_fields(tmp_path):
    logger = logging.getLogger("app.test_session")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonlFormatter())
    logger.addHandler(handler)

    logger.info("test", extra={"session_id": "ses_abc"})
    logger.handlers.clear()

    obj = json.loads(buf.getvalue().strip())
    assert obj["fields"] == {"session_id": "ses_abc"}


# ── JarvisContextFilter ─────────────────────────────────────────────


def test_context_filter_injects_trace_id():
    token = trace.bind_trace_id("trace_test123")
    try:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="filter test",
            args=(),
            exc_info=None,
        )
        f = JarvisContextFilter()
        result = f.filter(record)
        assert result is True
        assert record.trace_id == "trace_test123"
    finally:
        trace.reset_trace_id(token)


def test_context_filter_with_no_trace_bind():
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="no trace",
        args=(),
        exc_info=None,
    )
    f = JarvisContextFilter()
    result = f.filter(record)
    assert result is True
    assert record.trace_id is None


def test_context_filter_respects_explicit_trace_id_over_ambient_none():
    """M-OX.3 live-validation finding: an SSE handler running on a
    different asyncio Task than the one that started the work has no
    ambient trace_id, but can recover the real one from the
    opencode_tasks row and pass it via extra={"trace_id": ...}. The
    filter must not stomp that explicit value back to None just because
    no turn is currently bound -- this is exactly the bug that made every
    real OpenCode task's terminal-state log line carry trace_id: null."""
    assert trace.current_trace_id() is None  # confirm no ambient binding
    record = logging.LogRecord(
        name="app.integrations.opencode_supervisor",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="terminal state",
        args=(),
        exc_info=None,
    )
    record.trace_id = "trace_recovered_from_row"
    f = JarvisContextFilter()
    f.filter(record)
    assert record.trace_id == "trace_recovered_from_row"


def test_context_filter_fills_in_ambient_when_explicit_is_none():
    """The common case still works: no explicit override -> ambient
    ContextVar value (or None) is used, same as before this fix."""
    token = trace.bind_trace_id("trace_ambient_value")
    try:
        record = logging.LogRecord(
            name="app.test", level=logging.INFO, pathname="", lineno=0,
            msg="x", args=(), exc_info=None,
        )
        record.trace_id = None  # explicit extra={"trace_id": None}, or none set at all
        f = JarvisContextFilter()
        f.filter(record)
        assert record.trace_id == "trace_ambient_value"
    finally:
        trace.reset_trace_id(token)


def test_context_filter_integration_with_formatter_and_trace(tmp_path):
    token = trace.bind_trace_id("trace_integration")
    try:
        logger = logging.getLogger("app.test_integration")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JsonlFormatter())
        handler.addFilter(JarvisContextFilter())
        logger.addHandler(handler)

        logger.info("integration test")
        logger.handlers.clear()

        obj = json.loads(buf.getvalue().strip())
        assert obj["trace_id"] == "trace_integration"
    finally:
        trace.reset_trace_id(token)


def test_asctime_does_not_leak_into_fields_when_two_handlers_share_a_logger():
    """M-OX.3 live-validation finding: configure_structured_logging()
    attaches a console handler (format string uses %(asctime)s) and the
    JSONL file handler to the SAME root logger. logging.Formatter.format()
    sets record.asctime as a side effect on the shared record object when
    its format string requests it -- so whichever handler runs second
    would otherwise see `asctime` already present and leak it into
    `fields` on every single line. No prior test caught this because none
    attached two handlers with different formatters to one shared logger,
    which is exactly what real startup does."""
    logger = logging.getLogger("app.test_two_handlers")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console_buf = io.StringIO()
    console_handler = logging.StreamHandler(console_buf)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(console_handler)

    jsonl_buf = io.StringIO()
    jsonl_handler = logging.StreamHandler(jsonl_buf)
    jsonl_handler.setFormatter(JsonlFormatter())
    logger.addHandler(jsonl_handler)

    logger.info("shared record test")
    logger.handlers.clear()

    obj = json.loads(jsonl_buf.getvalue().strip())
    assert "asctime" not in obj["fields"], f"asctime leaked into fields: {obj['fields']}"


def test_taskname_does_not_leak_into_fields():
    """Python 3.12+ sets LogRecord.taskName automatically for every
    record emitted inside an asyncio Task -- confirmed against this
    environment's real LogRecord.__dict__, not assumed from documentation."""
    async def _log_inside_task():
        logger = logging.getLogger("app.test_taskname")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JsonlFormatter())
        logger.addHandler(handler)
        logger.info("inside a real asyncio task")
        logger.handlers.clear()
        return buf.getvalue().strip()

    import asyncio
    line = asyncio.run(_log_inside_task())
    obj = json.loads(line)
    assert "taskName" not in obj["fields"], f"taskName leaked into fields: {obj['fields']}"


# ── configure_structured_logging ────────────────────────────────────


def test_configure_idempotent():
    configure_structured_logging()
    configure_structured_logging()
    root = logging.getLogger()
    assert len(root.handlers) == 2


def test_configure_writes_jsonl_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))
    configure_structured_logging()

    logger = logging.getLogger("app.test")
    logger.info("hello structured world")

    files = list(tmp_path.iterdir())
    jsonl_files = [f for f in files if f.name == "jarvis.jsonl"]
    assert len(jsonl_files) == 1

    content = jsonl_files[0].read_text(encoding="utf-8")
    lines = [line for line in content.strip().split("\n") if line.strip()]
    assert len(lines) >= 1

    obj = json.loads(lines[0])
    assert obj["severity"] == "INFO"
    assert obj["message"] == "hello structured world"
    # "app.test" matches no taxonomy prefix -- per _component_for's own
    # contract ("never a placeholder"), the raw logger name is expected.
    assert obj["component"] == "app.test"


def test_configure_console_format_matches_expected(tmp_path, monkeypatch):
    """The constructed-but-never-attached handler/buf in the original
    version of this test asserted against output that could never have
    reached it -- configure_structured_logging() builds its own
    StreamHandler, not this test's. Redirect the console handler's real
    target (sys.stderr, StreamHandler's default) instead."""
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", buf)

    configure_structured_logging()

    logger = logging.getLogger("app.test")
    logger.info("console check")

    output = buf.getvalue()
    assert "app.test" in output
    assert "INFO" in output
    assert "console check" in output


# ── get_logging_status ──────────────────────────────────────────────


def test_get_logging_status_after_configure(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))
    configure_structured_logging()

    status = get_logging_status()
    assert isinstance(status, dict)
    assert status["enabled"] is True
    assert isinstance(status["level"], str)
    assert len(status["level"]) > 0
    assert isinstance(status["file_path"], str)
    assert isinstance(status["file_size_bytes"], int)
    assert status["file_size_bytes"] >= 0
    assert isinstance(status["retention_days"], int)
    assert status["retention_days"] == 14
