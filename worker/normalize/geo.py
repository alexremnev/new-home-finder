"""Postcode handling.

The outward code carried in a listing URL lets a run decide which listings are in
scope before making any request for them, which is what keeps a district-limited
first stage to a handful of fetches instead of one per new listing nationwide.

That prefilter is advisory. The authoritative postcode is the one on the listing
page, and the two are reconciled after extraction: a URL slug is derived from a
street address and can be absent or wrong.
"""

from __future__ import annotations

import re

# Outward code: one or two letters, one or two digits, optional trailing letter.
# Anchored on both sides so a scope of E1 cannot match E14 — the two are
# different districts, and a substring test silently mixes them.
_OUTWARD = re.compile(r"^[A-Z]{1,2}[0-9]{1,2}[A-Z]?$")

# OpenRent listing paths end with .../<slug ending in the outward code>/<id>,
# for example .../room-in-a-shared-house-mildred-avenue-ub3/63102
_SLUG_OUTWARD = re.compile(r"-([a-z]{1,2}[0-9]{1,2}[a-z]?)/[0-9]+/?$")

# A full postcode, from which the outward half is taken.
_FULL_POSTCODE = re.compile(
    r"\b([A-Z]{1,2}[0-9][0-9A-Z]?)\s*([0-9][A-Z]{2})\b", re.IGNORECASE
)


def normalize_outward(value: str | None) -> str | None:
    """Return a canonical outward code, or None if the value is not one."""
    if not value:
        return None
    candidate = value.strip().upper().replace(" ", "")
    return candidate if _OUTWARD.match(candidate) else None


def outward_from_listing_url(url: str) -> str | None:
    """Extract the outward code from an OpenRent listing URL.

    Returns None when the slug does not carry one, which happens and must be
    treated as "unknown", never as "out of scope".
    """
    match = _SLUG_OUTWARD.search(url.split("?", 1)[0])
    return normalize_outward(match.group(1)) if match else None


def split_postcode(value: str | None) -> tuple[str | None, str | None]:
    """Split a full postcode into (outward, inward)."""
    if not value:
        return None, None
    match = _FULL_POSTCODE.search(value)
    if match:
        return normalize_outward(match.group(1)), match.group(2).upper()
    return normalize_outward(value), None


# An address written out for a person ends with the district: "Rope Street,
# Surrey Quays, London, SE16". The letters must sit against the digits, so a
# "Studio 5" or a house number cannot be mistaken for a district.
_TRAILING_OUTWARD = re.compile(r"([A-Z]{1,2}[0-9]{1,2}[A-Z]?)\s*$", re.IGNORECASE)


def outward_from_address(value: str | None) -> str | None:
    """The district named at the end of a written address, if it names one.

    Rightmove's search cards carry an address rather than a URL slug, so this is
    what applies the district filter before any listing page is requested.
    Returns None when the address does not name a district — which means unknown,
    never out of scope.
    """
    if not value:
        return None
    outward, _ = split_postcode(value)
    if outward:
        return outward
    match = _TRAILING_OUTWARD.search(value.strip())
    return normalize_outward(match.group(1)) if match else None


def in_scope(url: str, scope: frozenset[str]) -> tuple[bool, str | None]:
    """Decide whether a listing URL is within the configured districts.

    Returns (in_scope, outward). An unparseable URL is reported as in scope so
    that a slug format change degrades into extra fetches rather than into
    silently dropping every listing.
    """
    outward = outward_from_listing_url(url)
    if outward is None:
        return True, None
    return outward in scope, outward
