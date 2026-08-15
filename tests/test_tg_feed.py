"""Parsing a Telegram listing feed's messages.

The fixtures are real messages, captured with `mirror.py dump --json` and pasted
here byte for byte — including the source's own broken markdown, where `**Label**`
arrives as literal text *and* as a bold entity. Inventing the format would have
tested the invention rather than the source, and the two differ in exactly the
places a parser breaks.
"""

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

# A real message, unmodified.
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
    """What makes the same flat arriving twice — resent, seen by a second reader
    account, or found later by our own scraper — one row rather than three."""
    parsed = parse(LISTING, BUTTONS, received_at=SENT)
    assert parsed.source_key == "zoopla"
    assert parsed.external_id == "73991134"
    assert parsed.url == "https://www.zoopla.co.uk/to-rent/details/73991134"
    assert parsed.raw["via"] == "tg_feed"


def test_tracking_parameters_are_dropped_from_the_url() -> None:
    """Otherwise the same listing looks new every time it is shared."""
    parsed = parse(
        LISTING,
        [{"url": "https://www.rightmove.co.uk/properties/92052438?utm_source=tg&x=1#photos"}],
        received_at=SENT,
    )
    assert parsed.url == "https://www.rightmove.co.uk/properties/92052438"
    assert parsed.external_id == "92052438"


def test_the_map_pin_is_not_mistaken_for_the_listing() -> None:
    """The only url in the *text* is a Google Maps link. Reading it as the listing
    would produce rows that all point at a map."""
    parsed = parse(LISTING, BUTTONS, received_at=SENT)
    assert "maps.google" not in parsed.url
    assert parsed.raw["address"] == "Grove Green Road, Leyton E11"


def test_a_message_with_no_listing_link_is_refused() -> None:
    with pytest.raises(Unparseable, match="no listing link"):
        parse(LISTING, [{"url": None}], received_at=SENT)


def test_prose_is_not_read_as_a_listing() -> None:
    with pytest.raises(Unparseable, match="not a listing"):
        parse("Hello! Welcome, send /start to begin.", BUTTONS)


@pytest.mark.parametrize("label", ["**Price**: N/A", "**Price**: "])
def test_a_missing_price_is_refused_rather_than_guessed(label: str) -> None:
    """`listings.price_pcm` is NOT NULL, and a guessed price is an alert nobody can
    act on — worse than an alert that never arrives."""
    text = LISTING.replace("💰 **Price**: £1700/month", f"💰 {label}")
    with pytest.raises(Unparseable, match="price"):
        parse(text, BUTTONS, received_at=SENT)


def test_the_label_is_matched_however_the_source_decorates_it() -> None:
    """The source is inconsistent about emoji and asterisks, and has changed both
    already. Anything stricter would break on the next cosmetic edit."""
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
    def test_a_studio_is_no_bedrooms_and_a_property_type(self) -> None:
        # The matcher counts a studio as 0; the renderer says "studio" rather than
        # "0 bedrooms", which needs the type as well as the count.
        assert bedrooms_of("Studio") == (0, "studio")

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
        # Zero would be a price, and a £0 listing matches every budget filter.
        assert money(absent) is None

    def test_furnishing_not_stated_stays_unknown(self) -> None:
        """Not defaulted to unfurnished: the matcher requires a known value when
        the criterion is set, and unknown is the truthful answer."""
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
        """Not today: parsing a week-old message must not claim it is available
        now. And leaving it unknown would exclude every "Immediately" listing from
        an "available before" filter, because unknown does not pass."""
        assert available_from("Immediately", received_at=SENT) == SENT.date()

    def test_an_unreadable_date_is_unknown_rather_than_wrong(self) -> None:
        assert available_from("sometime soon", received_at=SENT) is None
