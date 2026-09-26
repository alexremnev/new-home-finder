"""Floor area, from whatever the source happened to write.

Kept in square feet throughout — one unit, chosen because the filter is stated
in feet and because storing two units invites a row where they disagree. Square
metres are converted on the way in and shown alongside on the way out.

The sources do not agree on units: OpenRent's pages say "105 sq m", and the
Telegram feed's Size field has been seen empty, in feet and in metres. So the
unit is read from the text rather than assumed, and text with no unit at all is
refused — a bare "65" is 65 square feet or 65 square metres depending on who
wrote it, and guessing would be a filter quietly matching the wrong homes.
"""

from __future__ import annotations

import re

# A number, then a unit. The number may carry thousands separators and a
# decimal part; both appear in the wild.
AREA = re.compile(
    r"""(?P<size>\d[\d,\s]*(?:\.\d+)?)
        \s*
        (?P<unit>
            sq\.?\s*(?:ft|feet|foot) | sqft | ft²  | ft2  | square\s+feet
          | sq\.?\s*m(?:etres?|eters?)? | sqm | m²   | m2   | square\s+met(?:re|er)s?
        )""",
    re.IGNORECASE | re.VERBOSE,
)

SQFT_PER_SQM = 10.7639

# Anything outside this is not a London rental's floor area, it is a typo or a
# plot of land. Refused rather than stored, because a wrong number here silently
# removes homes from somebody's alerts.
SMALLEST_SQFT = 50
LARGEST_SQFT = 20_000


def sqft_from(text: str | None) -> int | None:
    """Square feet, or None when the text does not state an area we believe."""

    found = AREA.search(text or "")
    if not found:
        return None

    try:
        size = float(found.group("size").replace(",", "").replace(" ", ""))
    except ValueError:
        return None
    if size <= 0:
        return None

    unit = found.group("unit").lower().replace(".", "").replace(" ", "")
    metric = unit.startswith("m") or unit.startswith("sqm") or unit.startswith("squaremet")
    sqft = size * SQFT_PER_SQM if metric else size

    rounded = int(round(sqft))
    return rounded if SMALLEST_SQFT <= rounded <= LARGEST_SQFT else None


def sqm_from_sqft(sqft: int) -> int:
    """The same area in square metres, for showing beside the feet."""

    return int(round(sqft / SQFT_PER_SQM))


__all__ = ["LARGEST_SQFT", "SMALLEST_SQFT", "sqft_from", "sqm_from_sqft"]
