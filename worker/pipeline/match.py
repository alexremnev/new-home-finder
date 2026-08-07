"""Matching listings against saved criteria.

One rule governs everything here, and it is worth stating before the code:

    a criterion that is set requires a known value; unknown does not pass.

A listing that does not say whether pets are allowed fails a "pets allowed"
filter. So does one with no availability date against a "available before" filter.
The alternative — treating unknown as acceptable — produces alerts the recipient
cannot act on, and §11.5 is explicit that precision matters more than volume here:
three relevant alerts a day are tolerated, fifteen noisy ones lose the user.

Matching works on plain dictionaries rather than model instances, so it needs
neither a database nor a validation layer to test.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

Criteria = dict[str, Any]
ListingValues = dict[str, Any]


class Verdict:
    """Why a listing did or did not match. The reason is logged, never guessed at."""

    __slots__ = ("matched", "reason")

    def __init__(self, matched: bool, reason: str = "") -> None:
        self.matched = matched
        self.reason = reason

    def __bool__(self) -> bool:
        return self.matched

    def __repr__(self) -> str:
        return f"Verdict({self.matched}, {self.reason!r})"


MATCH = Verdict(True, "")


def matches(criteria: Criteria, listing: ListingValues) -> Verdict:
    """Decide whether one listing satisfies one set of criteria."""
    for check in (
        _check_price,
        _check_bedrooms,
        _check_property_type,
        _check_areas,
        _check_furnished,
        _check_tri_state,
        _check_available_from,
        _check_min_tenancy,
        _check_landlord_direct,
    ):
        verdict = check(criteria, listing)
        if not verdict.matched:
            return verdict
    return MATCH


# ── numeric ranges ────────────────────────────────────────────────────────


def _check_price(criteria: Criteria, listing: ListingValues) -> Verdict:
    return _range("price_pcm", criteria.get("price_pcm"), listing.get("price_pcm"))


def _check_bedrooms(criteria: Criteria, listing: ListingValues) -> Verdict:
    return _range("bedrooms", criteria.get("bedrooms"), listing.get("bedrooms"))


def _range(name: str, wanted: Any, value: Any) -> Verdict:
    if not isinstance(wanted, dict):
        return MATCH
    low, high = wanted.get("min"), wanted.get("max")
    if low is None and high is None:
        return MATCH
    if value is None:
        return Verdict(False, f"{name} unknown")
    if low is not None and value < low:
        return Verdict(False, f"{name} {value} below {low}")
    if high is not None and value > high:
        return Verdict(False, f"{name} {value} above {high}")
    return MATCH


# ── enumerations ──────────────────────────────────────────────────────────


def _check_property_type(criteria: Criteria, listing: ListingValues) -> Verdict:
    wanted = criteria.get("property_types")
    if not wanted:
        return MATCH
    value = listing.get("property_type")
    if value is None:
        return Verdict(False, "property type unknown")
    if str(value).lower() not in {str(w).lower() for w in wanted}:
        return Verdict(False, f"property type {value} not in {sorted(wanted)}")
    return MATCH


def _check_furnished(criteria: Criteria, listing: ListingValues) -> Verdict:
    wanted = criteria.get("furnished")
    if not wanted:
        return MATCH
    value = listing.get("furnished") or "unknown"
    if value == "unknown":
        return Verdict(False, "furnishing unknown")
    if str(value).lower() not in {str(w).lower() for w in wanted}:
        return Verdict(False, f"furnishing {value} not in {sorted(wanted)}")
    return MATCH


# ── geography ─────────────────────────────────────────────────────────────


def _check_areas(criteria: Criteria, listing: ListingValues) -> Verdict:
    areas = criteria.get("areas")
    if not isinstance(areas, dict):
        return MATCH

    districts = areas.get("postcode_districts") or []
    zones = areas.get("tfl_zones") or []
    if not districts and not zones:
        return MATCH

    # Districts and zones are alternative ways of naming the same geography, so
    # satisfying either is enough.
    district = (listing.get("postcode_district") or "").upper()
    if districts and district and district in {str(d).upper() for d in districts}:
        return MATCH

    zone = listing.get("tfl_zone")
    if zones and zone is not None and zone in set(zones):
        return MATCH

    if not district and listing.get("tfl_zone") is None:
        return Verdict(False, "location unknown")
    return Verdict(False, f"{district or 'zone ' + str(zone)} outside the requested areas")


# ── tri-state flags ───────────────────────────────────────────────────────

_TRI_STATE = ("pets_allowed", "bills_included")


def _check_tri_state(criteria: Criteria, listing: ListingValues) -> Verdict:
    """A flag the user asked for must be stated on the listing.

    `pets_allowed: true` means the listing has to say pets are allowed. One that
    is silent does not qualify: for someone with a pet, "unstated" is not an
    answer they can act on.
    """
    for name in _TRI_STATE:
        wanted = criteria.get(name)
        if wanted is None:
            continue
        value = listing.get(name)
        if value is None:
            return Verdict(False, f"{name} not stated")
        if bool(value) is not bool(wanted):
            return Verdict(False, f"{name} is {value}, wanted {wanted}")
    return MATCH


# ── dates and terms ───────────────────────────────────────────────────────


def _check_available_from(criteria: Criteria, listing: ListingValues) -> Verdict:
    wanted = criteria.get("available_from")
    if not isinstance(wanted, dict):
        return MATCH
    before, after = _as_date(wanted.get("before")), _as_date(wanted.get("after"))
    if before is None and after is None:
        return MATCH
    value = _as_date(listing.get("available_from"))
    if value is None:
        return Verdict(False, "availability date unknown")
    if before is not None and value > before:
        return Verdict(False, f"available {value}, wanted before {before}")
    if after is not None and value < after:
        return Verdict(False, f"available {value}, wanted after {after}")
    return MATCH


def _check_min_tenancy(criteria: Criteria, listing: ListingValues) -> Verdict:
    """A ceiling on the landlord's minimum term.

    Someone who wants a six-month let cannot take a place demanding twelve.
    """
    ceiling = criteria.get("min_tenancy_max_months")
    if ceiling is None:
        return MATCH
    value = listing.get("min_tenancy_months")
    if value is None:
        return Verdict(False, "minimum tenancy unknown")
    if value > ceiling:
        return Verdict(False, f"minimum tenancy {value} months exceeds {ceiling}")
    return MATCH


def _check_landlord_direct(criteria: Criteria, listing: ListingValues) -> Verdict:
    if not criteria.get("landlord_direct_only"):
        return MATCH
    if listing.get("is_landlord_direct") is not True:
        return Verdict(False, "not stated as landlord-direct")
    return MATCH


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ── eligibility, separate from criteria ───────────────────────────────────


def is_eligible(
    listing: ListingValues,
    *,
    backfill_from: Any,
    sent_today: int,
    max_alerts_per_day: int,
) -> Verdict:
    """Checks that are about the subscription, not about the listing's qualities.

    Kept apart from `matches` because they answer a different question: not "is
    this a suitable home" but "should this person be messaged about it now".
    """
    if sent_today >= max_alerts_per_day:
        return Verdict(False, f"daily cap of {max_alerts_per_day} reached")

    first_seen = _as_datetime(listing.get("first_seen_at"))
    cutoff = _as_datetime(backfill_from)
    if first_seen is not None and cutoff is not None and first_seen <= cutoff:
        # Existing stock at the moment of subscribing is not news; sending it would
        # open the relationship with a burst of listings the person did not ask for.
        return Verdict(False, "first seen before the subscription started")
    return MATCH


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
