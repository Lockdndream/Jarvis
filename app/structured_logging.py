"""Structured JSONL logging for the Python backend. ADR-021.

Provides JsonlFormatter (JSON Lines output), JarvisContextFilter (injects
trace_id from app.trace.current_trace_id()), component taxonomy mapping,
and configure_structured_logging() which replaces the bare
logging.basicConfig() call in app/main.py.
"""
import asyncio
import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone

# Standard LogRecord attribute names — anything in record.__dict__ beyond
# these (and not one of trace_id/conversation_id/task_id) is an extra
# attribute passed via extra= and goes into the `fields` dict.
_BUILTIN_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message",
    # Python 3.12+ adds this LogRecord attribute (the asyncio task name at
    # emit time) automatically -- omitting it here leaked it into every
    # record's `fields` dict as an unrequested extra (found via independent
    # review: the Builder's list was written from older LogRecord docs and
    # missed it; confirmed against this environment's real Python 3.14
    # LogRecord.__dict__, not assumed from documentation alone).
    "taskName",
    # M-OX.3 live-validation finding: configure_structured_logging() attaches
    # a second handler (the console StreamHandler, format string includes
    # %(asctime)s) to the SAME root logger as this file handler. Both
    # handlers process the SAME LogRecord object -- Formatter.format() sets
    # record.asctime as a side effect when its format string uses it, so by
    # the time this handler's JsonlFormatter runs, `asctime` is already on
    # the record and would otherwise leak into `fields` on every line. Never
    # observed by any unit test, because no test attaches two handlers with
    # different formatters to one shared logger the way real startup does.
    "asctime",
})

# ADR-021 component taxonomy: logger-name prefix -> component string.
# Keys are matched by longest prefix.
_COMPONENT_TAXONOMY: dict[str, str] = {
    "app.main": "backend.server",
    "app.connection_manager": "backend.websocket",
    "app.supervisor": "backend.supervisor",
    "app.voice_session_manager": "backend.voice_session",
    "app.integrations.opencode_server": "backend.opencode",
    "app.integrations.opencode_adapter": "backend.opencode",
    "app.integrations.opencode_supervisor": "backend.opencode",
    "app.integrations.opencode_events": "backend.opencode",
    "app.attention_manager": "backend.attention",
    "app.attention_scheduler": "backend.attention",
    "app.notifications": "backend.notifications",
    "app.operations": "backend.operations",
}


def _component_for(logger_name: str) -> str:
    best = None
    best_len = -1
    for key, value in _COMPONENT_TAXONOMY.items():
        if logger_name == key or logger_name.startswith(key + "."):
            if len(key) > best_len:
                best = value
                best_len = len(key)
    if best is not None:
        return best
    return logger_name


# Module-level state set by configure_structured_logging() for
# get_logging_status() to read back without walking handlers at every call.
_log_config: dict[str, str | bool | int] = {
    "enabled": False,
    "level": "NOTSET",
    "file_path": "",
    "retention_days": 14,
}


class JsonlFormatter(logging.Formatter):
    """Renders each LogRecord as one JSONL line per ADR-021's canonical schema."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        severity = record.levelname
        component = _component_for(record.name)
        message = record.getMessage()

        trace_id = getattr(record, "trace_id", None)
        conversation_id = getattr(record, "conversation_id", None)
        task_id = getattr(record, "task_id", None)

        try:
            task = asyncio.current_task()
            runtime_task = task.get_name() if task is not None else record.threadName
        except RuntimeError:
            runtime_task = record.threadName

        fields = {}
        for key, value in record.__dict__.items():
            if key in _BUILTIN_RECORD_ATTRS:
                continue
            if key in ("trace_id", "conversation_id", "task_id"):
                continue
            fields[key] = value

        obj = {
            "timestamp": timestamp,
            "severity": severity,
            "component": component,
            "message": message,
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "runtime_task": runtime_task,
            "fields": fields,
        }
        return json.dumps(obj, default=str)


class JarvisContextFilter(logging.Filter):
    """Injects trace_id from app.trace.current_trace_id() onto every record.

    This is a context-injection filter, not a filtering one — it always
    returns True so no record is ever dropped.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from app import trace
        # M-OX.3 live-validation finding: an explicit extra={"trace_id": ...}
        # at the call site -- e.g. an SSE handler recovering it from the
        # opencode_tasks row per ADR-020's own async-completion design,
        # since that handler runs on a different asyncio Task with no
        # ambient binding -- must take precedence over the ContextVar.
        # Unconditionally overwriting it here (the original behavior)
        # silently discarded exactly the case ADR-020 built the row-level
        # persistence for, live-confirmed: a real OpenCode task's terminal
        # "completed"/"failed" log line always carried trace_id: null.
        if getattr(record, "trace_id", None) is None:
            record.trace_id = trace.current_trace_id()
        return True


def configure_structured_logging() -> None:
    log_dir = os.environ.get("JARVIS_LOG_DIR", "logs")
    log_level = os.environ.get("JARVIS_LOG_LEVEL", "INFO")
    retention_days = int(os.environ.get("JARVIS_LOG_RETENTION_DAYS", "14"))

    os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger()
    root.handlers.clear()

    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    root.addHandler(console_handler)

    file_path = os.path.join(log_dir, "jarvis.jsonl")
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=file_path,
        when="midnight",
        backupCount=retention_days,
        utc=True,
    )
    file_handler.setFormatter(JsonlFormatter())
    file_handler.addFilter(JarvisContextFilter())
    root.addHandler(file_handler)

    _log_config["enabled"] = True
    _log_config["level"] = logging.getLevelName(root.level)
    _log_config["file_path"] = file_path
    _log_config["retention_days"] = retention_days


def get_logging_status() -> dict:
    file_path = _log_config["file_path"]
    try:
        assert isinstance(file_path, str)  # guaranteed: _log_config["file_path"] is always a string
        file_size_bytes = os.path.getsize(file_path)
    except FileNotFoundError:
        file_size_bytes = 0
    return {
        "enabled": _log_config["enabled"],
        "level": _log_config["level"],
        "file_path": file_path,
        "file_size_bytes": file_size_bytes,
        "retention_days": _log_config["retention_days"],
    }
