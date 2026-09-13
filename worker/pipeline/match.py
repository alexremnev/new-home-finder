from __future__ import annotations

from datetime import date, datetime
from typing import Any

Criteria = dict[str, Any]
ListingValues = dict[str, Any]

class Verdict:

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

    for check in (
        _check_price,
        _check_bedrooms,
        _check_bathrooms,
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

def _check_price(criteria: Criteria, listing: ListingValues) -> Verdict:
    return _range("price_pcm", criteria.get("price_pcm"), listing.get("price_pcm"))

def _check_bedrooms(criteria: Criteria, listing: ListingValues) -> Verdict:
    return _range("bedrooms", criteria.get("bedrooms"), listing.get("bedrooms"))

def _check_bathrooms(criteria: Criteria, listing: ListingValues) -> Verdict:

    return _range("bathrooms", criteria.get("bathrooms"), listing.get("bathrooms"))

def _range(name: str, wanted: Any, value: Any) -> Verdict:
    if not isinstance(wanted, dict):
        return MATCH
    low, high = wanted.get("min"), wanted.get("max")
    if low is None and high is None:
        return MATCH
    if value is None:

        return MATCH
    if low is not None and value < low:
        return Verdict(False, f"{name} {value} below {low}")
    if high is not None and value > high:
        return Verdict(False, f"{name} {value} above {high}")
    return MATCH

def _check_property_type(criteria: Criteria, listing: ListingValues) -> Verdict:
    wanted = criteria.get("property_types")
    if not wanted:
        return MATCH
    value = listing.get("property_type")
    if value is None:
        return MATCH
    if str(value).lower() not in {str(w).lower() for w in wanted}:
        return Verdict(False, f"property type {value} not in {sorted(wanted)}")
    return MATCH

def _check_furnished(criteria: Criteria, listing: ListingValues) -> Verdict:
    wanted = criteria.get("furnished")
    if not wanted:
        return MATCH
    value = listing.get("furnished") or "unknown"
    if value == "unknown":
        return MATCH
    if str(value).lower() not in {str(w).lower() for w in wanted}:
        return Verdict(False, f"furnishing {value} not in {sorted(wanted)}")
    return MATCH

def _check_areas(criteria: Criteria, listing: ListingValues) -> Verdict:
    areas = criteria.get("areas")
    if not isinstance(areas, dict):
        return MATCH

    districts = areas.get("postcode_districts") or []
    zones = areas.get("tfl_zones") or []
    if not districts and not zones:
        return MATCH

    district = (listing.get("postcode_district") or "").upper()
    if districts and district and district in {str(d).upper() for d in districts}:
        return MATCH

    zone = listing.get("tfl_zone")
    if zones and zone is not None and zone in set(zones):
        return MATCH

    if not district and listing.get("tfl_zone") is None:

        return Verdict(False, "location unknown")
    return Verdict(False, f"{district or 'zone ' + str(zone)} outside the requested areas")

_TRI_STATE = ("pets_allowed", "bills_included")

def _check_tri_state(criteria: Criteria, listing: ListingValues) -> Verdict:

    for name in _TRI_STATE:
        wanted = criteria.get(name)
        if wanted is None:
            continue
        value = listing.get(name)
        if value is None:
            continue
        if bool(value) is not bool(wanted):
            return Verdict(False, f"{name} is {value}, wanted {wanted}")
    return MATCH

def _check_available_from(criteria: Criteria, listing: ListingValues) -> Verdict:
    wanted = criteria.get("available_from")
    if not isinstance(wanted, dict):
        return MATCH
    before, after = _as_date(wanted.get("before")), _as_date(wanted.get("after"))
    if before is None and after is None:
        return MATCH
    value = _as_date(listing.get("available_from"))
    if value is None:
        return MATCH
    if before is not None and value > before:
        return Verdict(False, f"available {value}, wanted before {before}")
    if after is not None and value < after:
        return Verdict(False, f"available {value}, wanted after {after}")
    return MATCH

def _check_min_tenancy(criteria: Criteria, listing: ListingValues) -> Verdict:

    ceiling = criteria.get("min_tenancy_max_months")
    if ceiling is None:
        return MATCH
    value = listing.get("min_tenancy_months")
    if value is None:
        return MATCH
    if value > ceiling:
        return Verdict(False, f"minimum tenancy {value} months exceeds {ceiling}")
    return MATCH

def _check_landlord_direct(criteria: Criteria, listing: ListingValues) -> Verdict:

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

def is_eligible(listing: ListingValues, *, backfill_from: Any) -> Verdict:

    first_seen = _as_datetime(listing.get("first_seen_at"))
    cutoff = _as_datetime(backfill_from)
    if first_seen is not None and cutoff is not None and first_seen <= cutoff:

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
