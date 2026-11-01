"""OnTheMarket's parser, its batching and the two silent failures it guards.

Every fixture is a genuine page the live site served on 1 November 2026, cut
down to the payload this reader reads and nothing else.

`onthemarket_batch_search.html` is one `se16 + se8 + e14` query with the
recency filter on, trimmed to eight result slots. Which eight is deliberate:
each is the only live example of something that has to keep working.

  * 20666315 — an ordinary flat, "Added today", address ending in an outcode;
  * 20666269 — `humanised-property-type: "Studio"` with `bedrooms: null`, the
    pair that has to become nought bedrooms rather than no bedroom count;
  * 20665660 — "House share", which contains a word that is not what it is;
  * an in-market advert slot, `"ad?": true` with an integer `id`, sitting
    fourth rather than at either end;
  * 20665579 — "Balfron Tower, 7 St Leonards Road, London E14 0UY", the one
    card in twelve that states a full postcode;
  * 20665608 — "Tabard House,Manciple Street, London(HD VIDEO)", which states
    neither an outcode nor a postcode and so needs its district read off its
    own page;
  * 20471231 — `reduced?: true` with "Reduced today";
  * 20529672 — `reduced?: true` with "Reduced yesterday".

`onthemarket_too_many_locations.html` is what the site answers when asked for
ten locations: HTTP 200, an empty result set and neither parameter echoed back.
It is the fixture for the one failure that looks exactly like a quiet market.

The listing pages carry the four filters a card cannot answer:

  * `onthemarket_details.html` — "Available as furnished or unfurnished" alone,
    the most common item on these pages and a trap for both the furnishing
    parser and the availability one;
  * `onthemarket_details_term.html` — the full vocabulary: a deposit, a
    contract length, a minimum term, a pets icon and a floor area;
  * `onthemarket_details_available_on.html` — "Availability date: 22 Dec 2026";
  * `onthemarket_details_no_district.html` — listing 20665608's own page, which
    is where its SE1 comes from.

Nothing here touches the network.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, date, datetime

import pytest

from worker.obs.log import Stage
from worker.sources.fetch import Refused, Reply
from worker.sources.onthemarket import (
    BATCH,
    SOURCE_KEY,
    OnTheMarket,
    a_dwelling,
    area_on,
    as_listing,
    available_in,
    catches_in,
    deposit_in,
    exclusive_on,
    fill,
    first_listed,
    furnished_in,
    honoured,
    is_advert,
    kind_of,
    letting_details,
    listing_url,
    monthly,
    months_in,
    next_page,
    pets_in,
    postcode_on,
    query_in,
    results_in,
    search_url,
    where_in,
)
from worker.sources.sweep import Catch, Memory

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PAGE = (FIXTURES / "onthemarket_batch_search.html").read_text(encoding="utf-8")
PAGE_TWO = (FIXTURES / "onthemarket_batch_page2.html").read_text(encoding="utf-8")
TOO_MANY = (FIXTURES / "onthemarket_too_many_locations.html").read_text(encoding="utf-8")
DETAILS = (FIXTURES / "onthemarket_details.html").read_text(encoding="utf-8")
TERM = (FIXTURES / "onthemarket_details_term.html").read_text(encoding="utf-8")
AVAILABLE_ON = (FIXTURES / "onthemarket_details_available_on.html").read_text(encoding="utf-8")
NO_DISTRICT = (FIXTURES / "onthemarket_details_no_district.html").read_text(encoding="utf-8")

# The day the fixtures were taken, so "Added today" is a fixed date here.
TODAY = date(2026, 11, 1)
BATCH_OF_THREE = ("SE16", "SE8", "E14")


class _Log(Stage):
    """A stage that records rather than writes."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.values: dict[str, object] = {}
        self.lines: list[tuple[str, str]] = []

    def count(self, name: str, delta: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + delta

    def set(self, name: str, value: object) -> None:
        self.values[name] = value

    def log(self, level: str, message: str, **ctx: object) -> None:
        self.lines.append((level, message))

    def degrade(self, reason: str, **ctx: object) -> None:
        self.lines.append(("degraded", reason))


def _reply(body: str, status: int = 200) -> Reply:
    return Reply(status=status, body=body, wire=len(body) // 5, impersonated="chrome124")


class _Pages:
    """A fetcher serving a scripted list of replies, recording what was asked."""

    def __init__(self, replies: list[Reply | Exception]) -> None:
        self.replies = replies
        self.asked: list[str] = []
        self.tolerated: list[tuple[int, ...]] = []

    def get(self, url: str, *, accept: str = "", tolerate: tuple[int, ...] = ()) -> Reply:
        self.asked.append(url)
        self.tolerated.append(tolerate)
        reply = self.replies[min(len(self.asked) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _reader(**over: object) -> OnTheMarket:
    """A reader that never actually waits out a shed page."""

    return OnTheMarket(shed_pause=0.0, **over)  # type: ignore[arg-type]


# ── the url, which is where the nine-location rule is enforced ────────────


def test_repeats_the_parameter_rather_than_listing_it():
    # A comma-separated list is accepted by the site and silently ignored,
    # which is why this is asserted character by character.
    url = search_url(BATCH_OF_THREE)
    assert url == (
        "https://www.onthemarket.com/to-rent/property/se16/"
        "?alt-location-ids=se8&alt-location-ids=e14&recently-added=24-hours"
    )
    assert "se8,e14" not in url


def test_asks_for_the_first_page_without_saying_so():
    # `?page=1` answers 404 on the live site; page one is the bare url.
    assert "page=" not in search_url(BATCH_OF_THREE, 1)
    assert search_url(BATCH_OF_THREE, 2).endswith("&page=2")


def test_refuses_to_ask_for_a_tenth_location():
    # The site would answer this 200 with nothing in it. Failing here is the
    # only way that stays visible.
    ten = tuple(f"E{n}" for n in range(1, 11))
    assert len(ten) == BATCH + 1
    with pytest.raises(ValueError, match="at most 9"):
        search_url(ten)
    search_url(ten[:BATCH])


# ── the two guards, against a 200 that means nothing ──────────────────────


def test_catches_the_page_the_site_sends_for_too_many_locations():
    # Genuinely what came back: no error, no redirect, an empty result set.
    assert results_in(TOO_MANY).get("list") in (None, [])
    assert query_in(TOO_MANY).get("altLocationIds") == []
    why = honoured(TOO_MANY, ("E1", "E2", "E3"))
    assert why is not None
    assert "24 hours" in why


def test_accepts_a_page_that_echoes_both_parameters():
    assert honoured(PAGE, BATCH_OF_THREE) is None
    assert honoured(PAGE_TWO, BATCH_OF_THREE) is None


def test_catches_a_page_that_dropped_one_location():
    why = honoured(PAGE, (*BATCH_OF_THREE, "N1"))
    assert why is not None
    assert "alt-location-ids" in why


def test_reads_the_recency_filter_back_off_every_page():
    # Not the count: a page that quietly widened to the whole district would
    # otherwise look like a district with a lot of new stock.
    widened = PAGE.replace('"recentlyAdded":"24-hours"', '"recentlyAdded":null')
    assert widened != PAGE
    why = honoured(widened, BATCH_OF_THREE)
    assert why is not None
    assert "24 hours" in why


# ── the cards ─────────────────────────────────────────────────────────────


def test_reads_every_listing_on_the_page_and_no_advert():
    read = catches_in(PAGE, today=TODAY)
    assert read.adverts == 1
    assert read.invalid == 0
    assert len(read.caught) == 7
    assert all(one.listing.source_key == SOURCE_KEY for one in read.caught)


def test_an_advert_slot_is_not_a_listing():
    cards = results_in(PAGE)["list"]
    adverts = [one for one in cards if is_advert(one)]
    assert len(adverts) == 1
    assert adverts[0]["ad?"] is True
    assert not isinstance(adverts[0]["id"], str)


def test_a_studio_is_nought_bedrooms_and_not_an_unknown_count():
    read = catches_in(PAGE, today=TODAY)
    studio = next(one for one in read.caught if one.listing.external_id == "20666269")
    assert studio.listing.bedrooms == 0
    assert studio.listing.property_type == "flat"


def test_a_house_share_is_a_room():
    read = catches_in(PAGE, today=TODAY)
    share = next(one for one in read.caught if one.listing.external_id == "20665660")
    assert share.listing.property_type == "room"


def test_the_one_card_with_a_full_postcode_keeps_it():
    read = catches_in(PAGE, today=TODAY)
    stated = next(one for one in read.caught if one.listing.external_id == "20665579")
    assert stated.listing.postcode == "E14 0UY"
    assert stated.listing.postcode_district == "E14"
    assert stated.listing.postcode_source == "portal"


def test_a_card_with_no_outcode_is_filed_nowhere_yet():
    read = catches_in(PAGE, today=TODAY)
    lost = next(one for one in read.caught if one.listing.external_id == "20665608")
    assert lost.listing.postcode_district is None
    assert lost.listing.postcode is None


def test_the_picture_is_the_cdn_variant_the_card_already_carries():
    read = catches_in(PAGE, today=TODAY)
    assert all(one.image and one.image.startswith("https://") for one in read.caught)


# ── the date, and the price cut that is not one ───────────────────────────


def test_added_today_is_the_end_of_today():
    assert first_listed("Added today", reduced=False, today=TODAY) == datetime(
        2026, 11, 1, 23, 59, 59, 999999, tzinfo=UTC
    )


def test_added_yesterday_is_the_end_of_yesterday():
    assert first_listed("Added yesterday", reduced=False, today=TODAY) == datetime(
        2026, 10, 31, 23, 59, 59, 999999, tzinfo=UTC
    )


@pytest.mark.parametrize("label", ["Reduced today", "Reduced yesterday"])
def test_a_price_cut_carries_no_date_at_all(label):
    # The label says when the price moved, not when the flat appeared, so a
    # flat reduced today may have been advertised since July. `_announceable`
    # refuses a dated portal's listing with no date, which is what keeps a
    # price cut from going out as a new flat.
    assert first_listed(label, reduced=True, today=TODAY) is None


def test_both_reduced_cards_on_the_page_are_undated():
    read = catches_in(PAGE, today=TODAY)
    reduced = {"20471231", "20529672"}
    for one in read.caught:
        if one.listing.external_id in reduced:
            assert one.first_listed is None
        else:
            assert one.first_listed is not None


def test_a_label_this_has_not_learned_is_not_guessed_at():
    # "Added < 7 days" appears only when the recency filter was lost, which
    # `honoured` catches first — this is the second line of the same defence.
    assert first_listed("Added < 7 days", reduced=False, today=TODAY) is None
    assert first_listed(None, reduced=False, today=TODAY) is None


# ── the smaller readers ───────────────────────────────────────────────────


def test_the_monthly_rent_is_read_and_not_converted():
    assert monthly("£2,445 pcm (£564 pw)") == 2445
    assert monthly("£5,400 pcm (£1,246 pw)") == 5400


def test_a_weekly_only_price_is_converted_rather_than_dropped():
    assert monthly("£500 pw") == 2167
    assert monthly("") is None
    assert monthly(None) is None


def test_the_address_is_read_for_a_postcode_then_an_outcode():
    assert where_in("Balfron Tower, 7 St Leonards Road, London E14 0UY") == (
        "E14 0UY",
        "E14",
    )
    assert where_in("Biscuit Walk, Bermondsey, London, SE16") == (None, "SE16")
    assert where_in("Tabard House,Manciple Street, London(HD VIDEO)") == (None, None)


def test_a_parking_space_is_not_somewhere_anybody_lives():
    assert not a_dwelling("Parking")
    assert not a_dwelling("Garage")
    assert a_dwelling("Flat")
    parking = {"id": "1", "price": "£250 pcm", "humanised-property-type": "Parking"}
    assert as_listing(parking) is None


def test_an_unrecognised_type_answers_every_filter_rather_than_the_wrong_one():
    assert kind_of("Flat") == "flat"
    assert kind_of("Apartment") == "flat"
    assert kind_of("Penthouse") == "flat"
    assert kind_of("Detached house") == "house"
    assert kind_of("Bedsit") == "room"
    assert kind_of("Flat share") == "room"
    assert kind_of("") is None


# ── the listing page ──────────────────────────────────────────────────────


def test_furnished_or_unfurnished_is_not_furnished():
    # The single most common item on these pages. Read as "furnished" it would
    # mis-file most of the source; `unknown` hands the listing to everybody.
    assert letting_details(DETAILS) == ["Available as furnished or unfurnished"]
    assert furnished_in(letting_details(DETAILS)) == "unknown"


def test_a_stated_furnishing_is_taken():
    assert furnished_in(letting_details(TERM)) == "furnished"
    assert furnished_in(["Unfurnished"]) == "unfurnished"
    assert furnished_in(["Part furnished"]) == "part"
    assert furnished_in([]) == "unknown"


def test_available_now_is_today_and_the_furnishing_line_is_not_a_date():
    assert available_in(letting_details(TERM), today=TODAY) == TODAY
    # The trap: "Available as furnished or unfurnished" starts with the word an
    # availability parser looks for and is not an availability at all.
    assert available_in(letting_details(DETAILS), today=TODAY) is None


def test_a_stated_availability_date_is_read():
    assert available_in(letting_details(AVAILABLE_ON), today=TODAY) == date(2026, 12, 22)


def test_the_minimum_term_wins_over_the_contract_length():
    assert months_in(letting_details(TERM)) == 12
    assert months_in(["Contract length: 18 months"]) == 18
    assert months_in(["Contract length: 18 months", "Min. term: 6 months"]) == 6
    assert months_in(["Long term let"]) is None


def test_the_deposit_is_the_amount_stated():
    assert deposit_in(letting_details(TERM)) == 2740.40
    assert deposit_in(letting_details(AVAILABLE_ON)) == 2504.0
    assert deposit_in(letting_details(DETAILS)) is None


def test_pets_are_true_or_unstated_and_never_false():
    assert pets_in(TERM) is True
    assert pets_in(DETAILS) is None


def test_the_full_postcode_comes_off_the_data_layer():
    assert postcode_on(TERM) == "SE16 4FB"
    assert postcode_on(NO_DISTRICT) == "SE1 4DL"


def test_a_postcode_from_another_district_is_refused():
    # The page carries panels for similar properties; a postcode under the
    # wrong outward code is a neighbour's and reads as fact in an alert.
    assert postcode_on(TERM, "SE16") == "SE16 4FB"
    assert postcode_on(TERM, "E14") is None


def test_the_floor_area_is_already_in_square_feet():
    assert area_on(TERM) == 401
    assert area_on(DETAILS) is None


def test_whether_the_listing_is_on_the_other_portals_yet_is_kept():
    assert exclusive_on(TERM) is False


def test_the_page_fills_in_what_the_card_could_not_say():
    card = next(
        one for one in catches_in(PAGE, today=TODAY).caught
        if one.listing.external_id == "20666315"
    )
    filled = fill(card.listing, TERM, today=TODAY)
    assert filled.furnished == "furnished"
    assert filled.available_from == TODAY
    assert filled.min_tenancy_months == 12
    assert filled.deposit_pcm == 2740.40
    assert filled.pets_allowed is True
    assert filled.floor_area_sqft == 401
    assert filled.raw["exclusive"] is False
    # The card said E14 and this page says SE16, so the postcode is refused
    # rather than written across districts.
    assert filled.postcode is None


def test_a_page_that_says_nothing_overwrites_nothing():
    card = next(
        one for one in catches_in(PAGE, today=TODAY).caught
        if one.listing.external_id == "20665579"
    )
    filled = fill(card.listing, DETAILS, today=TODAY)
    assert filled.postcode == "E14 0UY"
    assert filled.furnished == "unknown"
    assert filled.min_tenancy_months is None


# ── the batching, which is the whole point of this reader ─────────────────


def test_nine_districts_cost_one_request():
    reader = _reader()
    nine = ("SE16", "SE8", "E14", "E1", "SE1", "SE15", "SE17", "N1", "NW1")
    reader.plan(nine)
    pages = _Pages([_reply(PAGE_TWO)])
    stage = _Log()

    harvests = [reader.harvest(one, pages, stage) for one in nine]

    assert len(pages.asked) == 1
    assert pages.asked[0].count("alt-location-ids=") == BATCH - 1
    # The pages belong to the district that triggered the request, so the run's
    # page count is the number fetched and not nine times it.
    assert [one.pages for one in harvests] == [1, 0, 0, 0, 0, 0, 0, 0, 0]


def test_a_tenth_district_starts_a_second_request():
    reader = _reader()
    ten = (
        "SE16", "SE8", "E14", "E1", "SE1", "SE15", "SE17", "N1", "NW1", "SW11",
    )
    reader.plan(ten)
    pages = _Pages([_reply(PAGE_TWO)])
    for one in ten:
        reader.harvest(one, pages, _Log())
    assert len(pages.asked) == 2
    assert pages.asked[1].count("alt-location-ids=") == 0


def test_each_listing_is_filed_under_its_own_district():
    reader = _reader()
    reader.plan(BATCH_OF_THREE)
    pages = _Pages([_reply(PAGE), _reply(PAGE_TWO), _reply(NO_DISTRICT)])
    stage = _Log()

    mine = {one: reader.harvest(one, pages, stage) for one in BATCH_OF_THREE}

    filed = {
        district: sorted(one.listing.external_id for one in harvest.caught)
        for district, harvest in mine.items()
    }
    # Four in E14 across the two pages, one in SE16, and none at all in SE8 —
    # which is the ordinary case and not a fault. A batch of nine outcodes is
    # asked as one query precisely so that a quiet district costs nothing.
    assert filed["E14"] == ["20665533", "20665579", "20665660", "20666315"]
    assert filed["SE16"] == ["20471231"]
    assert filed["SE8"] == []
    # Three the batch does not own: SE1, E1, and the card whose address states
    # no district at all. Each is read again by the batch that owns it, or by
    # nobody when nobody subscribes to it — filing them here would judge them
    # against another district's watch.
    assert stage.counts.get("outside_the_batch") == 3


def test_the_pages_of_one_batch_are_followed_to_the_end():
    reader = _reader()
    reader.plan(("SE16",))
    pages = _Pages([_reply(PAGE), _reply(PAGE_TWO), _reply(NO_DISTRICT)])
    harvest = reader.harvest("SE16", pages, _Log())
    assert next_page(PAGE) and not next_page(PAGE_TWO)
    assert harvest.pages == 2
    assert harvest.complete is True
    # Both pages were read; only one of their eight listings is in SE16.
    assert [one.listing.external_id for one in harvest.caught] == ["20471231"]


def test_plan_starts_a_run_rather_than_adding_to_the_last_one():
    reader = _reader()
    reader.plan(BATCH_OF_THREE)
    first = _Pages([_reply(PAGE_TWO)])
    reader.harvest("SE16", first, _Log())
    assert len(first.asked) == 1

    reader.plan(BATCH_OF_THREE)
    second = _Pages([_reply(PAGE_TWO)])
    reader.harvest("SE16", second, _Log())
    # Asked again rather than answered from the first sweep's page.
    assert len(second.asked) == 1


# ── the 404 that is a shed and not an answer ──────────────────────────────


def test_a_shed_page_is_retried_directly_and_not_through_the_proxy():
    reader = _reader()
    reader.plan(("SE16",))
    pages = _Pages([_reply("", 404), _reply(PAGE_TWO)])
    stage = _Log()

    harvest = reader.harvest("SE16", pages, stage)

    assert len(pages.asked) == 2
    assert pages.asked[0] == pages.asked[1]
    # `tolerate` is what keeps `Fetcher.get` from spending a second fingerprint
    # or a paid exit address on being told 404 again.
    assert pages.tolerated == [(404,), (404,)]
    assert stage.counts["shed"] == 1
    assert stage.counts["shed_retried"] == 1
    assert harvest.complete is True


def test_a_run_stops_waiting_once_it_has_spent_its_budget():
    reader = OnTheMarket(shed_pause=10.0, shed_budget=10.0)
    reader.plan(("SE16",))
    pages = _Pages([_reply("", 404)])
    stage = _Log()

    harvest = reader.harvest("SE16", pages, stage)

    # One wait of ten seconds fits the budget; the second would not.
    assert len(pages.asked) == 2
    assert harvest.complete is False
    assert harvest.caught == []
    assert any("waiting budget" in line for _, line in stage.lines)


def test_an_unhonoured_page_degrades_rather_than_reporting_an_empty_district():
    reader = _reader()
    reader.plan(BATCH_OF_THREE)
    pages = _Pages([_reply(TOO_MANY)])
    stage = _Log()

    harvest = reader.harvest("SE16", pages, stage)

    assert harvest.complete is False
    assert harvest.caught == []
    assert any(level == "degraded" for level, _ in stage.lines)


# ── the eight per cent with no district on the card ───────────────────────


def test_a_districtless_listing_has_its_district_read_off_its_own_page():
    reader = _reader()
    reader.plan(("SE1",))
    pages = _Pages([_reply(PAGE), _reply(PAGE_TWO), _reply(NO_DISTRICT)])
    stage = _Log()

    harvest = reader.harvest("SE1", pages, stage)

    # 20666269 states SE1 in its address; 20665608 states nothing and got its
    # district from `onthemarket_details_no_district.html`.
    filed = sorted(one.listing.external_id for one in harvest.caught)
    assert filed == ["20665608", "20666269"]
    resolved = next(
        one for one in harvest.caught if one.listing.external_id == "20665608"
    )
    assert resolved.listing.postcode_district == "SE1"
    # The postcode itself is deliberately left for the engine's own order: the
    # page is read again by `enrich` for a district somebody subscribes to, and
    # a value written here would pre-empt both that and `_derive_postcodes`.
    assert resolved.listing.postcode is None
    assert stage.counts["district_resolved"] == 1


def test_a_district_already_resolved_is_not_asked_about_again():
    reader = _reader()
    reader.plan(("SE1",))
    pages = _Pages([_reply(PAGE), _reply(PAGE_TWO)])
    remembered: dict[str, str | None] = {}
    memory = Memory(
        stored=lambda ids: frozenset(),
        resolved=lambda ids: {"20665608": "SE1"},
        remember=remembered.update,
    )

    harvest = reader.harvest("SE1", pages, _Log(), None, memory)

    # Two search pages and no listing page at all: 0053 is what happens without
    # this, which is a third of a search re-resolved on every run, for ever.
    assert len(pages.asked) == 2
    assert sorted(one.listing.external_id for one in harvest.caught) == [
        "20665608",
        "20666269",
    ]
    assert remembered == {}


def test_what_this_pass_resolved_is_written_down():
    reader = _reader()
    reader.plan(("SE1",))
    pages = _Pages([_reply(PAGE), _reply(PAGE_TWO), _reply(NO_DISTRICT)])
    remembered: dict[str, str | None] = {}
    memory = Memory(
        stored=lambda ids: frozenset(),
        resolved=lambda ids: {},
        remember=remembered.update,
    )

    reader.harvest("SE1", pages, _Log(), None, memory)

    assert remembered == {"20665608": "SE1"}


def test_an_unreadable_listing_page_costs_the_listing_and_not_the_run():
    reader = _reader()
    reader.plan(("SE1",))
    pages = _Pages([_reply(PAGE), _reply(PAGE_TWO), Refused("403")])
    stage = _Log()

    harvest = reader.harvest("SE1", pages, stage)

    # The card that stated SE1 itself is unaffected; only the one that needed
    # a page is lost, and the run reports a complete read of the window.
    assert [one.listing.external_id for one in harvest.caught] == ["20666269"]
    assert harvest.complete is True
    assert stage.counts["district_unreachable"] == 1


def test_resolving_has_a_budget_of_its_own():
    # It is spent before the engine knows whether anybody subscribes to the
    # district, so it is the one detail traffic this reader cannot gate on
    # `wanted` — which is why it is capped separately.
    reader = OnTheMarket(shed_pause=0.0, resolve_budget=0)
    reader.plan(("SE1",))
    pages = _Pages([_reply(PAGE), _reply(PAGE_TWO)])
    stage = _Log()

    reader.harvest("SE1", pages, stage, None, None)

    assert len(pages.asked) == 2
    assert stage.counts["district_over_budget"] == 1


# ── enrich, and the page it does not fetch twice ──────────────────────────


def test_enrich_reuses_the_page_the_district_lookup_already_read():
    reader = _reader()
    reader.plan(("SE1",))
    pages = _Pages([_reply(PAGE), _reply(PAGE_TWO), _reply(NO_DISTRICT)])
    reader.harvest("SE1", pages, _Log(), None, None)
    before = len(pages.asked)

    catch = Catch(
        listing=as_listing(
            {
                "id": "20665608",
                "price": "£2,000 pcm",
                "humanised-property-type": "Flat",
                "address": "Tabard House, London, SE1",
            }
        )
    )
    assert reader.postcode_for(catch, pages, _Log()) == "SE1 4DL"
    assert len(pages.asked) == before


def test_enrich_stops_at_its_budget_rather_than_at_nothing():
    reader = OnTheMarket(shed_pause=0.0, detail_budget=1)
    stage = _Log()
    pages = _Pages([_reply(TERM)])
    catch = Catch(
        listing=as_listing(
            {
                "id": "20663462",
                "price": "£2,375 pcm",
                "humanised-property-type": "Studio",
                "address": "Biscuit Walk, London, SE16",
            }
        )
    )
    first = reader.enrich(catch, pages, stage)
    assert first.listing.furnished == "furnished"
    second = reader.enrich(catch, pages, stage)
    assert second is catch
    assert stage.counts["detail_over_budget"] == 1


def test_today_is_londons_today_and_not_the_servers(monkeypatch):
    # The server runs on UTC and the site writes for London, so through BST the
    # hour after midnight in London is the previous day in UTC. A flat posted at
    # 00:30 on a July morning would be filed as yesterday's, and for a district
    # whose watch settled that day `_announceable` would then refuse it.
    import worker.sources.onthemarket as otm

    bst_small_hours = datetime(2026, 7, 14, 23, 30, tzinfo=UTC)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return bst_small_hours.astimezone(tz) if tz else bst_small_hours

    monkeypatch.setattr(otm, "datetime", _Clock)
    # 23:30 UTC on the 14th is 00:30 on the 15th in London, and the site means
    # the 15th by "today".
    assert otm.today_in_london() == date(2026, 7, 15)


def test_the_reader_answers_the_portal_protocol():
    reader = OnTheMarket()
    assert reader.key == SOURCE_KEY
    assert reader.dated is True
    assert reader.batch == BATCH
    # Never a region reader: the whole city is this reader with every district
    # enabled, which is why there is no second job for it.
    assert reader.regions == ()
    assert listing_url("20663462") == "https://www.onthemarket.com/details/20663462/"
