"""Generic on-disk ownership-proof marker, shared by every subsystem that
spawns a subprocess it must later prove it owns before killing (OpenCode's
`opencode serve`, and JOPS v1.0's Jarvis self-management). Extracted from
app/integrations/opencode_server.py (ADR-023) rather than duplicated.

The marker records the PID actually resolved as LISTENING on the target
port after spawn (never the launcher PID an OS-level subprocess call
returns, which can be unreliable -- see process_utils.py), so a later
process (or a restarted supervisor) can reconcile "is this still mine"
against what is actually listening now.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)


def read_owner_marker(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_owner_marker(path: str, pid: int, port: int) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "port": port}, f)
    except OSError:
        logger.warning("Could not write owner marker at %s", path)


def clear_owner_marker(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
