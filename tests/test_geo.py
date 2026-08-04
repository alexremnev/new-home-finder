from __future__ import annotations

import pytest

from worker.normalize.geo import (
    in_scope,
    normalize_outward,
    outward_from_listing_url,
    split_postcode,
)

BASE = "https://www.openrent.co.uk/property-to-rent"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{BASE}/hayes/room-in-a-shared-house-mildred-avenue-ub3/63102", "UB3"),
        (f"{BASE}/london/2-bed-flat-rotherhithe-street-se16/1234567", "SE16"),
        (f"{BASE}/london/studio-flat-deptford-high-street-se8/2222", "SE8"),
        (f"{BASE}/london/1-bed-flat-manilla-street-e14/999", "E14"),
        (f"{BASE}/london/1-bed-flat-brick-lane-e1/998", "E1"),
        (f"{BASE}/swanscombe/3-bed-end-terrace-mason-avenue-da10/66442", "DA10"),
        (f"{BASE}/london/1-bed-flat-somewhere-ec1a/1", "EC1A"),
        # Trailing slash and query string must not defeat the match.
        (f"{BASE}/london/2-bed-flat-street-se16/1234567/", "SE16"),
        (f"{BASE}/london/2-bed-flat-street-se16/1234567?utm_source=x", "SE16"),
        # No outward code in the slug: unknown, not absent.
        (f"{BASE}/london/lovely-flat-near-the-park/555", None),
    ],
)
def test_outward_from_listing_url(url: str, expected: str | None) -> None:
    assert outward_from_listing_url(url) == expected


def test_scope_does_not_confuse_adjacent_districts() -> None:
    """E1 must not match E14, E17, or E1W.

    A substring test would put all of them in scope, so a first stage limited to
    three districts would quietly widen to a dozen.
    """
    scope = frozenset({"E1"})
    assert in_scope(f"{BASE}/london/flat-brick-lane-e1/1", scope)[0] is True
    assert in_scope(f"{BASE}/london/flat-poplar-e14/2", scope)[0] is False
    assert in_scope(f"{BASE}/london/flat-walthamstow-e17/3", scope)[0] is False


def test_first_stage_scope() -> None:
    scope = frozenset({"SE16", "SE8", "E14"})
    inside = [
        f"{BASE}/london/2-bed-flat-rotherhithe-street-se16/1",
        f"{BASE}/london/studio-deptford-se8/2",
        f"{BASE}/london/1-bed-canary-wharf-e14/3",
    ]
    outside = [
        f"{BASE}/london/1-bed-brick-lane-e1/4",
        f"{BASE}/london/2-bed-battersea-sw11/5",
        f"{BASE}/reading/room-beresford-road-rg30/6",
    ]
    assert all(in_scope(u, scope)[0] for u in inside)
    assert not any(in_scope(u, scope)[0] for u in outside)


def test_unparseable_url_is_kept_in_scope() -> None:
    """A slug format change must cost extra fetches, not lose every listing."""
    ok, outward = in_scope(f"{BASE}/london/no-postcode-here/7", frozenset({"SE16"}))
    assert ok is True
    assert outward is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("se16", "SE16"), (" E14 ", "E14"), ("EC1A", "EC1A"), ("", None), ("london", None)],
)
def test_normalize_outward(value: str, expected: str | None) -> None:
    assert normalize_outward(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("E14 9SH", ("E14", "9SH")),
        ("se16 4dg", ("SE16", "4DG")),
        ("Flat 2, 10 High Street, SE8 3JD", ("SE8", "3JD")),
        ("E14", ("E14", None)),
        (None, (None, None)),
    ],
)
def test_split_postcode(value: str | None, expected: tuple[str | None, str | None]) -> None:
    assert split_postcode(value) == expected
