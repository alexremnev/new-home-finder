"""Rightmove's parser, against a page the real site served.

`tests/fixtures/rightmove_e14_search.html` is a genuine E14 search page taken
on 26 September 2026, trimmed to six listings that between them cover every
shape the parser has to survive: a full postcode and one stated only to the
outcode, an address with no outcode at all, a studio, a house, a promoted
listing, a listing with no floor area, and a first photograph published as a
PNG when JPEGs of the same flat exist.

Nothing here touches the network.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

import pytest

from worker.sources.fetch import Reply
from worker.sources.rightmove import (
    Rightmove,
    a_dwelling,
    as_listing,
    at,
    catches_in,
    kind_of,
    monthly,
    picture,
    results_in,
    search_url,
)
from worker.sources.sweep import _announceable

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "rightmove_e14_search.html"
PAGE = FIXTURE.read_text(encoding="utf-8")


def _page_of(rows: list[dict]) -> str:
    """A search page carrying exactly these rows, in the real page's shape."""

    payload = {
        "props": {"pageProps": {"searchResults": {"properties": rows, "pagination": {}}}}
    }
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></body></html>"
    )


@pytest.fixture(scope="module")
def caught() -> dict[str, object]:
    return {
        one.listing.external_id: one for one in catches_in(PAGE, "e14").caught
    }


# ── the page ─────────────────────────────────────────────────────────────

def test_every_listing_on_the_page_is_read() -> None:
    read = catches_in(PAGE, "E14")
    assert len(read.caught) == 6
    assert read.skipped == 0
    # The paging cursor has to survive, or a busy district is read once and
    # never paged.
    assert read.pagination.get("next") == "24"


def test_the_search_is_this_outcode_and_nothing_around_it() -> None:
    # `radius: 0.0` is why the searched district can be trusted as the
    # listing's district. If Rightmove ever widens that default, every listing
    # would be filed under a neighbouring outcode and subscribers would be
    # sent flats outside the area they chose — so it is asserted, not assumed.
    assert results_in(PAGE)["searchParameters"]["radius"] == "0.0"


def test_a_page_without_the_payload_yields_nothing_rather_than_raising() -> None:
    read = catches_in("<html><body>maintenance</body></html>", "E14")
    assert read.caught == [] and read.pagination == {} and read.skipped == 0


# ── one listing's fields ─────────────────────────────────────────────────

def test_a_flat_is_read_whole(caught: dict) -> None:
    one = caught["93625473"].listing
    assert one.source_key == "rightmove"
    assert one.url == "https://www.rightmove.co.uk/properties/93625473"
    assert one.bedrooms == 2
    assert one.property_type == "flat"
    assert one.postcode == "E14 0XU"
    assert one.postcode_district == "E14"
    assert one.floor_area_sqft == 703
    assert 100 <= (one.price_pcm or 0) <= 100_000
    # Rightmove lists through member agents only.
    assert one.is_landlord_direct is False
    assert one.lat is not None and one.lng is not None


def test_a_studio_is_zero_bedrooms_and_a_flat(caught: dict) -> None:
    # This project's own convention, and the portal already agrees — every
    # `propertySubType: "Studio"` on the live page had `bedrooms: 0`. The
    # bedroom slider reads the zero back as the word.
    one = caught["92456712"].listing
    assert one.bedrooms == 0
    assert one.property_type == "flat"


def test_a_terraced_house_is_a_house(caught: dict) -> None:
    # The filter offers four words, so "Terraced" has to answer a search for a
    # house. The portal's own word is kept.
    one = caught["93624564"].listing
    assert one.property_type == "house"
    assert one.raw["property_sub_type"] == "Terraced"


def test_an_address_with_no_outcode_still_lands_in_the_district(caught: dict) -> None:
    # "York Square, London" — a third of the addresses state no outcode, and
    # taking the district from the address would drop those listings entirely.
    one = caught["93624564"].listing
    assert one.title == "York Square, London"
    assert one.postcode is None
    assert one.postcode_district == "E14"


def test_an_outcode_only_address_gives_no_full_postcode(caught: dict) -> None:
    # Half a postcode is worse than none: the duplicate rule compares full
    # postcodes, and "E14" would make every 2-bed at one price the same flat.
    one = caught["92362062"].listing
    assert one.postcode is None
    assert one.postcode_district == "E14"


def test_no_stated_area_is_unknown_rather_than_zero(caught: dict) -> None:
    one = caught["92362062"].listing
    assert one.floor_area_sqft is None


def test_the_portals_own_first_seen_date_is_kept(caught: dict) -> None:
    one = caught["93625473"]
    assert one.first_listed is not None
    assert one.first_listed.tzinfo is not None


def test_a_promoted_listing_is_flagged(caught: dict) -> None:
    # Rightmove's featured and premium slots put a listing from last May at
    # the top of a newest-first page. It is stored like any other, but it must
    # not be read as the sort order.
    assert caught["93585705"].promoted is True
    assert caught["92362062"].promoted is False


# ── the pieces ───────────────────────────────────────────────────────────

def test_a_weekly_rent_is_converted_not_taken_at_face_value() -> None:
    # Read as monthly, a weekly figure is a quarter of the real rent — it
    # would pass the filter and waste somebody's evening.
    assert monthly({"amount": 2445, "frequency": "monthly"}) == 2445
    assert monthly({"amount": 500, "frequency": "weekly"}) == 2167
    assert monthly({"amount": 24000, "frequency": "yearly"}) == 2000


def test_an_unusable_price_is_refused() -> None:
    assert monthly(None) is None
    assert monthly({}) is None
    assert monthly({"amount": "ask"}) is None
    assert monthly({"amount": 50, "frequency": "monthly"}) is None
    assert monthly({"amount": 500_000, "frequency": "monthly"}) is None
    # An unknown frequency is refused rather than assumed to be monthly.
    assert monthly({"amount": 2000, "frequency": "per_fortnight"}) is None


# Every propertySubType Rightmove served across eight London outcodes on
# 26 September 2026. The mapping is asserted against the real vocabulary
# rather than an imagined one, because a type that falls through to None
# currently answers every filter.
@pytest.mark.parametrize(
    ("sub_type", "kind"),
    [
        ("Apartment", "flat"),
        ("Flat", "flat"),
        ("Studio", "flat"),
        ("Penthouse", "flat"),
        ("Maisonette", "flat"),
        ("Ground Maisonette", "flat"),
        ("Duplex", "flat"),
        ("House", "house"),
        ("Terraced", "house"),
        ("End of Terrace", "house"),
        ("Semi-Detached", "house"),
        ("Detached", "house"),
        ("Town House", "house"),
        ("House Share", "room"),
        ("Flat Share", "room"),
        ("Not Specified", None),
        # A houseboat is somewhere to live but it is neither a house nor a
        # flat, so it is stored with no type rather than filed as a house.
        ("House Boat", None),
    ],
)
def test_the_portals_own_words_map_to_the_four_the_filter_offers(
    sub_type: str, kind: str | None
) -> None:
    assert kind_of(sub_type) == kind


def test_a_share_is_a_room_even_though_its_name_says_house() -> None:
    # Matched on word boundaries rather than substrings. A substring match
    # filed both "House Share" and "House Boat" as houses.
    assert kind_of("House Share") == "room"
    assert kind_of("Semi-Detached House") == "house"
    assert kind_of(None) is None


def test_a_parking_space_is_not_a_home_and_is_not_stored() -> None:
    # Rightmove's rental channel carries these, and one came back as
    # `Parking` with `bedrooms: 0`. Left in, a £250 parking space answers a
    # studio search on both the bedroom count and the rent.
    assert a_dwelling("Parking") is False
    assert a_dwelling("Garage") is False
    assert a_dwelling("Block of Apartments") is False
    assert a_dwelling("Flat") is True
    assert a_dwelling("House Boat") is True

    space = {
        "id": 1,
        "propertySubType": "Parking",
        "bedrooms": 0,
        "price": {"amount": 250, "frequency": "monthly"},
        "displayAddress": "Canary Wharf, E14",
    }
    assert as_listing(space, "E14") is None


def test_a_skipped_row_is_counted_not_swallowed() -> None:
    read = catches_in(_page_of([
        {"id": 1, "propertySubType": "Parking", "bedrooms": 0,
         "price": {"amount": 250, "frequency": "monthly"}},
        {"id": 2, "propertySubType": "Flat", "bedrooms": 1,
         "price": {"amount": 1800, "frequency": "monthly"},
         "displayAddress": "Poplar, London, E14 6AB",
         "firstVisibleDate": "2026-09-26T10:00:00Z"},
    ]), "E14")
    assert [one.listing.external_id for one in read.caught] == ["2"]
    assert read.skipped == 1


def test_a_jpeg_is_preferred_to_a_first_position_png(caught: dict) -> None:
    # Same flat, and measured against the live CDN: 141KB as the PNG the agent
    # put first, 17KB as a JPEG. It is the subscriber's phone that pulls it.
    chosen = caught["93625281"].image
    assert chosen is not None
    assert chosen.endswith((".jpg", ".jpeg"))
    assert chosen.startswith("https://media.rightmove.co.uk")


def test_no_picture_is_none_rather_than_a_broken_url() -> None:
    assert picture(None) is None
    assert picture({"images": []}) is None
    # WhatsApp will not follow a relative path, and neither will a browser.
    assert picture({"images": [{"srcUrl": "/property-photo/x.jpeg"}]}) is None


def test_the_search_url_is_newest_first_and_pages() -> None:
    assert search_url("e14") == (
        "https://www.rightmove.co.uk/property-to-rent/E14.html?sortType=6"
    )
    assert search_url("E14", 24).endswith("?sortType=6&index=24")
    assert "index" not in search_url("E14", 0)


def test_a_missing_or_broken_timestamp_is_none() -> None:
    assert at("2026-09-26T20:08:05Z") == datetime(2026, 9, 26, 20, 8, 5, tzinfo=UTC)
    assert at(None) is None
    assert at("") is None
    assert at("last Tuesday") is None


def test_a_row_with_no_id_or_no_price_is_skipped() -> None:
    assert as_listing({"price": {"amount": 2000}}, "E14") is None
    assert as_listing({"id": 1}, "E14") is None
    assert as_listing({}, "E14") is None


# ── paging, and what gets announced ──────────────────────────────────────

class Pages:
    """A fetcher that serves canned pages and records what was asked for."""

    def __init__(self, *bodies: str) -> None:
        self.bodies = list(bodies)
        self.asked: list[str] = []

    def get(self, url: str, **_: object) -> Reply:
        self.asked.append(url)
        body = self.bodies[min(len(self.asked) - 1, len(self.bodies) - 1)]
        return Reply(status=200, body=body, wire=1000, impersonated="chrome124")


class Quiet:
    """A stage that swallows what it is told. The real one needs a database."""

    def log(self, *_: object, **__: object) -> None: ...
    def count(self, *_: object, **__: object) -> None: ...
    def set(self, *_: object, **__: object) -> None: ...
    def degrade(self, *_: object, **__: object) -> None: ...


def test_starting_to_watch_a_district_costs_one_page() -> None:
    # The whole reason this portal does not need OpenRent's read-through: on a
    # first look nothing will be announced, so paging through forty pages of
    # standing stock would be forty requests spent on listings nobody is told
    # about.
    pages = Pages(PAGE)
    harvest = Rightmove().harvest("E14", pages, Quiet(), None)  # type: ignore[arg-type]
    assert len(pages.asked) == 1
    assert harvest.pages == 1
    assert harvest.complete is True
    assert len(harvest.caught) == 6


def test_paging_stops_once_the_page_predates_the_watch() -> None:
    # Every listing in the fixture predates this, so one page is enough and
    # the run is complete.
    pages = Pages(PAGE)
    since = datetime(2030, 1, 1, tzinfo=UTC)
    harvest = Rightmove().harvest("E14", pages, Quiet(), since)  # type: ignore[arg-type]
    assert len(pages.asked) == 1
    assert harvest.complete is True


def test_a_district_still_newer_than_the_watch_pages_to_the_cap() -> None:
    # Everything on every page is newer than 2020, so the cap is what stops
    # it — and the run says so rather than settling on a partial read.
    pages = Pages(PAGE)
    since = datetime(2020, 1, 1, tzinfo=UTC)
    harvest = Rightmove(max_pages=3).harvest("E14", pages, Quiet(), since)  # type: ignore[arg-type]
    assert len(pages.asked) == 3
    assert harvest.pages == 3
    assert harvest.complete is False
    # The overlapping promoted slot is not counted twice.
    assert len(harvest.caught) == 6


def test_an_empty_district_is_complete_not_broken() -> None:
    pages = Pages("<html><body>no properties found</body></html>")
    harvest = Rightmove().harvest("ZZ99", pages, Quiet(), datetime(2020, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]
    assert harvest.caught == []
    assert harvest.complete is True


def test_nothing_is_announced_from_a_district_we_have_only_just_started_watching(
    caught: dict,
) -> None:
    portal = Rightmove()
    assert _announceable(portal, caught["93625473"], None) is False


def test_only_listings_newer_than_the_watch_are_announced(caught: dict) -> None:
    portal = Rightmove()
    one = caught["93625473"]
    assert one.first_listed is not None
    before = one.first_listed.replace(year=one.first_listed.year - 1)
    after = one.first_listed.replace(year=one.first_listed.year + 1)
    assert _announceable(portal, one, before) is True
    assert _announceable(portal, one, after) is False


def test_a_dated_portal_that_gave_no_date_announces_nothing(caught: dict) -> None:
    # Storing it is right; announcing it is not. A missing date from a portal
    # that normally publishes one is not evidence that the listing is new.
    import dataclasses

    portal = Rightmove()
    undated = dataclasses.replace(caught["93625473"], first_listed=None)
    assert _announceable(portal, undated, datetime(2020, 1, 1, tzinfo=UTC)) is False
