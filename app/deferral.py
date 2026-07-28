"""Deterministic deferral/snooze phrase parsing (Milestone 8 Phase 7).

Never invents a random time for a vague phrase ("later", "not now") unless
a default snooze duration is explicitly configured (JARVIS_DEFAULT_SNOOZE_MINUTES)
— prefers asking for clarification. Server-local timezone is used
explicitly for daypart targets (morning/afternoon/evening), never silently
UTC (datetime.astimezone() with no argument converts to the machine's real
local timezone).
"""
import re
from datetime import datetime, timedelta, timezone

from app import config

DAYPART_HOURS = {"morning": 8, "afternoon": 15, "evening": 19}

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45,
    "forty five": 45, "sixty": 60,
}
# Longest-first so "forty five" matches before "five" alone would.
_NUMBER_WORD_ALTERNATION = "|".join(sorted(_WORD_NUMBERS.keys(), key=len, reverse=True))

_DURATION_RE = re.compile(
    rf"\b(\d+|{_NUMBER_WORD_ALTERNATION})\s*(minute|minutes|min|mins|hour|hours|hr|hrs)\b"
)
_AN_HOUR_RE = re.compile(r"\b(?:an|a)\s+hour\b")
_TOMORROW_DAYPART_RE = re.compile(r"\btomorrow\s+(morning|afternoon|evening)\b")
_BARE_TOMORROW_RE = re.compile(r"\btomorrow\b")
_DAYPART_ONLY_RE = re.compile(r"\b(morning|afternoon|evening)\b")
_VAGUE_RE = re.compile(r"^(later|not now|not right now|some other time|maybe later)$")


class DeferralResult:
    """kind is one of:
      "resolved" — deferred_until is a concrete ISO-8601 UTC timestamp
      "vague"    — message holds the clarification question to ask
      "none"     — the text wasn't a deferral phrase at all
    """

    def __init__(self, kind: str, deferred_until: str | None = None, message: str | None = None):
        self.kind = kind
        self.deferred_until = deferred_until
        self.message = message


def _default_snooze_minutes() -> int | None:
    return config.default_snooze_minutes()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _next_local_daypart(now: datetime, hour: int, days_ahead: int) -> datetime:
    local_now = now.astimezone()
    return local_now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)


def parse_defer_phrase(text: str, now: datetime | None = None) -> DeferralResult:
    now = now or datetime.now(timezone.utc)
    cleaned = (text or "").strip().lower().rstrip(".!?")
    if not cleaned:
        return DeferralResult("none")

    m = _DURATION_RE.search(cleaned)
    if m:
        raw_amount, unit = m.group(1), m.group(2)
        amount = int(raw_amount) if raw_amount.isdigit() else _WORD_NUMBERS.get(raw_amount)
        if amount:
            minutes = amount if unit.startswith("min") else amount * 60
            return DeferralResult("resolved", deferred_until=_iso(now + timedelta(minutes=minutes)))

    if _AN_HOUR_RE.search(cleaned):
        return DeferralResult("resolved", deferred_until=_iso(now + timedelta(hours=1)))

    m = _TOMORROW_DAYPART_RE.search(cleaned)
    if m:
        target = _next_local_daypart(now, DAYPART_HOURS[m.group(1)], days_ahead=1)
        return DeferralResult("resolved", deferred_until=_iso(target))

    if _BARE_TOMORROW_RE.search(cleaned):
        # Bare "tomorrow", no daypart specified — project convention: morning.
        target = _next_local_daypart(now, DAYPART_HOURS["morning"], days_ahead=1)
        return DeferralResult("resolved", deferred_until=_iso(target))

    m = _DAYPART_ONLY_RE.search(cleaned)
    if m:
        hour = DAYPART_HOURS[m.group(1)]
        target = _next_local_daypart(now, hour, days_ahead=0)
        if target <= now.astimezone():
            target = _next_local_daypart(now, hour, days_ahead=1)
        return DeferralResult("resolved", deferred_until=_iso(target))

    if _VAGUE_RE.match(cleaned):
        default_minutes = _default_snooze_minutes()
        if default_minutes:
            return DeferralResult("resolved", deferred_until=_iso(now + timedelta(minutes=default_minutes)))
        return DeferralResult("vague", message="When should I come back?")

    return DeferralResult("none")
