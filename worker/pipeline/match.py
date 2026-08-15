"""Matching listings against saved criteria.

One rule governs everything here, and it is worth stating before the code:

    a criterion rejects a listing only on a value that contradicts it.
    Unknown passes.

A listing that does not say whether pets are allowed still satisfies a "pets
allowed" filter; one that says they are not does not.

This is the opposite of what this file did originally, and the reversal was not a
change of taste. The earlier rule — unknown fails — was defensible while every
listing came from a scraped page where the field was either present or genuinely
absent. It stopped being defensible once a feed arrived that never states pets or
bills at all: under the old rule, anybody who ticked "pets allowed" in the wizard
received nothing, for ever, with no error anywhere and no way to find out. A
filter that silently matches zero listings is worse than a filter that occasionally
includes one the person has to check themselves.

What makes the reversal honest is the message: the renderer says "not stated"
rather than staying silent, so the recipient can see which of their requirements
this listing has not actually answered.

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
        # Unknown passes, like everywhere else. In practice both price and
        # bedrooms are refused at ingest when absent, so this is a guard rather
        # than a path anything travels.
        return MATCH
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
        return MATCH  # unknown passes; the renderer says so
    if str(value).lower() not in {str(w).lower() for w in wanted}:
        return Verdict(False, f"property type {value} not in {sorted(wanted)}")
    return MATCH


def _check_furnished(criteria: Criteria, listing: ListingValues) -> Verdict:
    wanted = criteria.get("furnished")
    if not wanted:
        return MATCH
    value = listing.get("furnished") or "unknown"
    if value == "unknown":
        return MATCH  # unknown passes; the renderer says so
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
        # The one place unknown still fails, and deliberately: an area filter is
        # the only criterion nobody sets by accident, and a listing of unknown
        # location cannot be viewed, travelled to, or judged. Sending it would be
        # noise rather than a judgement call the recipient can make.
        return Verdict(False, "location unknown")
    return Verdict(False, f"{district or 'zone ' + str(zone)} outside the requested areas")


# ── tri-state flags ───────────────────────────────────────────────────────

_TRI_STATE = ("pets_allowed", "bills_included")


def _check_tri_state(criteria: Criteria, listing: ListingValues) -> Verdict:
    """A flag the user asked for is only violated by a listing that contradicts it.

    `pets_allowed: true` excludes a listing that says pets are *not* allowed, and
    keeps one that says nothing. Most sources say nothing most of the time — one
    feed never mentions pets at all — so rejecting silence would turn this
    criterion into a mute off switch for the whole subscription.
    """
    for name in _TRI_STATE:
        wanted = criteria.get(name)
        if wanted is None:
            continue
        value = listing.get(name)
        if value is None:
            continue  # unknown passes; the renderer says so
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
        return MATCH  # unknown passes; the renderer says so
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
        return MATCH  # unknown passes; the renderer says so
    if value > ceiling:
        return Verdict(False, f"minimum tenancy {value} months exceeds {ceiling}")
    return MATCH


def _check_landlord_direct(criteria: Criteria, listing: ListingValues) -> Verdict:
    """Three states, and the middle one is why this is not `is not True`.

    `False` is a statement — an agency listing — and it contradicts the criterion.
    `None` is silence, and under the rule at the top of this file silence passes.
    Conflating them, as `is not True` did, rejected every source that does not
    report agency status at all.
    """
    if not criteria.get("landlord_direct_only"):
        return MATCH
    if listing.get("is_landlord_direct") is False:
        return Verdict(False, "listed by an agency, not the landlord")
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


def is_eligible(listing: ListingValues, *, backfill_from: Any) -> Verdict:
    """Checks that are about the subscription, not about the listing's qualities.

    Kept apart from `matches` because they answer a different question: not "is
    this a suitable home" but "should this person be messaged about it now".

    There is no daily cap. Everything that matches is delivered: withholding a
    listing that matched is invisible to the person waiting for it, and the way to
    get fewer messages is a narrower filter, which they control.
    """
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
