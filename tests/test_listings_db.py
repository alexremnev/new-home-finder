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
