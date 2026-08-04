"""Price normalisation.

Portals mix weekly and monthly prices, and a filter that compares the two is
silently wrong rather than visibly broken. Conversion happens here, once, at
parse time — never in the matcher.

The weekly-to-monthly convention is `pw * 52 / 12`, which is what the portals
themselves use when they show both figures.
"""

from __future__ import annotations

import re
from typing import Literal

Period = Literal["month", "week", "unknown"]

_AMOUNT = re.compile(r"£?\s*([0-9][0-9,\s]*(?:\.[0-9]{1,2})?)")

_WEEKLY = (
    "p/w", "pw", "per week", "a week", "weekly", "per wk", "/week", "/wk", "pppw",
)
_MONTHLY = (
    "p/m", "pcm", "pm", "per month", "a month", "monthly", "per calendar month",
    "/month", "/mo", "ppcm",
)


class PriceError(ValueError):
    pass


def detect_period(text: str) -> Period:
    low = text.lower()
    # Weekly is checked first: "£450 pw (£1,950 pcm)" leads with the weekly
    # figure, and the amount parsed below is the leading one.
    for token in _WEEKLY:
        if token in low:
            return "week"
    for token in _MONTHLY:
        if token in low:
            return "month"
    return "unknown"


def parse_amount(text: str) -> float | None:
    match = _AMOUNT.search(text.replace("\xa0", " "))
    if not match:
        return None
    cleaned = match.group(1).replace(",", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def to_pcm(text: str | None, *, default_period: Period = "month") -> int:
    """Convert a displayed price to whole pounds per calendar month.

    `default_period` applies only when the text carries no period at all. It
    defaults to monthly because that is how every source in use displays prices
    when unqualified; a source that differs must say so explicitly.
    """
    if not text:
        raise PriceError("empty price")
    amount = parse_amount(str(text))
    if amount is None:
        raise PriceError(f"no amount in {text!r}")

    period = detect_period(str(text))
    if period == "unknown":
        period = default_period
    if period == "week":
        amount = amount * 52 / 12
    return int(round(amount))


def deposit_to_pounds(text: str | None) -> float | None:
    """Deposits are quoted as a plain sum, not per period."""
    if not text:
        return None
    return parse_amount(str(text))
