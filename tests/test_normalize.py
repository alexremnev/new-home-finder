from __future__ import annotations

from datetime import date

import pytest

from worker.normalize import dates, money, text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Monthly, as displayed by each source in use.
        ("£1,950 pcm", 1950),
        ("£590.00 p/m", 590),
        ("£2,200 per month", 2200),
        ("£1,500", 1500),               # unqualified: monthly by convention
        ("1950 pcm", 1950),
        ("£1,950\xa0pcm", 1950),        # non-breaking space
        # Weekly, converted with the portals' own 52/12 convention.
        ("£450 pw", 1950),
        ("£450 p/w", 1950),
        ("£450 per week", 1950),
        # Both shown at once: the leading figure is weekly, and taking the
        # amount without the period would understate the rent fourfold.
        ("£450 pw (£1,950 pcm)", 1950),
    ],
)
def test_to_pcm(raw: str, expected: int) -> None:
    assert money.to_pcm(raw) == expected


def test_weekly_and_monthly_agree() -> None:
    """The same rent expressed either way must land on the same number."""
    assert money.to_pcm("£450 pw") == money.to_pcm("£1,950 pcm")


def test_missing_price_is_an_error_not_a_zero() -> None:
    with pytest.raises(money.PriceError):
        money.to_pcm("POA")
    with pytest.raises(money.PriceError):
        money.to_pcm(None)


def test_deposit_is_a_plain_sum() -> None:
    assert money.deposit_to_pounds("£415.38") == 415.38
    assert money.deposit_to_pounds(None) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pets allowed", True),
        ("Pets considered", True),
        ("Pets: Yes", True),
        ("No pets", False),
        ("Pets not allowed", False),
        ("Bills included", True),
        # "not included" contains "included": negation must win.
        ("Bills not included", False),
        ("", None),
        (None, None),
        ("Ask the landlord", None),
    ],
)
def test_tri_state_keeps_three_states(raw: str | None, expected: bool | None) -> None:
    assert text.tri_state(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2 bedrooms", 2),
        ("Studio", 0),          # a studio is zero bedrooms, not unknown
        ("Bedsit", 0),
        ("Room in a Shared House", 1),
        ("3 bed flat", 3),
        (None, None),
        ("", None),
    ],
)
def test_bedrooms(raw: str | None, expected: int | None) -> None:
    assert text.bedrooms(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Furnished", "furnished"),
        ("Unfurnished", "unfurnished"),
        ("Part furnished", "part"),
        ("Partially furnished", "part"),
        # "unfurnished" contains "furnished": the longer match must win.
        ("Not furnished", "unfurnished"),
        (None, "unknown"),
        ("Whatever", "unknown"),
    ],
)
def test_furnished(raw: str | None, expected: str) -> None:
    assert text.furnished(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2 bed flat", "flat"),
        ("Apartment", "flat"),
        ("Room in a Shared Flat", "room"),
        ("End terrace", "terrace"),
        ("Maisonette", "maisonette"),
        (None, None),
    ],
)
def test_property_type(raw: str | None, expected: str | None) -> None:
    assert text.property_type(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("12 months minimum", 12), ("6 month let", 6), ("1 year", 12), (None, None)],
)
def test_min_tenancy(raw: str | None, expected: int | None) -> None:
    assert text.min_tenancy_months(raw) == expected


TODAY = date(2026, 8, 4)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Now", TODAY),
        ("Today", TODAY),
        ("Available immediately", TODAY),
        ("2026-09-12", date(2026, 9, 12)),
        ("12 Sep 2026", date(2026, 9, 12)),
        ("12 September 2026", date(2026, 9, 12)),
        ("12th Sep", date(2026, 9, 12)),
        ("Sep 12", date(2026, 9, 12)),
        ("12/09/2026", date(2026, 9, 12)),   # day-first, the UK convention
        (None, None),
        ("", None),
        ("whenever", None),
    ],
)
def test_available_from(raw: str | None, expected: date | None) -> None:
    assert dates.parse_available_from(raw, today=TODAY) == expected


def test_bare_day_month_rolls_to_next_year_when_already_past() -> None:
    """A date months behind us means next year, not a listing in the past."""
    assert dates.parse_available_from("12 Feb", today=TODAY) == date(2027, 2, 12)
    # A few days behind is a grace period, not a roll-over.
    assert dates.parse_available_from("1 Aug", today=TODAY) == date(2026, 8, 1)


def test_implausible_dates_are_rejected() -> None:
    """A misread field must yield None rather than a date years away."""
    assert dates.parse_available_from("2019-01-01", today=TODAY) is None
    assert dates.parse_available_from("2031-01-01", today=TODAY) is None


def test_invalid_calendar_date_is_rejected() -> None:
    assert dates.parse_available_from("31 Feb 2026", today=TODAY) is None
