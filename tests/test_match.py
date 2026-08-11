from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from worker.pipeline.match import is_eligible, matches


def listing(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "price_pcm": 1950,
        "bedrooms": 2,
        "property_type": "flat",
        "furnished": "furnished",
        "pets_allowed": True,
        "bills_included": False,
        "available_from": date(2026, 9, 1),
        "min_tenancy_months": 6,
        "postcode_district": "SE16",
        "tfl_zone": 2,
        "is_landlord_direct": True,
        "first_seen_at": datetime(2026, 8, 7, 12, 0),
    }
    values.update(overrides)
    return values


# ── the empty criteria case ───────────────────────────────────────────────


def test_empty_criteria_match_everything() -> None:
    assert matches({}, listing())


# ── ranges ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("price", "expected"),
    [(1200, True), (1950, True), (2200, True), (1199, False), (2201, False)],
)
def test_price_range_is_inclusive(price: int, expected: bool) -> None:
    criteria = {"price_pcm": {"min": 1200, "max": 2200}}
    assert bool(matches(criteria, listing(price_pcm=price))) is expected


def test_an_open_ended_range_works() -> None:
    assert matches({"price_pcm": {"max": 2000}}, listing(price_pcm=500))
    assert not matches({"price_pcm": {"min": 2000}}, listing(price_pcm=500))


def test_a_studio_is_zero_bedrooms_not_a_missing_value() -> None:
    """A studio must match a "from zero bedrooms" filter rather than fall out."""
    assert matches({"bedrooms": {"min": 0, "max": 1}}, listing(bedrooms=0))
    assert not matches({"bedrooms": {"min": 1}}, listing(bedrooms=0))


# ── the rule about unknown values ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("criteria", "unknown_field"),
    [
        ({"price_pcm": {"max": 2000}}, "price_pcm"),
        ({"bedrooms": {"min": 1}}, "bedrooms"),
        ({"property_types": ["flat"]}, "property_type"),
        ({"furnished": ["furnished"]}, "furnished"),
        ({"pets_allowed": True}, "pets_allowed"),
        ({"available_from": {"before": "2026-10-01"}}, "available_from"),
        ({"min_tenancy_max_months": 12}, "min_tenancy_months"),
    ],
)
def test_a_set_criterion_requires_a_known_value(
    criteria: dict[str, Any], unknown_field: str
) -> None:
    """The single rule that governs matching.

    Treating unknown as acceptable produces alerts the recipient cannot act on,
    and precision matters more than volume: three relevant alerts a day are
    tolerated, fifteen noisy ones lose the user.
    """
    verdict = matches(criteria, listing(**{unknown_field: None}))
    assert not verdict
    assert "unknown" in verdict.reason or "not stated" in verdict.reason


def test_an_unset_criterion_ignores_an_unknown_value() -> None:
    """Only criteria that were asked for constrain anything."""
    assert matches({}, listing(pets_allowed=None, available_from=None, furnished="unknown"))


def test_furnishing_unknown_is_not_a_furnishing_state() -> None:
    assert not matches({"furnished": ["furnished"]}, listing(furnished="unknown"))


# ── tri-state flags ───────────────────────────────────────────────────────


def test_pets_wanted_requires_the_listing_to_say_so() -> None:
    assert matches({"pets_allowed": True}, listing(pets_allowed=True))
    assert not matches({"pets_allowed": True}, listing(pets_allowed=False))
    # Silent is not a yes. For someone with a pet it is not an answer they can act on.
    assert not matches({"pets_allowed": True}, listing(pets_allowed=None))


def test_a_flag_can_be_required_to_be_false() -> None:
    assert matches({"bills_included": False}, listing(bills_included=False))
    assert not matches({"bills_included": False}, listing(bills_included=True))


def test_a_null_flag_in_criteria_means_do_not_filter() -> None:
    """`null` and `false` are different instructions and must not be conflated."""
    for value in (True, False, None):
        assert matches({"bills_included": None}, listing(bills_included=value))


# ── geography ─────────────────────────────────────────────────────────────


def test_district_list() -> None:
    criteria = {"areas": {"postcode_districts": ["SE16", "SE8", "E14"]}}
    assert matches(criteria, listing(postcode_district="SE16"))
    assert not matches(criteria, listing(postcode_district="E1"))


def test_district_matching_is_exact_not_a_prefix() -> None:
    """E1 must not admit E14, the same trap as in discovery."""
    criteria = {"areas": {"postcode_districts": ["E1"]}}
    assert matches(criteria, listing(postcode_district="E1", tfl_zone=None))
    assert not matches(criteria, listing(postcode_district="E14", tfl_zone=None))


def test_district_and_zone_are_alternatives() -> None:
    """Both name the same geography, so satisfying either is enough."""
    criteria = {"areas": {"postcode_districts": ["W1"], "tfl_zones": [2]}}
    assert matches(criteria, listing(postcode_district="SE16", tfl_zone=2))
    assert matches(criteria, listing(postcode_district="W1", tfl_zone=5))
    assert not matches(criteria, listing(postcode_district="SE16", tfl_zone=5))


def test_unknown_location_fails_an_area_filter() -> None:
    criteria = {"areas": {"postcode_districts": ["SE16"]}}
    assert not matches(criteria, listing(postcode_district=None, tfl_zone=None))


def test_empty_areas_constrain_nothing() -> None:
    assert matches({"areas": {}}, listing(postcode_district=None, tfl_zone=None))


# ── dates and terms ───────────────────────────────────────────────────────


def test_available_before() -> None:
    criteria = {"available_from": {"before": "2026-09-15"}}
    assert matches(criteria, listing(available_from=date(2026, 9, 1)))
    assert matches(criteria, listing(available_from=date(2026, 9, 15)))
    assert not matches(criteria, listing(available_from=date(2026, 9, 16)))


def test_available_after() -> None:
    criteria = {"available_from": {"after": "2026-09-01"}}
    assert not matches(criteria, listing(available_from=date(2026, 8, 1)))
    assert matches(criteria, listing(available_from=date(2026, 9, 2)))


def test_dates_are_accepted_as_strings_or_dates() -> None:
    criteria = {"available_from": {"before": date(2026, 9, 15)}}
    assert matches(criteria, listing(available_from="2026-09-01"))


def test_minimum_tenancy_is_a_ceiling_on_the_landlords_demand() -> None:
    """Someone wanting six months cannot take a place demanding twelve."""
    criteria = {"min_tenancy_max_months": 6}
    assert matches(criteria, listing(min_tenancy_months=6))
    assert not matches(criteria, listing(min_tenancy_months=12))


def test_landlord_direct_only() -> None:
    assert matches({"landlord_direct_only": True}, listing(is_landlord_direct=True))
    assert not matches({"landlord_direct_only": True}, listing(is_landlord_direct=None))
    assert matches({"landlord_direct_only": False}, listing(is_landlord_direct=None))


# ── a realistic set of criteria ────────────────────────────────────────────

REALISTIC = {
    "price_pcm": {"min": 1200, "max": 2200},
    "bedrooms": {"min": 1, "max": 2},
    "property_types": ["flat", "studio"],
    "areas": {"postcode_districts": ["SE16", "SE8", "E14"]},
    "pets_allowed": True,
    "furnished": ["furnished", "part"],
    "available_from": {"before": "2026-09-15"},
    "min_tenancy_max_months": 12,
    "landlord_direct_only": False,
    "notify": {"new_listings": True, "price_drop": False},
}


def test_a_realistic_subscription_matches_a_suitable_listing() -> None:
    assert matches(REALISTIC, listing())


@pytest.mark.parametrize(
    ("field", "value", "expected_in_reason"),
    [
        ("price_pcm", 2500, "above"),
        ("bedrooms", 3, "above"),
        ("property_type", "house", "not in"),
        ("postcode_district", "W1", "outside"),
        ("pets_allowed", False, "pets_allowed"),
        ("furnished", "unfurnished", "not in"),
        ("available_from", date(2026, 12, 1), "wanted before"),
        ("min_tenancy_months", 24, "exceeds"),
    ],
)
def test_each_criterion_can_reject_and_says_why(
    field: str, value: Any, expected_in_reason: str
) -> None:
    verdict = matches(REALISTIC, listing(**{field: value}))
    assert not verdict
    assert expected_in_reason in verdict.reason, verdict.reason


# ── eligibility ───────────────────────────────────────────────────────────

SUBSCRIBED_AT = datetime(2026, 8, 7, 10, 0)



def test_existing_stock_at_subscription_time_is_not_news() -> None:
    """Otherwise the relationship opens with a burst of listings nobody asked for."""
    older = listing(first_seen_at=SUBSCRIBED_AT - timedelta(hours=1))
    verdict = is_eligible(older, backfill_from=SUBSCRIBED_AT)
    assert not verdict
    assert "before the subscription" in verdict.reason


def test_a_listing_seen_after_subscribing_is_eligible() -> None:
    newer = listing(first_seen_at=SUBSCRIBED_AT + timedelta(minutes=1))
    assert is_eligible(newer, backfill_from=SUBSCRIBED_AT)


def test_eligibility_and_suitability_are_separate_questions() -> None:
    """A listing can be a perfect home and still not be worth messaging about now."""
    older = listing(first_seen_at=SUBSCRIBED_AT - timedelta(days=1))
    assert matches(REALISTIC, older)
    assert not is_eligible(older, backfill_from=SUBSCRIBED_AT)
