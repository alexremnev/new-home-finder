"""SpareRoom's parser and its paging rule, against pages the real site served.

`tests/fixtures/spareroom_london_search.html` is a genuine Bermondsey area page
taken on 9 October 2026, trimmed to five cards. Which five is deliberate — each
one is the only live example of something that has to keep working:

  * 18363867 — `brand="featured"`, so the one card the engine may not page on,
    and a "3 doubles" descriptor, which is still one room to the tenant;
  * 18456083 — a future availability date on the *card*, "21st Dec 2026", and
    no bills-included span;
  * 18454881 — `£190 pw`, the per-week rent that has to become £823 pcm;
  * 17542407 — `£692 pw` with a "1 bed flat" descriptor, so the one card in the
    set that is a whole property rather than a room;
  * 18364758 — `property-type="property"`, a value none of the four filter
    words covers, beside a room descriptor that decides the type anyway.

`tests/fixtures/spareroom_advert.html` is advert 18454055's own page from the
same day, trimmed to the `feature--*` sections and the coordinates block.

Nothing here touches the network.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, date, datetime, timedelta

import pytest

from worker.obs.log import Stage
from worker.sources.fetch import Fetcher, Refused, Reply
from worker.sources.spareroom import (
    PAGES_PER_RUN,
    SOURCE_KEY,
    WINDOW,
    SpareRoom,
    all_let,
    as_listing,
    available_in,
    bills_of,
    catches_in,
    coords_in,
    deposit_of,
    fill,
    first_listed,
    furnished_of,
    landlord_direct,
    let_of,
    listing_url,
    monthly,
    months_of,
    pets_of,
    picture,
    search_url,
    sections_in,
    total_in,
    when,
)
from worker.sources.sweep import Catch, _enrich, _pin_direct

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PAGE = (FIXTURES / "spareroom_london_search.html").read_text(encoding="utf-8")
ADVERT = (FIXTURES / "spareroom_advert.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def caught() -> dict[str, Catch]:
    return {one.listing.external_id: one for one in catches_in(PAGE).caught}


# ── the urls robots.txt leaves open ──────────────────────────────────────


def test_the_search_url_is_a_path_with_no_query_string() -> None:
    # Every filter and every sort this site understands is disallowed as a
    # query parameter, so a `?` here would be the bug. See the module note.
    assert search_url() == "https://www.spareroom.co.uk/flatshare/london"
    assert "?" not in search_url("london", 7)
    assert search_url("london", 7).endswith("/flatshare/london/page7")


def test_page_one_has_no_page_suffix() -> None:
    assert search_url("london", 1) == search_url("london", 0)
    assert search_url("london", 1) == search_url("london")


def test_the_click_tracker_is_stripped_off_a_listing_url() -> None:
    # The href on a featured card carries `?listing_click=1`, and
    # `data-listing-url` is a `fad_click.pl?…&search_id=` tracker that
    # robots.txt disallows outright. Only the plain path is followed.
    assert listing_url("/flatshare/london/rotherhithe/16281029?listing_click=1") == (
        "https://www.spareroom.co.uk/flatshare/london/rotherhithe/16281029"
    )


def test_a_url_outside_flatshare_is_refused() -> None:
    assert listing_url("/flatshare/fad_click.pl?fad_id=1") is not None
    assert listing_url("https://evil.example/flatshare/x") is None
    assert listing_url("/pro/whatever") is None


# ── reading the cards ────────────────────────────────────────────────────


def test_every_card_on_the_page_is_read(caught: dict[str, Catch]) -> None:
    read = catches_in(PAGE)
    assert len(caught) == 5
    assert read.invalid == 0
    assert read.skipped == 0


def test_the_stated_total_survives() -> None:
    assert total_in(PAGE) == 383
    assert total_in("<p>no such block</p>") is None


def test_every_card_is_counted_exactly_once() -> None:
    # `cards` is what `harvest` reads to tell "past the end of the area" from
    # "this page had nothing usable on it", so the three buckets have to add up.
    read = catches_in(PAGE)
    assert read.cards == len(read.caught) + read.skipped + read.invalid == 5
    assert catches_in("<html>no cards at all</html>").cards == 0


def test_a_weekly_rent_is_converted_to_the_calendar_month() -> None:
    # £190pw is £823pcm, not £190. `ad-rate-normalised` does not do this:
    # measured on 8 October 2026 it echoed "£347 pw" back unchanged.
    assert monthly("&pound;190", "pw") == 823
    assert monthly("&pound;1,500", "pcm") == 1500


def test_the_weekly_card_is_stored_at_its_monthly_rent(
    caught: dict[str, Catch],
) -> None:
    assert caught["18454881"].listing.price_pcm == 823
    assert caught["17542407"].listing.price_pcm == 2999


def test_a_rent_that_cannot_be_read_is_refused() -> None:
    assert monthly("POA", "pcm") is None
    assert monthly("&pound;0", "pcm") is None


# ── what is being let, which is the decision this reader rests on ────────


def test_a_room_is_a_room_and_one_bedroom() -> None:
    # Not the card's `property-type`, which is the *building*. Storing a room
    # as a flat would send nothing but rooms to somebody who ticked Flat and
    # House precisely to exclude them.
    assert let_of("Double room") == ("room", 1)
    assert let_of("Single room") == ("room", 1)


def test_several_rooms_going_is_still_one_room_to_the_tenant() -> None:
    assert let_of("3 doubles") == ("room", 1)
    assert let_of("2 doubles") == ("room", 1)


def test_a_whole_property_keeps_its_bedroom_count_and_asks_the_card_its_type() -> None:
    # A kind of None means "ask the card", which is the only thing that knows
    # a flat from a house.
    assert let_of("1 bed flat") == (None, 1)
    assert let_of("2 bed flat") == (None, 2)


def test_a_studio_is_nought_bedrooms() -> None:
    # Migration 0042: a studio is a flat with no separate bedroom, and the
    # zero carries the meaning.
    assert let_of("Studio") == (None, 0)


def test_the_whole_property_card_is_a_flat_rather_than_a_room(
    caught: dict[str, Catch],
) -> None:
    whole = caught["17542407"].listing
    assert whole.property_type == "flat"
    assert whole.bedrooms == 1

    room = caught["18363867"].listing
    assert room.property_type == "room"
    assert room.bedrooms == 1


def test_an_unrecognised_card_type_does_not_stop_a_room_being_a_room(
    caught: dict[str, Catch],
) -> None:
    # 18364758's card says `property-type="property"`, which answers no filter.
    # The descriptor says "Double room", and the descriptor decides.
    assert caught["18364758"].listing.property_type == "room"


def test_a_descriptor_with_nothing_left_in_it_is_not_a_listing() -> None:
    assert as_listing(_card(room="(NOW LET)")) is None


# ── the fields the card does answer ──────────────────────────────────────


def test_the_outcode_comes_off_the_card_and_not_off_the_search(
    caught: dict[str, Catch],
) -> None:
    # This reader sweeps the city, so the card is the only thing that says
    # where a listing is — and one area page genuinely spans two outcodes.
    assert caught["18456083"].listing.postcode_district == "SE1"
    assert caught["18363867"].listing.postcode_district == "SE16"


def test_a_card_with_no_outcode_is_refused() -> None:
    assert as_listing(_card(postcode="")) is None
    assert as_listing(_card(postcode="Bermondsey")) is None


def test_no_full_postcode_is_ever_claimed(caught: dict[str, Catch]) -> None:
    # SpareRoom publishes none anywhere, on either page. One is derived from
    # the advert's coordinates later and marked `derived`; see 0056.
    assert all(one.listing.postcode is None for one in caught.values())


def test_an_availability_date_is_read_off_the_card() -> None:
    assert available_in("Double room - Available 1st Nov 2026") == date(2026, 11, 1)
    assert available_in("2 doubles - Available now") == date.today()


def test_a_doubled_available_still_reads_as_the_date_not_as_now() -> None:
    # One live card rendered "- Available Available now" and another
    # "- Available 1st Nov 2026". A "now" test before the date would have
    # called the second one available today.
    assert available_in("Double room - Available Available now") == date.today()
    assert when("Available 21st Dec 2026") == date(2026, 12, 21)


def test_an_unreadable_date_is_none_rather_than_today() -> None:
    assert when("soon") is None
    assert when("31st Febtober 2026") is None
    assert when("") is None


def test_bills_included_is_true_or_unstated_but_never_false_from_a_card(
    caught: dict[str, Catch],
) -> None:
    # The span is there or it is not, and its absence is "not stated". Only
    # the advert's own page can say No — see `bills_of`.
    assert caught["18363867"].listing.bills_included is True
    assert caught["18456083"].listing.bills_included is None


def test_an_agency_is_not_the_landlord() -> None:
    assert landlord_direct("agent") is False
    assert landlord_direct("live out landlord") is True
    assert landlord_direct("current flatmate") is True
    assert landlord_direct("former flatmate") is True


def test_a_role_nobody_has_seen_is_unstated_rather_than_guessed() -> None:
    # The filter is "not an agency", and a word this reader does not know is
    # not evidence either way.
    assert landlord_direct("relocation service") is None
    assert landlord_direct("") is None
    assert landlord_direct(None) is None


def test_only_the_featured_card_counts_as_promoted(
    caught: dict[str, Catch],
) -> None:
    # `bold` and `boosted` are paid upgrades too, but ten of eleven cards on a
    # live page carry one, so calling those promoted would say nothing.
    assert caught["18363867"].promoted is True
    assert all(
        one.promoted is False
        for key, one in caught.items()
        if key != "18363867"
    )


def test_a_protocol_relative_photo_becomes_a_url_a_phone_can_fetch() -> None:
    # Telegram and WhatsApp fetch the picture themselves and will not follow
    # `//photos2.…`. See worker.ingest.photo for the same rule.
    card = _card(image="//photos2.spareroom.co.uk/images/x.jpg?width=400")
    assert picture(card) == "https://photos2.spareroom.co.uk/images/x.jpg?width=400"


def test_no_photo_is_none_rather_than_a_broken_url() -> None:
    assert picture(_card(image="data:image/gif;base64,AAAA")) is None
    assert picture("<li>nothing here</li>") is None


def test_a_wanted_card_is_not_a_listing() -> None:
    # `type="wanted"` is somebody looking for a room, not a room going. It
    # carries no rent and would be stored as something nobody can rent.
    assert as_listing(_card(kind="wanted")) is None


# ── the date the announcing rule rests on ────────────────────────────────


def test_days_old_becomes_the_end_of_that_day() -> None:
    today = date(2026, 10, 9)
    assert first_listed("0", today=today) == datetime(
        2026, 10, 9, 23, 59, 59, 999999, tzinfo=UTC
    )
    assert first_listed("2", today=today).date() == date(2026, 10, 7)


def test_a_missing_day_count_leaves_the_listing_undated() -> None:
    assert first_listed(None) is None
    assert first_listed("") is None
    assert first_listed("ages") is None


# ── the advert's own page ────────────────────────────────────────────────


def test_the_feature_sections_are_read_as_pairs() -> None:
    found = sections_in(ADVERT)
    assert found["availability"]["minimum term"] == "4 months"
    assert found["extra-cost"]["deposit"] == "£550.00"
    assert found["amenities"]["furnishings"] == "Furnished"


def test_the_question_mark_is_not_part_of_the_label() -> None:
    assert "bills included" in sections_in(ADVERT)["extra-cost"]
    assert "pets suitable" in sections_in(ADVERT)["household-preferences"]


def test_the_three_filters_the_card_cannot_answer_come_off_the_advert(
    caught: dict[str, Catch],
) -> None:
    filled = fill(caught["18363867"].listing, ADVERT)
    assert filled is not None
    assert filled.furnished == "furnished"
    assert filled.pets_allowed is False
    assert filled.min_tenancy_months == 4


def test_the_advert_also_gives_the_deposit_and_the_coordinates(
    caught: dict[str, Catch],
) -> None:
    filled = fill(caught["18363867"].listing, ADVERT)
    assert filled is not None
    assert filled.deposit_pcm == 550.0
    # The only coordinates this site states, and so the only route to a
    # postcode on it.
    assert (round(filled.lat, 4), round(filled.lng, 4)) == (51.4933, -0.0563)


def test_the_household_s_own_pets_are_not_the_answer_to_the_filter() -> None:
    # `current-household` asks "Any pets?", meaning the flatmates'. Reading
    # that would tell somebody with a cat that a flat was fine because
    # somebody else's cat lives there.
    assert pets_of({"pets suitable": "Yes"}) is True
    assert pets_of({"any pets": "Yes"}) is None


def test_bills_included_some_is_neither_yes_nor_no() -> None:
    # Three-valued on the page, two-valued in the contract. "Some" is not an
    # answer to "are bills in the rent", so it is left unstated.
    assert bills_of({"bills included": "Yes"}) is True
    assert bills_of({"bills included": "No"}) is False
    assert bills_of({"bills included": "Some"}) is None


def test_a_minimum_term_of_none_is_no_minimum() -> None:
    assert months_of({"minimum term": "6 months"}) == 6
    assert months_of({"minimum term": "None"}) is None
    assert months_of({}) is None


def test_a_deposit_of_nothing_is_nought_rather_than_unstated() -> None:
    # Seen live: "Deposit £0.00". A landlord asking for no deposit is an
    # answer, and a useful one.
    assert deposit_of({"deposit": "£0.00"}) == 0.0
    assert deposit_of({"deposit": "£1,269.00"}) == 1269.0
    assert deposit_of({}) is None


def test_the_furnishing_words_map_to_the_three_the_filter_offers() -> None:
    assert furnished_of({"furnishings": "Furnished"}) == "furnished"
    assert furnished_of({"furnishings": "Unfurnished"}) == "unfurnished"
    assert furnished_of({"furnishings": "Part furnished"}) == "part"
    assert furnished_of({}) == "unknown"


def test_part_furnished_is_not_read_as_furnished() -> None:
    # "part furnished" contains "furnished", so the order of the patterns is
    # what makes this right.
    assert furnished_of({"furnishings": "part-furnished"}) == "part"


def test_an_advert_with_one_room_left_is_still_worth_sending() -> None:
    # A live advert listed "£998 pcm (NOW LET)" above "£998 pcm double", so one
    # of its two rooms was still going.
    page = _advert_prices([("£998 pcm", "(NOW LET)"), ("£998 pcm", "double")])
    assert all_let(page) is False


def test_an_advert_with_every_room_gone_is_dropped(
    caught: dict[str, Catch],
) -> None:
    page = _advert_prices([("£998 pcm", "(NOW LET)"), ("£700 pcm", "single (now let)")])
    assert all_let(page) is True
    assert fill(caught["18363867"].listing, page) is None


def test_a_page_with_no_price_section_is_not_treated_as_let() -> None:
    # Absence of evidence. Dropping every listing whose markup moved would be
    # a silent outage rather than a parse error.
    assert all_let("<html><body>nothing</body></html>") is False


def test_no_coordinates_is_none_rather_than_nought() -> None:
    assert coords_in("<script>var x = 1;</script>") is None


# ── the rotating window ──────────────────────────────────────────────────


def test_a_full_cycle_covers_every_page_of_the_window() -> None:
    # The rule the whole reader rests on: ten consecutive five-minute runs
    # read pages 1..WINDOW between them and nothing twice.
    portal = SpareRoom()
    start = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    runs = WINDOW // PAGES_PER_RUN
    seen: list[int] = []
    for nth in range(runs):
        at = start + timedelta(minutes=5 * nth)
        offset = portal._offset(at)
        seen += [portal._page(offset + page) for page in range(PAGES_PER_RUN)]
    assert sorted(seen) == list(range(1, WINDOW + 1))


def test_two_runs_in_the_same_five_minute_slot_read_the_same_slice() -> None:
    # Which is harmless and deliberate: the advisory lock means two runs of
    # one job cannot overlap anyway, and the slice is a function of the clock
    # rather than of how many times it has been asked for.
    portal = SpareRoom()
    at = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    assert portal._offset(at) == portal._offset(at + timedelta(seconds=299))
    assert portal._offset(at) != portal._offset(at + timedelta(seconds=300))


def test_the_window_never_runs_past_the_feeds_last_page() -> None:
    portal = SpareRoom(window=500)
    assert max(portal._page(one) for one in range(2000)) <= 100


def test_starting_to_watch_costs_one_page() -> None:
    # Nothing read on the first run can be announced — `_announceable`
    # refuses everything until the watch is settled — so reading six pages to
    # throw five away is five pages wasted.
    get = _Pages()
    harvest = SpareRoom().harvest("london", get, _Log(), None, None)
    assert harvest.pages == 1
    assert len(get.asked) == 1


def test_a_settled_watch_reads_the_whole_slice() -> None:
    get = _Pages()
    harvest = SpareRoom().harvest(
        "london", get, _Log(), datetime(2026, 10, 9, tzinfo=UTC), None
    )
    assert harvest.pages == PAGES_PER_RUN
    assert len(get.asked) == PAGES_PER_RUN


def test_the_run_is_always_reported_complete() -> None:
    # "Complete" means "read back to the watermark", and this reader never
    # claims to have. Saying otherwise would degrade every run and never move
    # `swept_at`, which `stale_watches` then reads as a permanent gap — the
    # trap openrent fell into.
    harvest = SpareRoom().harvest(
        "london", _Pages(), _Log(), datetime(2026, 10, 9, tzinfo=UTC), None
    )
    assert harvest.complete is True


def test_an_empty_page_ends_the_slice_without_failing_the_run() -> None:
    get = _Pages(bodies=[PAGE, "<html><body>no cards</body></html>", PAGE])
    harvest = SpareRoom().harvest(
        "london", get, _Log(), datetime(2026, 10, 9, tzinfo=UTC), None
    )
    assert harvest.pages == 2
    assert harvest.complete is True


def test_the_first_refusal_of_a_run_is_raised_to_the_engine() -> None:
    # The engine counts refusals, decides when to stop asking and degrades a
    # run that read nothing at all. Swallowing it here would make a blocked
    # reader look like a quiet one.
    with pytest.raises(Refused):
        SpareRoom().harvest(
            "london", _Refusing(), _Log(), datetime(2026, 10, 9, tzinfo=UTC), None
        )


def test_a_refusal_part_way_through_keeps_what_was_read() -> None:
    get = _Pages(bodies=[PAGE, PAGE], then_refuse=True)
    harvest = SpareRoom().harvest(
        "london", get, _Log(), datetime(2026, 10, 9, tzinfo=UTC), None
    )
    assert harvest.pages == 2
    assert harvest.caught


def test_duplicate_ids_across_a_slice_are_stored_once() -> None:
    # The front of this feed barely moves and the window wraps, so one run can
    # genuinely see the same advert twice.
    get = _Pages(bodies=[PAGE, PAGE])
    harvest = SpareRoom().harvest(
        "london", get, _Log(), datetime(2026, 10, 9, tzinfo=UTC), None
    )
    ids = [one.listing.external_id for one in harvest.caught]
    assert len(ids) == len(set(ids)) == 5


# ── the engine hooks ─────────────────────────────────────────────────────


def test_the_reader_is_a_region_reader_writing_one_source_key() -> None:
    portal = SpareRoom()
    assert portal.regions == ("london",)
    assert portal.key == SOURCE_KEY
    assert portal.dated is True


def test_the_host_is_pinned_to_a_direct_route() -> None:
    # Not an escalation: a proxy that gets reached for silently is a bill
    # nobody decided to pay. See sweep._pin_direct.
    fetcher = Fetcher(proxy="http://user:pass@gw.example:823")
    _pin_direct(SpareRoom(), fetcher)
    assert fetcher.never_proxy("https://www.spareroom.co.uk/flatshare/london")


def test_pinning_keeps_the_image_cdns_already_exempt() -> None:
    # `direct_hosts` holds the picture CDNs. Replacing it rather than adding to
    # it would put every photograph on the metered connection.
    fetcher = Fetcher(proxy="http://x@y:1", direct_hosts=("media.rightmove.co.uk",))
    _pin_direct(SpareRoom(), fetcher)
    assert fetcher.never_proxy("https://media.rightmove.co.uk/a.jpg")
    assert fetcher.never_proxy("https://www.spareroom.co.uk/x")


def test_pinning_a_host_twice_does_not_repeat_it() -> None:
    fetcher = Fetcher(direct_hosts=("spareroom.co.uk",))
    _pin_direct(SpareRoom(), fetcher)
    assert fetcher.direct_hosts.count("spareroom.co.uk") == 1


def test_a_portal_with_no_pin_is_left_alone() -> None:
    fetcher = Fetcher(proxy="http://x@y:1")
    _pin_direct(object(), fetcher)
    assert fetcher.direct_hosts == ()


def test_only_a_wanted_district_costs_an_advert_page(
    caught: dict[str, Catch],
) -> None:
    # A region sweep reads the whole city. Without this gate the reader would
    # fetch a page for every new listing in London on every run.
    get = _Advert()
    fresh = list(caught.values())
    filled = _enrich(SpareRoom(), fresh, {"SE1"}, get, _Log())
    assert len(get.asked) == 1
    by_id = {one.listing.external_id: one for one in filled}
    assert by_id["18456083"].listing.furnished == "furnished"
    assert by_id["18363867"].listing.furnished == "unknown"


def test_the_advert_budget_is_a_ceiling_on_one_run(
    caught: dict[str, Catch],
) -> None:
    get = _Advert()
    portal = SpareRoom(detail_budget=1)
    _enrich(portal, list(caught.values()), {"SE1", "SE16"}, get, _Log())
    assert len(get.asked) == 1


def test_an_unreadable_advert_keeps_the_listing_the_card_gave(
    caught: dict[str, Catch],
) -> None:
    # One page that will not load is not worth losing a listing over: it is
    # stored with what the search page said, which is most of it.
    filled = _enrich(SpareRoom(), list(caught.values()), {"SE1"}, _Refusing(), _Log())
    by_id = {one.listing.external_id: one for one in filled}
    assert by_id["18456083"].listing.price_pcm == 1350
    assert by_id["18456083"].listing.furnished == "unknown"


def test_a_portal_with_no_enrich_hook_pays_nothing(
    caught: dict[str, Catch],
) -> None:
    fresh = list(caught.values())
    assert _enrich(object(), fresh, {"SE1"}, _Refusing(), _Log()) is fresh


# ── helpers ──────────────────────────────────────────────────────────────


def _card(
    *,
    listing_id: str = "123",
    postcode: str = "SE16",
    kind: str = "offered",
    rate: str = "&pound;900",
    period: str = "pcm",
    room: str = "Double room",
    image: str = "",
) -> str:
    """One card, in the shape the live page renders."""

    photo = (
        f'<img class="listing-card__main-image" src="{image}">' if image else ""
    )
    return (
        f' data-listing-id="{listing_id}"'
        f' data-listing-type="{kind}"'
        f' data-listing-postcode="{postcode}"'
        f' data-listing-property-type="flat"'
        f' data-listing-advertiser-role="agent"'
        f' data-listing-days-old="0"'
        f' data-listing-ad-pics="4"'
        f' data-listing-ad-headline-rate="{rate}"'
        f' data-listing-ad-headline-rate-period="{period}"'
        f' data-listing-brand="free">'
        f'<article><a class="listing-card__link"'
        f' href="/flatshare/london/bermondsey/{listing_id}">{photo}'
        f'<p class="listing-card__room-type"><span>{room}</span>'
        f'<span> - Available now</span></p></a></article>'
    )


def _reply(body: str) -> Reply:
    """A served page. The byte count is the fetcher's business, not a parser's."""

    return Reply(status=200, body=body, wire=len(body) // 5, impersonated="chrome124")


def _advert_prices(rooms: list[tuple[str, str]]) -> str:
    """An advert page carrying just its room-and-price section."""

    pairs = "".join(
        f'<dt class="feature-list__key">{price}</dt>'
        f'<dd class="feature-list__value">{what}</dd>'
        for price, what in rooms
    )
    return (
        '<section class="feature feature--price_room_only">'
        f'<dl class="feature-list">{pairs}</dl></section>'
    )


class _Log(Stage):
    """A stage that records nothing, for a parser that only ever counts."""

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

    def degrade(self, reason: str) -> None:
        self.lines.append(("degraded", reason))


class _Pages:
    """A fetcher that serves the fixture, and records what was asked for."""

    def __init__(
        self, bodies: list[str] | None = None, then_refuse: bool = False
    ) -> None:
        self.bodies = bodies
        self.then_refuse = then_refuse
        self.asked: list[str] = []

    def get(self, url: str, *, accept: str = "") -> Reply:
        self.asked.append(url)
        if self.bodies is not None:
            if len(self.asked) > len(self.bodies):
                if self.then_refuse:
                    raise Refused(f"{url} answered 403")
                return _reply("<html></html>")
            body = self.bodies[len(self.asked) - 1]
        else:
            body = PAGE
        return _reply(body)


class _Advert:
    """A fetcher that serves the advert fixture for any url."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def get(self, url: str, *, accept: str = "") -> Reply:
        self.asked.append(url)
        return _reply(ADVERT)


class _Refusing:
    """A fetcher that will not serve anything."""

    def get(self, url: str, *, accept: str = "") -> Reply:
        raise Refused(f"{url} answered 403 under every fingerprint tried")
