"""The property type, read off a listing page's title.

The og:title strings here are the ones the real sites served on
10 October 2026, fetched with the same plain client the worker uses, so what
is asserted is the shape that exists rather than one that would be convenient.
Nothing here touches the network.
"""

from __future__ import annotations

from worker.ingest.kind import said_type, title_in, type_in

RIGHTMOVE = (
    '<html><head><meta property="og:title" '
    'content="Check out this 2 bedroom apartment for rent on Rightmove">'
    "<title>2 bedroom apartment to rent in Chalton Street, NW1</title></head>"
)

OPENRENT_ROOM = (
    '<head><meta content="Room in a Shared Flat, Willis House, E14" '
    'property="og:title"></head>'
)

OPENRENT_FLAT = (
    '<head><meta property="og:title" content="3 Bed Flat, Hale Street, E14"></head>'
)


def test_the_portals_own_title_names_the_type() -> None:
    assert type_in(RIGHTMOVE) == "flat"
    assert type_in(OPENRENT_FLAT) == "flat"
    # A room is its own type, and the one the filter most needs to get right:
    # an untyped room answers a search for a flat.
    assert type_in(OPENRENT_ROOM) == "room"


def test_og_title_is_preferred_to_the_document_title() -> None:
    # Both are present on a Rightmove page and they are worded differently.
    # Either reads as a flat, so the assertion is on which one was read.
    assert title_in(RIGHTMOVE) == "Check out this 2 bedroom apartment for rent on Rightmove"


def test_the_document_title_is_read_when_there_is_no_og_title() -> None:
    page = "<head><title>Studio flat to rent in Poplar, E14</title></head>"
    assert title_in(page) == "Studio flat to rent in Poplar, E14"
    assert type_in(page) == "flat"


def test_the_portals_name_is_dropped_and_entities_are_read() -> None:
    page = (
        '<head><meta property="og:title" '
        'content="2 bed flat to rent in Hale &amp; Dock Street, E14 | Zoopla">'
        "</head>"
    )
    assert title_in(page) == "2 bed flat to rent in Hale & Dock Street, E14"
    assert type_in(page) == "flat"


def test_only_the_part_before_to_rent_is_read() -> None:
    # The whole point of cutting the title. `KINDS` is matched in order and the
    # address is prose: read whole, this title's "Studio Court" would make a
    # three-bedroom flat a studio, and "Terrace Road" would make it a house.
    title = "3 bedroom flat to rent in Studio Court, Terrace Road, SE16"
    assert said_type(title) == "3 bedroom flat"
    assert type_in(f"<head><title>{title}</title></head>") == "flat"


def test_a_title_with_no_to_rent_is_cut_at_the_first_comma() -> None:
    # OpenRent's shape, which states no "to rent" at all.
    assert said_type("Room in a Shared Flat, Willis House, E14") == "Room in a Shared Flat"


def test_a_page_that_names_nothing_is_left_alone() -> None:
    # Honest silence rather than a guess. The caller still records that the
    # page was read, so it is not fetched again.
    assert type_in('<head><title>Property to rent in London</title></head>') is None
    assert type_in("<head><title></title></head>") is None
    # Zoopla answers a plain client with a Cloudflare interstitial, and the
    # fetch gives the caller nothing at all.
    assert type_in("") is None


def test_a_house_boat_is_not_a_house() -> None:
    # NEITHER is inside kind_of, so the title cannot promote a boat either.
    page = '<head><title>2 bedroom house boat to rent in Chelsea, SW10</title></head>'
    assert type_in(page) is None
