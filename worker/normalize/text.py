"""Free-text fields reduced to values the matcher can use.

Three-state answers are kept three-state. "Not stated" and "no" filter
differently, and collapsing them turns a missing field into a false negative.
"""

from __future__ import annotations

import re

_YES = ("yes", "allowed", "accepted", "considered", "welcome", "ok", "permitted", "included")
_NO = ("no", "not allowed", "not accepted", "none", "excluded", "not included", "without")

_STUDIO = ("studio", "bedsit")
_ROOM = ("room in", "shared house", "shared flat", "houseshare", "flatshare", "room to rent")

_FURNISHED = {
    "part": ("part furnished", "partly furnished", "partially furnished", "optional"),
    "unfurnished": ("unfurnished", "not furnished"),
    "furnished": ("furnished",),
}


def tri_state(text: str | None) -> bool | None:
    """True, False, or None for "the listing did not say"."""
    if text is None:
        return None
    low = str(text).strip().lower()
    if not low:
        return None
    # Negations are checked first: "not included" contains "included".
    for token in _NO:
        if re.search(rf"\b{re.escape(token)}\b", low):
            return False
    for token in _YES:
        if re.search(rf"\b{re.escape(token)}\b", low):
            return True
    return None


def bedrooms(text: str | None) -> int | None:
    """A studio is zero bedrooms, not a missing value."""
    if text is None:
        return None
    low = str(text).strip().lower()
    if not low:
        return None
    if any(token in low for token in _STUDIO):
        return 0
    match = re.search(r"(\d+)", low)
    if match:
        return int(match.group(1))
    # A room in a shared property is one habitable room.
    return 1 if any(token in low for token in _ROOM) else None


def furnished(text: str | None) -> str:
    if not text:
        return "unknown"
    low = str(text).strip().lower()
    for value, tokens in _FURNISHED.items():
        if any(token in low for token in tokens):
            return value
    return "unknown"


def property_type(text: str | None) -> str | None:
    if not text:
        return None
    low = str(text).strip().lower()
    if any(token in low for token in _ROOM):
        return "room"
    for candidate in ("maisonette", "studio", "apartment", "flat", "bungalow",
                      "terrace", "detached", "semi", "house"):
        if candidate in low:
            return "flat" if candidate == "apartment" else candidate
    return None


def min_tenancy_months(text: str | None) -> int | None:
    if not text:
        return None
    low = str(text).lower()
    if (match := re.search(r"(\d+)\s*(?:month|mth|mo)", low)):
        return int(match.group(1))
    if (match := re.search(r"(\d+)\s*year", low)):
        return int(match.group(1)) * 12
    return None


def clean(text: str | None, *, limit: int | None = None) -> str | None:
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", str(text)).strip()
    if not collapsed:
        return None
    return collapsed[:limit] if limit else collapsed
