"""Reading a full postcode off a listing's own page.

Both scraped portals publish an incomplete address on a search page, and they
publish it differently:

  * Rightmove states a full postcode in about a third of its search results
    ("Balfron Tower, St Leonards Road, London, E14 0UY") and stops at the
    outward code in the rest.
  * Zoopla states none at all — every address ends at the outward code.

Measured on 30 September 2026. The outward code alone is not enough for two
separate reasons, and the second is the one that bites:

 1. An alert that says only "E14" tells somebody the flat is somewhere in two
    square miles.
 2. `store.mark_duplicate` compares on the full postcode and skips any listing
    without one. So the same flat advertised on both portals could not be
    recognised as one flat, and both copies were sent — Zoopla's always,
    Rightmove's two times in three.

Which is why this costs a request: about 47KB for a Zoopla listing page and
100KB for a Rightmove one, and only for a listing we are about to store.

── two portals, two ways, one rule ─────────────────────────────────────────

Zoopla's page carries the halves as structured fields, `"outcode": "E14"` and
`"incode": "0UY"`, which is unambiguous and needs no judgement.

Rightmove is not read here at all, and that is a finding rather than an
omission: its detail page ships an empty `pageProps`, carries no postcode field
under any name, and has no `ld+json` block, so the only source is the markup —
where a six-character hex colour has a postcode's shape, and where three of six
pages checked held several candidates under the right outward code with nothing
to tell the listing's own from a neighbour's. The reasoning is kept in full at
the top of `worker.sources.rightmove`.
"""

from __future__ import annotations

import re

# The inward half is always digit, letter, letter. The outward half is what
# varies: E14, W1A, SW11, EC3N.
INWARD = r"\d[A-Z]{2}"

# `"outcode": "E14"` and `"incode": "0UY"`, in either order, anywhere in the
# payload. Zoopla's own fields for the listing being shown.
OUTCODE_FIELD = re.compile(r'"outcode"\s*:\s*"([A-Z]{1,2}\d{1,2}[A-Z]?)"', re.IGNORECASE)
INCODE_FIELD = re.compile(rf'"incode"\s*:\s*"({INWARD})"', re.IGNORECASE)


def tidy(outward: str, inward: str) -> str | None:
    """The two halves as one postcode, or None if they do not look like one."""

    outward = outward.strip().upper()
    inward = inward.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,2}\d{1,2}[A-Z]?", outward):
        return None
    if not re.fullmatch(INWARD, inward):
        return None
    return f"{outward} {inward}"


def from_fields(page: str, district: str | None = None) -> str | None:
    """A postcode from structured `outcode`/`incode` fields. Zoopla's shape.

    When a district is given it has to agree: the page carries panels for
    other properties, and a field belonging to one of those is not the
    postcode of the listing we asked for.
    """

    outward = OUTCODE_FIELD.search(page)
    inward = INCODE_FIELD.search(page)
    if not outward or not inward:
        return None
    if district and outward.group(1).strip().upper() != district.strip().upper():
        return None
    return tidy(outward.group(1), inward.group(1))


__all__ = ["INWARD", "from_fields", "tidy"]
