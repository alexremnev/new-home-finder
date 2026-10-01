from __future__ import annotations

from datetime import date, datetime

import pytest

from worker.ingest.tg_feed import (
    Unparseable,
    available_from,
    bedrooms_of,
    district_of,
    furnishing_of,
    money,
    parse,
)

LISTING = (
    "🚀 A listing matching your criteria has just been posted.\n"
    "🏡 **Location**: Leytonstone\n"
    "📮 **Postcode**: E11 4EG\n"
    "🧭 **Address**: Grove Green Road, Leyton E11 📍 "
    "[View on map](https://maps.google.com/?q=51.568248,0.007582)\n"
    "💰 **Price**: £1700/month\n"
    "🛏 **Bedrooms**: 2 Bedrooms\n"
    "🛁 **Bathrooms**: 1 Bathroom\n"
    "📏 **Size**: N/A\n"
    "📅 **Available**: Immediately\n"
    "🛋 **Furnishing**: Unfurnished\n"
    "🔒 **Deposit**: £1961\n"
    "⚠️ **You're missing out on 90% of new listings:** Right now, you're only seeing "
    "10% of available flats - upgrade to Premium to unlock full access 🔎"
)

BUTTONS = [
    {"row": 0, "text": "🏠 View Listing", "url": "https://www.zoopla.co.uk/to-rent/details/73991134"},
    {"row": 1, "text": "💳 Upgrade to Paid Plan", "url": None},
]

SENT = datetime(2026, 8, 15, 15, 59, 4)

def test_a_whole_message_becomes_a_listing() -> None:
    parsed = parse(LISTING, BUTTONS, received_at=SENT, message_id=108177)
    assert (parsed.price_pcm, parsed.bedrooms, parsed.bathrooms) == (1700, 2, 1)
    assert parsed.furnished == "unfurnished"
    assert (parsed.postcode, parsed.postcode_district) == ("E11 4EG", "E11")
    assert parsed.deposit_pcm == 1961.0

def test_the_listing_is_identified_by_the_portal_not_the_message() -> None:

    parsed = parse(LISTING, BUTTONS, received_at=SENT)
    assert parsed.source_key == "zoopla"
    assert parsed.external_id == "73991134"
    assert parsed.url == "https://www.zoopla.co.uk/to-rent/details/73991134"
    assert parsed.raw["via"] == "tg_feed"

def test_tracking_parameters_are_dropped_from_the_url() -> None:

    parsed = parse(
        LISTING,
        [{"url": "https://www.rightmove.co.uk/properties/92052438?utm_source=tg&x=1#photos"}],
        received_at=SENT,
    )
    assert parsed.url == "https://www.rightmove.co.uk/properties/92052438"
    assert parsed.external_id == "92052438"

def test_the_map_pin_is_not_mistaken_for_the_listing() -> None:

    parsed = parse(LISTING, BUTTONS, received_at=SENT)
    assert "maps.google" not in parsed.url
    assert parsed.raw["address"] == "Grove Green Road, Leyton E11"

# OpenRent serves both of these for the same flat, and the feed carries the
# long one. The number at the end is the listing; the number at the head of the
# slug is a bedroom count.
OPENRENT = (
    "https://www.openrent.co.uk/property-to-rent/london/"
    "2-bed-flat-discovery-dock-e14/3059105"
)
OPENRENT_SHORT = "https://www.openrent.co.uk/property-to-rent/london/3059105"


def test_an_openrent_id_is_the_listing_and_not_the_bedroom_count() -> None:
    # This captured "2" — the head of the slug. `listings` is UNIQUE
    # (source_key, external_id), so every 2-bed OpenRent listing from the feed
    # upserted onto one row: the flat was never stored, and `notifications`
    # being UNIQUE (user_id, listing_id) meant nobody was told about it either.
    parsed = parse(LISTING, [{"url": OPENRENT}], received_at=SENT)

    assert parsed.source_key == "openrent"
    assert parsed.external_id == "3059105"


def test_the_short_openrent_url_still_works() -> None:
    # The form the old pattern was written against. Both are served, so both
    # have to parse.
    parsed = parse(LISTING, [{"url": OPENRENT_SHORT}], received_at=SENT)
    assert parsed.external_id == "3059105"


def test_a_studio_or_a_room_is_not_lost_for_want_of_a_leading_digit() -> None:
    # Their slugs start with a word, so the old pattern matched nothing at all
    # and the message was counted unparseable rather than wrong.
    for slug, expected in (
        ("studio-flat-london-e14/3024812", "3024812"),
        ("room-in-a-shared-flat-willis-house-e14/2937375", "2937375"),
    ):
        parsed = parse(
            LISTING,
            [{"url": f"https://www.openrent.co.uk/property-to-rent/london/{slug}"}],
            received_at=SENT,
        )
        assert parsed.external_id == expected, slug


def test_a_trailing_slash_or_a_tracking_parameter_does_not_shift_the_id() -> None:
    for tail in ("/", "?utm_source=tg", "#photos"):
        parsed = parse(LISTING, [{"url": OPENRENT + tail}], received_at=SENT)
        assert parsed.external_id == "3059105", tail


def test_an_openrent_url_with_no_id_is_refused() -> None:
    # Better unparseable and counted than stored under a number that is not an
    # id — that is the whole lesson of the bug above.
    with pytest.raises(Unparseable, match="no listing link"):
        parse(
            LISTING,
            [{"url": "https://www.openrent.co.uk/property-to-rent/london/2-bed-flat-e14"}],
            received_at=SENT,
        )


def test_a_message_with_no_listing_link_is_refused() -> None:
    with pytest.raises(Unparseable, match="no listing link"):
        parse(LISTING, [{"url": None}], received_at=SENT)

def test_prose_is_not_read_as_a_listing() -> None:
    with pytest.raises(Unparseable, match="not a listing"):
        parse("Hello! Welcome, send /start to begin.", BUTTONS)

@pytest.mark.parametrize("label", ["**Price**: N/A", "**Price**: "])
def test_a_missing_price_is_refused_rather_than_guessed(label: str) -> None:

    text = LISTING.replace("💰 **Price**: £1700/month", f"💰 {label}")
    with pytest.raises(Unparseable, match="price"):
        parse(text, BUTTONS, received_at=SENT)

def test_the_label_is_matched_however_the_source_decorates_it() -> None:

    for variant in (
        "💰 **Price**: £1700/month",
        "💰 *Price*: £1700/month",
        "**Price**: £1700/month",
        "Price: £1700/month",
        "💰 **Price** : £1700/month",
    ):
        text = LISTING.replace("💰 **Price**: £1700/month", variant)
        assert parse(text, BUTTONS, received_at=SENT).price_pcm == 1700, variant

class TestValues:
    def test_a_studio_is_a_flat_with_no_bedrooms(self) -> None:

        # Not a type of its own. Stored as "studio" it was invisible to a filter
        # for flats — which is the filter a studio hunter also ticks — and the
        # nought already carries the meaning.
        assert bedrooms_of("Studio") == (0, "flat")

    def test_a_room_in_a_share_is_named_as_one(self) -> None:
        assert bedrooms_of("Room in a share") == (1, "room")

    @pytest.mark.parametrize(
        ("text", "expected"), [("1 Bedroom", 1), ("2 Bedrooms", 2), ("4 Bedrooms", 4)]
    )
    def test_a_count_is_read_singular_or_plural(self, text: str, expected: int) -> None:
        assert bedrooms_of(text)[0] == expected

    def test_bedrooms_not_stated_is_refused(self) -> None:
        with pytest.raises(Unparseable):
            bedrooms_of("N/A")

    def test_money_survives_thousands_separators(self) -> None:
        assert money("£1,961") == 1961
        assert money("£1700/month") == 1700

    @pytest.mark.parametrize("absent", ["N/A", "", "  ", "-", "TBC"])
    def test_money_not_stated_is_none_not_zero(self, absent: str) -> None:

        assert money(absent) is None

    def test_furnishing_not_stated_stays_unknown(self) -> None:

        assert furnishing_of("N/A") == "unknown"
        assert furnishing_of("Furnished") == "furnished"
        assert furnishing_of("Unfurnished") == "unfurnished"

    @pytest.mark.parametrize(
        ("postcode", "district"),
        [("E11 4EG", "E11"), ("SW17 8BW", "SW17"), ("HA1 1EH", "HA1"), ("E1 8EY", "E1")],
    )
    def test_the_outward_code_is_what_subscriptions_name(
        self, postcode: str, district: str
    ) -> None:
        assert district_of(postcode) == district

    def test_a_postcode_that_is_not_one_is_none(self) -> None:
        assert district_of("N/A") is None
        assert district_of("Camden Town") is None

    def test_a_date_survives_its_ordinal_suffix(self) -> None:
        assert available_from("from 1st September 2026", received_at=None) == date(2026, 9, 1)
        assert available_from("from 13th October 2026", received_at=None) == date(2026, 10, 13)
        assert available_from("from 22nd August 2026", received_at=None) == date(2026, 8, 22)

    def test_immediately_means_the_day_the_message_was_sent(self) -> None:

        assert available_from("Immediately", received_at=SENT) == SENT.date()

    def test_an_unreadable_date_is_unknown_rather_than_wrong(self) -> None:
        assert available_from("sometime soon", received_at=SENT) is None

class TestSize:
    def test_the_size_field_becomes_square_feet(self) -> None:
        from worker.units import sqft_from

        # British listings quote feet, the scraped pages quote metres, and the
        # feed has been seen doing either. The unit is read, never assumed.
        assert sqft_from("650 sq ft") == 650
        assert sqft_from("1,250 sqft") == 1250
        assert sqft_from("820 ft²") == 820
        assert sqft_from("105 sq m") == 1130
        assert sqft_from("60m²") == 646

    def test_an_area_with_no_unit_is_refused(self) -> None:
        from worker.units import sqft_from

        # "65" is 65 square feet or 65 square metres depending on who wrote it,
        # and either reading silently removes homes from somebody's alerts.
        assert sqft_from("65") is None
        assert sqft_from("N/A") is None
        assert sqft_from("") is None
        assert sqft_from(None) is None

    def test_an_area_nobody_could_live_in_is_refused(self) -> None:
        from worker.units import sqft_from

        # A typo or a plot of land, not a London flat.
        assert sqft_from("2 sq m") is None
        assert sqft_from("999999 sq ft") is None

    def test_the_conversion_survives_a_round_trip(self) -> None:
        from worker.units import sqft_from, sqm_from_sqft

        feet = sqft_from("105 sq m")
        assert feet is not None
        assert sqm_from_sqft(feet) == 105

    def test_a_message_without_a_size_stores_none(self) -> None:
        parsed = parse(LISTING, BUTTONS, received_at=SENT, message_id=108177)
        # The fixture says "N/A", which is exactly the commonest case.
        assert parsed.floor_area_sqft is None


def test_the_feed_falls_back_to_the_slug_for_an_openrent_address() -> None:
    # The alert's "where" line reads `raw.address`, and the feed's OpenRent
    # messages mostly state none — which is how an alert came to show a
    # postcode, a price and no idea where the flat was. The url carries the
    # building, so there is something to say without fetching anything.
    from worker.ingest.parse import as_listing

    text = LISTING.replace("🧭 **Address**: Grove Green Road, Leyton E11 📍 ", "")
    parsed = parse(text, [{"url": OPENRENT}], received_at=SENT)
    listing = as_listing(parsed)

    assert listing.raw["address"] == "Discovery Dock"


def test_what_the_message_states_beats_the_slug() -> None:
    # The slug is a reconstruction: its punctuation and capitals are gone. What
    # the message stated is what the portal stated, so it wins.
    from worker.ingest.parse import as_listing

    parsed = parse(LISTING, [{"url": OPENRENT}], received_at=SENT)
    assert as_listing(parsed).raw["address"] == "Grove Green Road, Leyton E11"


def test_the_feed_types_an_openrent_listing_from_its_slug() -> None:
    # The feed states no property type at all — its Bedrooms field says
    # "2 Bedrooms" and stops — and an untyped listing passes every
    # property-type filter, because silence never excludes. So somebody who
    # asked for a flat was being sent houses and rooms as well.
    from worker.ingest.parse import as_listing

    for slug, expected in (
        ("2-bed-flat-discovery-dock-e14", "flat"),
        ("4-bed-maisonette-smythe-st-e14", "flat"),
        ("2-bed-terraced-house-rotherhithe-street-se16", "house"),
        ("room-in-a-shared-flat-willis-house-e14", "room"),
    ):
        url = f"https://www.openrent.co.uk/property-to-rent/london/{slug}/3059105"
        parsed = parse(LISTING, [{"url": url}], received_at=SENT)
        assert as_listing(parsed).property_type == expected, slug


def test_what_the_message_states_beats_the_slug_for_the_type_too() -> None:
    from worker.ingest.parse import as_listing

    # "Room" in the Bedrooms field is a type the message did state, and the
    # slug must not overrule it.
    text = LISTING.replace("🛏 **Bedrooms**: 2 Bedrooms", "🛏 **Bedrooms**: Room")
    url = "https://www.openrent.co.uk/property-to-rent/london/2-bed-flat-x-e14/3059105"
    parsed = parse(text, [{"url": url}], received_at=SENT)

    assert as_listing(parsed).property_type == "room"
