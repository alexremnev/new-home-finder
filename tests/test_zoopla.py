"""Zoopla's parser, against a page the real site served.

`tests/fixtures/zoopla_e14_search.html` is built from a genuine E14 search page
taken on 26 September 2026 and trimmed to four listings. Two details of the
real page are preserved deliberately, because both have already caused a bug:

  * the payload is split across two `self.__next_f.push` calls, so the stream
    has to be reassembled before the listings array can be found;
  * one listing shows `price: "£3,831 pcm"` with `priceUnformatted: 884` — the
    weekly figure — which is the listing that proved the number beside the
    price cannot be trusted.

The fixture also carries a `featuredListingsFormatted` and an
`extendedListingsFormatted` array, neither of which may be read.

Nothing here touches the network.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from worker.sources.fetch import Reply
from worker.sources.sweep import _announceable
from worker.sources.zoopla import (
    Zoopla,
    a_dwelling,
    catches_in,
    flight_stream,
    kind_of,
    listings_in,
    monthly,
    pages_total,
    picture,
    published,
    search_url,
    stated_outcode,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "zoopla_e14_search.html"
PAGE = FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def caught() -> dict[str, object]:
    return {
        one.listing.external_id: one for one in catches_in(PAGE, "e14").caught
    }


# ── getting at the data at all ───────────────────────────────────────────

def test_the_stream_is_reassembled_from_every_push() -> None:
    # Zoopla ships the payload in pieces. Reading only the first would find
    # the array's opening bracket and none of its contents.
    stream = flight_stream(PAGE)
    assert '"regularListingsFormatted"' in stream
    assert stream.count("{") > 10


def test_only_the_districts_own_listings_are_read() -> None:
    # The page carries three arrays. `featuredListingsFormatted` is promoted
    # and out of date order; `extendedListingsFormatted` is Zoopla's widened
    # radius and is in *other* districts, so storing it under this one would
    # send somebody flats in an area they did not choose.
    ids = {str(row.get("listingId")) for row in listings_in(PAGE)}
    assert "999" not in ids, "a promoted listing was read"
    assert "888" not in ids, "a listing from outside the district was read"
    assert len(ids) == 4


def test_a_page_with_no_stream_yields_nothing_rather_than_raising() -> None:
    read = catches_in("<html><body>maintenance</body></html>", "E14")
    assert read.caught == [] and read.skipped == 0


def test_the_pagination_survives() -> None:
    assert pages_total(PAGE) == 40


# ── the rent, which is the part that matters most ────────────────────────

def test_the_displayed_price_beats_the_number_beside_it() -> None:
    # The listing that made this necessary. Taking `priceUnformatted` would
    # have stored a £3,831 flat at £884 and sent it to everybody with a small
    # budget, with the wrong rent printed in the alert.
    assert monthly({"price": "£3,831 pcm", "priceUnformatted": 884}) == 3831


def test_a_weekly_rent_is_converted() -> None:
    assert monthly({"price": "£500 pw", "priceUnformatted": 500}) == 2167
    assert monthly({"price": "£2,575 pcm", "priceUnformatted": 2575}) == 2575


def test_a_bare_number_contradicted_by_a_weekly_label_is_refused() -> None:
    # No readable price string, and the weekly label repeats the number: the
    # number is the weekly one and the monthly figure is not knowable here.
    # Better no listing than a listing at a quarter of its rent.
    assert monthly(
        {
            "price": "",
            "priceUnformatted": 884,
            "alternativeRentFrequencyLabel": "£884 pw",
        }
    ) is None
    # A label that differs is not a contradiction, so the number stands.
    assert monthly(
        {
            "price": "",
            "priceUnformatted": 3841,
            "alternativeRentFrequencyLabel": "£886.38 pw",
        }
    ) == 3841


def test_an_unusable_price_is_refused() -> None:
    assert monthly({}) is None
    assert monthly({"price": "POA"}) is None
    assert monthly({"price": "£50 pcm", "priceUnformatted": 50}) is None


# ── one listing's fields ─────────────────────────────────────────────────

def test_a_flat_is_read_whole(caught: dict) -> None:
    one = caught["74348552"].listing
    assert one.source_key == "zoopla"
    assert one.url == "https://www.zoopla.co.uk/to-rent/details/74348552/"
    assert one.price_pcm == 2575
    assert one.bedrooms == 2
    assert one.bathrooms == 1
    assert one.property_type == "flat"
    assert one.postcode_district == "E14"
    assert one.floor_area_sqft == 703
    assert one.pets_allowed is True
    assert one.lat is not None and one.lng is not None


def test_the_bedroom_count_comes_from_the_little_figures(caught: dict) -> None:
    # There is no `bedrooms` field on these objects at all — the counts are in
    # `features` as {"content": 2, "iconId": "bed"}.
    assert caught["74348552"].listing.bedrooms == 2


def test_the_mismatched_listing_is_stored_at_its_real_rent(caught: dict) -> None:
    assert caught["74348497"].listing.price_pcm == 3831


def test_no_full_postcode_is_claimed(caught: dict) -> None:
    # Zoopla states the outward code and stops. Half a postcode is worse than
    # none: the duplicate rule compares full postcodes, and "E14" alone would
    # make every 2-bed at one price in E14 the same flat.
    assert caught["74348552"].listing.postcode is None
    assert caught["74348552"].listing.postcode_district == "E14"


def test_a_town_house_is_a_house(caught: dict) -> None:
    assert caught["74348031"].listing.property_type == "house"


def test_no_stated_area_is_unknown_rather_than_zero(caught: dict) -> None:
    assert caught["48298538"].listing.floor_area_sqft is None


# ── the pieces ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("flat", "flat"),
        ("terraced", "house"),
        ("semi_detached", "house"),
        ("detached", "house"),
        ("town_house", "house"),
        ("maisonette", "flat"),
        ("bungalow", "house"),
        ("house_share", "room"),
        ("flat_share", "room"),
        (None, None),
        # Somewhere to live, but neither a house nor a flat.
        ("house_boat", None),
    ],
)
def test_the_portals_own_words_map_to_the_four_the_filter_offers(
    kind: str | None, expected: str | None
) -> None:
    assert kind_of(kind) == expected


def test_a_parking_space_is_not_a_home() -> None:
    assert a_dwelling("parking") is False
    assert a_dwelling("garage") is False
    assert a_dwelling("flat") is True


def test_a_day_becomes_the_end_of_that_day() -> None:
    # The end, not midnight: a district first watched at 14:00 could otherwise
    # never announce anything published that same day, and a new subscriber
    # would hear nothing until tomorrow.
    stamp = published("26th Sep 2026")
    assert stamp is not None
    assert stamp.date() == datetime(2026, 9, 26, tzinfo=UTC).date()
    assert stamp.hour == 23 and stamp.minute == 59

    noon = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    assert stamp > noon


def test_every_ordinal_suffix_is_read() -> None:
    for text, day in [
        ("1st Oct 2026", 1), ("2nd Oct 2026", 2), ("3rd Oct 2026", 3),
        ("21st Dec 2026", 21), ("30th Jun 2026", 30),
    ]:
        stamp = published(text)
        assert stamp is not None and stamp.day == day


def test_an_unreadable_date_is_none() -> None:
    assert published(None) is None
    assert published("") is None
    assert published("last Tuesday") is None
    assert published("32nd Smarch 2026") is None


def test_the_small_picture_is_the_one_stored() -> None:
    # The same photograph at 645 and at 354 wide. It is the subscriber's phone
    # that pulls it, so the narrow one is what gets stored.
    chosen = picture(
        {
            "image": {
                "responsiveImgList": [
                    {"src": "https://lid.zoocdn.com/645/430/a.jpg", "width": 645},
                    {"src": "https://lid.zoocdn.com/354/255/a.jpg", "width": 354},
                ],
                "src": "https://lid.zoocdn.com/645/430/a.jpg",
            }
        }
    )
    assert chosen == "https://lid.zoocdn.com/354/255/a.jpg"


def test_no_picture_is_none_rather_than_a_broken_url() -> None:
    assert picture({}) is None
    assert picture({"image": {"responsiveImgList": []}}) is None
    # WhatsApp will not follow a relative path, and neither will a browser.
    assert picture({"image": {"src": "/media/a.jpg"}}) is None


def test_the_outcode_is_read_off_the_end_of_the_address() -> None:
    assert stated_outcode("Balfron Tower, St Leonards Road, London E14") == "E14"
    assert stated_outcode("Lady Margaret Road, Southall UB1") == "UB1"
    assert stated_outcode("Somewhere Unnamed") is None


def test_the_search_url_is_newest_first_and_pages() -> None:
    assert search_url("E14") == (
        "https://www.zoopla.co.uk/to-rent/property/e14/?results_sort=newest_listings"
    )
    assert search_url("E14", 2).endswith("&pn=2")
    assert "pn=" not in search_url("E14", 1)


# ── paging, and what gets announced ──────────────────────────────────────

class Pages:
    def __init__(self, *bodies: str) -> None:
        self.bodies = list(bodies)
        self.asked: list[str] = []

    def get(self, url: str, **_: object) -> Reply:
        self.asked.append(url)
        body = self.bodies[min(len(self.asked) - 1, len(self.bodies) - 1)]
        return Reply(status=200, body=body, wire=1000, impersonated="chrome124")


class Quiet:
    def log(self, *_: object, **__: object) -> None: ...
    def count(self, *_: object, **__: object) -> None: ...
    def set(self, *_: object, **__: object) -> None: ...
    def degrade(self, *_: object, **__: object) -> None: ...


def test_starting_to_watch_a_district_costs_one_page() -> None:
    pages = Pages(PAGE)
    harvest = Zoopla().harvest("E14", pages, Quiet(), None)  # type: ignore[arg-type]
    assert len(pages.asked) == 1
    assert harvest.complete is True
    assert len(harvest.caught) == 4


def test_paging_stops_once_the_page_predates_the_watch() -> None:
    pages = Pages(PAGE)
    harvest = Zoopla().harvest(
        "E14", pages, Quiet(), datetime(2030, 1, 1, tzinfo=UTC)  # type: ignore[arg-type]
    )
    assert len(pages.asked) == 1
    assert harvest.complete is True


def test_a_district_still_newer_than_the_watch_pages_to_the_cap() -> None:
    pages = Pages(PAGE)
    harvest = Zoopla(max_pages=3).harvest(
        "E14", pages, Quiet(), datetime(2020, 1, 1, tzinfo=UTC)  # type: ignore[arg-type]
    )
    assert len(pages.asked) == 3
    assert harvest.complete is False
    # The same page served three times must not be counted three times.
    assert len(harvest.caught) == 4


def test_only_listings_published_since_the_watch_are_announced(caught: dict) -> None:
    portal = Zoopla()
    one = caught["74348552"]
    assert one.first_listed is not None
    assert _announceable(portal, one, None) is False
    assert _announceable(portal, one, one.first_listed - timedelta(days=2)) is True
    assert _announceable(portal, one, one.first_listed + timedelta(days=2)) is False
