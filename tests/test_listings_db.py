"""What a second sighting of the same flat is allowed to change.

Against a real Postgres, because the whole behaviour under test is one
`ON CONFLICT` clause — a stub conn would be asserting that the string we
wrote is the string we wrote. Skipped without `TEST_DATABASE_URL`, and the
database is wiped, so only ever a throwaway container.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from worker import store
from worker.contracts.listing import Listing
from worker.ingest import photo
from worker.ingest.parse import fill_images, fill_types
from worker.obs import Run

URL = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "db" / "migrations"

WIPE = "TRUNCATE listings, listing_sightings RESTART IDENTITY CASCADE"


@pytest.fixture(scope="module")
def schema() -> Iterator[None]:
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
    yield


@pytest.fixture
def conn(schema: None) -> Iterator[Any]:
    with psycopg.connect(URL, autocommit=True, row_factory=psycopg.rows.dict_row) as c:
        c.execute(WIPE)
        yield c


@pytest.fixture
def run(conn: Any) -> Run:
    return Run(conn, job="ingest", trigger="manual")


def listing(**over: Any) -> Listing:
    """The same Rightmove flat, as one reader or another would describe it."""

    fields: dict[str, Any] = {
        "source_key": "rightmove",
        "external_id": "94166934",
        "url": "https://www.rightmove.co.uk/properties/94166934",
        "price_pcm": 2500,
        "bedrooms": 2,
        "postcode": "NW1 1HN",
        "postcode_district": "NW1",
    }
    return Listing(**{**fields, **over})


def type_of(conn: Any, listing_id: int) -> str | None:
    row = conn.execute(
        "SELECT property_type FROM listings WHERE id = %s", (listing_id,)
    ).fetchone()
    assert row is not None
    return row["property_type"]


def test_a_scraper_fills_the_type_the_feed_left_blank(conn: Any) -> None:
    # The order this actually happens in: the feed runs every two minutes and
    # states no type at all, the Rightmove reader comes along later with
    # "Apartment" off the portal. Before this the second write kept only
    # `last_seen_at`, so the flat stayed untyped for ever and answered every
    # property-type filter.
    first = store.insert_listing(conn, listing(property_type=None))
    assert type_of(conn, first) is None

    again = store.insert_listing(conn, listing(property_type="flat"))
    assert again == first
    assert type_of(conn, first) == "flat"


def test_a_stated_type_is_not_overwritten_by_a_blank_one(conn: Any) -> None:
    # The other order, and the reason this fills blanks rather than taking the
    # newest value: a reader that stated nothing has not learned that the flat
    # is untyped.
    first = store.insert_listing(conn, listing(property_type="house"))
    store.insert_listing(conn, listing(property_type=None))
    assert type_of(conn, first) == "house"


def test_a_stated_type_is_not_overwritten_by_another_one(conn: Any) -> None:
    # Both readers read a page and both are entitled to their answer; the
    # first one's stands rather than flapping between runs.
    first = store.insert_listing(conn, listing(property_type="flat"))
    store.insert_listing(conn, listing(property_type="room"))
    assert type_of(conn, first) == "flat"


PAGE = (
    '<head><meta property="og:title" '
    'content="Check out this 2 bedroom apartment for rent on Rightmove">'
    '<meta property="og:image" content="https://media.rightmove.co.uk/1.jpeg">'
    "</head>"
)


def pages_served(monkeypatch: pytest.MonkeyPatch, body: str | None) -> list[str]:
    """Every url the fill steps actually fetched, with the network stubbed out."""

    asked: list[str] = []

    def head_of(url: str, *, opener: object | None = None) -> str | None:
        asked.append(url)
        return body

    monkeypatch.setattr(photo, "head_of", head_of)
    return asked


def test_the_type_queue_holds_only_untyped_listings(conn: Any) -> None:
    untyped = store.insert_listing(conn, listing(property_type=None))
    store.insert_listing(conn, listing(external_id="1", property_type="flat"))
    assert [row["id"] for row in store.listings_missing_type(conn)] == [untyped]


def test_a_page_that_named_nothing_is_not_asked_again(conn: Any) -> None:
    listing_id = store.insert_listing(conn, listing(property_type=None))
    store.set_listing_type(conn, listing_id, None)
    assert type_of(conn, listing_id) is None
    # Looked at and silent is not the same as not looked at, or every untyped
    # listing would be fetched again on every run forever.
    assert store.listings_missing_type(conn) == []


def test_a_type_that_arrived_meanwhile_is_not_blanked(conn: Any) -> None:
    # The scraper can land between a listing being picked up and the page
    # coming back. The page said nothing; the scraper said "flat".
    listing_id = store.insert_listing(conn, listing(property_type=None))
    store.insert_listing(conn, listing(property_type="flat"))
    store.set_listing_type(conn, listing_id, None)
    assert type_of(conn, listing_id) == "flat"


def test_the_feeds_untyped_listing_gets_its_type_from_the_page(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing_id = store.insert_listing(conn, listing(property_type=None))
    asked = pages_served(monkeypatch, PAGE)

    assert fill_types(conn, run) == 1
    assert type_of(conn, listing_id) == "flat"
    assert len(asked) == 1

    # And the work is done: nothing left to ask about, so no second request.
    assert fill_types(conn, run) == 0
    assert len(asked) == 1


def test_a_listing_needing_both_facts_is_fetched_once(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reason the two steps share a dict. Before this the picture and the
    # type were two GETs of the same document.
    listing_id = store.insert_listing(conn, listing(property_type=None))
    asked = pages_served(monkeypatch, PAGE)

    pages: dict[str, str] = {}
    fill_images(conn, run, pages=pages)
    fill_types(conn, run, pages=pages)

    assert len(asked) == 1
    assert type_of(conn, listing_id) == "flat"
    row = conn.execute(
        "SELECT image_url FROM listings WHERE id = %s", (listing_id,)
    ).fetchone()
    assert row is not None
    assert row["image_url"] == "https://media.rightmove.co.uk/1.jpeg"


def test_a_portal_that_refused_is_recorded_as_looked_at(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Zoopla's case: 403 and a Cloudflare page, so `head_of` gives nothing.
    # The listing is marked checked all the same — its type comes from the
    # scraper writing into the row, not from here.
    listing_id = store.insert_listing(conn, listing(property_type=None))
    asked = pages_served(monkeypatch, None)

    assert fill_types(conn, run) == 0
    assert len(asked) == 1
    assert type_of(conn, listing_id) is None
    assert store.listings_missing_type(conn) == []
