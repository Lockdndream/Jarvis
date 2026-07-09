"""Protocol parser for Jarvis structured worker messages."""
import json
import logging

logger = logging.getLogger("jarvis")

QUESTION_PREFIX = "JARVIS_QUESTION:"


def parse_line(line: str) -> dict | None:
    """Parse a single line of worker output.

    Returns a dict if the line is a recognised protocol message, None otherwise.

    Supported message types:
      question  ->  {"type":"question","question_id":"...","question":"...",
                     "options":[...],"context":"..."}

    Malformed lines are logged and return None (safe to ignore).
    """
    if not line or not line.startswith(QUESTION_PREFIX):
        return None

    json_str = line[len(QUESTION_PREFIX):]
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Malformed JARVIS_QUESTION line: %.100s", line)
        return None

    if not isinstance(data, dict):
        return None

    question_id = data.get("question_id")
    question = data.get("question")
    if not question_id or not question:
        logger.warning("JARVIS_QUESTION missing required fields: %.100s", line)
        return None

    return {
        "type": "question",
        "question_id": str(question_id),
        "question": str(question),
        "options": data.get("options", []),
        "context": str(data.get("context", "")),
    }
