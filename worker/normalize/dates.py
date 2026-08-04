"""Availability dates.

Portals write availability as "Now", "Today", "12 Sep", "12 September 2026", or
an ISO date. A bare day and month has no year, and choosing the wrong one moves a
listing a year out of range, so the year is resolved against a reference date and
the result is always within a sane window.

All dates are handled as plain calendar dates. Storage is UTC and display is
Europe/London, so a date never carries a timezone of its own.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_IMMEDIATE = ("now", "today", "immediately", "immediate", "asap", "available now")
_TOMORROW = ("tomorrow",)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY = re.compile(r"\b(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{2,4})\b")
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?\s*(\d{4})?\b", re.IGNORECASE
)
_MONTH_DAY = re.compile(
    r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\b", re.IGNORECASE
)

# A date outside this window relative to the reference is treated as a parse
# failure rather than accepted, since it is almost always a misread field.
PAST_LIMIT = timedelta(days=365)
FUTURE_LIMIT = timedelta(days=730)


def parse_available_from(text: str | None, *, today: date | None = None) -> date | None:
    """Best-effort availability date. None means "not stated or unparseable"."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    reference = today or datetime.now().date()
    low = raw.lower()

    if any(token in low for token in _IMMEDIATE):
        return reference
    if any(token in low for token in _TOMORROW):
        return reference + timedelta(days=1)

    parsed = (
        _from_iso(raw)
        or _from_dmy(raw)
        or _from_day_month(raw, reference)
        or _from_month_day(raw, reference)
    )
    return parsed if parsed and _plausible(parsed, reference) else None


def _plausible(value: date, reference: date) -> bool:
    return reference - PAST_LIMIT <= value <= reference + FUTURE_LIMIT


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _from_iso(raw: str) -> date | None:
    match = _ISO.search(raw)
    return _safe(*(int(g) for g in match.groups())) if match else None


def _from_dmy(raw: str) -> date | None:
    """Day-first, which is the UK convention on every source in use."""
    match = _DMY.search(raw)
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    return _safe(year, month, day)


def _resolve_year(month: int, day: int, reference: date, year: str | None) -> date | None:
    if year:
        return _safe(int(year), month, day)
    # No year given: take the next occurrence, allowing a short grace period so
    # a listing that became available a few days ago is not pushed a year out.
    candidate = _safe(reference.year, month, day)
    if candidate is None:
        return None
    if candidate < reference - timedelta(days=31):
        candidate = _safe(reference.year + 1, month, day)
    return candidate


def _from_day_month(raw: str, reference: date) -> date | None:
    for match in _DAY_MONTH.finditer(raw):
        day_s, name, year = match.groups()
        month = _MONTHS.get(name[:4].lower()) or _MONTHS.get(name[:3].lower())
        if month:
            return _resolve_year(month, int(day_s), reference, year)
    return None


def _from_month_day(raw: str, reference: date) -> date | None:
    for match in _MONTH_DAY.finditer(raw):
        name, day_s, year = match.groups()
        month = _MONTHS.get(name[:4].lower()) or _MONTHS.get(name[:3].lower())
        if month:
            return _resolve_year(month, int(day_s), reference, year)
    return None
