"""Reading a full postcode off a listing's own page.

Why this is worth a request at all: `store.mark_duplicate` compares on the
full postcode and skips any listing without one, so the same flat advertised
on both portals could not be recognised as one flat and both copies were sent.
Zoopla states no postcode at all on a search page, so every Zoopla listing was
in that position.

Nothing here touches the network.
"""

from __future__ import annotations

from worker.sources.postcode import from_fields, tidy

# The shape Zoopla's listing page ships, cut down to the two fields that
# matter. Confirmed on a live page on 30 September 2026.
ZOOPLA = (
    '{"listingId":"74384180","location":{"coordinates":{"latitude":51.51},'
    '"outcode":"E14","incode":"0UY","countryCode":"gb"},"title":"2 bed flat"}'
)


def test_the_two_halves_become_one_postcode() -> None:
    assert from_fields(ZOOPLA, "E14") == "E14 0UY"


def test_the_district_has_to_agree() -> None:
    # The page carries panels for other properties, and a field belonging to
    # one of those is not the postcode of the listing we asked for.
    assert from_fields(ZOOPLA, "SE16") is None


def test_it_works_without_a_district_to_check_against() -> None:
    # A listing with no district at all is not a reason to refuse the postcode
    # the page states.
    assert from_fields(ZOOPLA, None) == "E14 0UY"


def test_a_page_with_only_one_half_gives_nothing() -> None:
    assert from_fields('{"outcode":"E14"}', "E14") is None
    assert from_fields('{"incode":"0UY"}', "E14") is None
    assert from_fields("nothing structured here", "E14") is None


def test_an_inward_code_of_the_wrong_shape_is_refused() -> None:
    # Always digit, letter, letter. Anything else is not an inward code, and
    # half a guess in this field ends up in the duplicate fingerprint.
    assert from_fields('{"outcode":"E14","incode":"UY0"}', "E14") is None
    assert tidy("E14", "XYZ") is None
    assert tidy("E14", "00A") is None


def test_an_outward_code_of_the_wrong_shape_is_refused() -> None:
    assert tidy("12", "0UY") is None
    assert tidy("", "0UY") is None
    assert tidy("TOOLONG", "0UY") is None


def test_every_real_outward_shape_is_accepted() -> None:
    # London alone uses all of these: E14, W1A, SW11, EC3N.
    for outward in ("E1", "E14", "W1A", "SW11", "EC3N", "N1C"):
        assert tidy(outward, "0UY") == f"{outward} 0UY", outward


def test_case_and_spacing_are_normalised() -> None:
    # One spelling in the database, or the duplicate rule compares "E14 0UY"
    # against "e140uy" and finds two flats where there is one.
    assert tidy(" e14 ", " 0uy ") == "E14 0UY"
    assert from_fields('{"outcode":"e14","incode":"0uy"}', "E14") == "E14 0UY"
