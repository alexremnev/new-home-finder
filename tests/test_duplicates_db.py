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

WIPE = "TRUNCATE listings RESTART IDENTITY CASCADE"


@pytest.fixture(scope="module")
def schema() -> Iterator[None]:
    # Every migration, in order. The rule under test is the last one and it
    # reads `district_days` and `daily_stats`, so the short list the other
    # database test uses is not enough — and applying the whole chain is the
    # only setup that matches what the server actually has.
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


def add(
    conn: Any,
    *,
    source: str,
    external_id: str,
    postcode: str | None = "SE16 4TH",
    price: int = 2100,
    bedrooms: int = 2,
    bathrooms: int | None = 1,
    days_ago: int = 0,
) -> int:
    """Store a listing and return its id, then mark it as the worker would."""

    listing_id = store.insert_listing(
        conn,
        Listing(
            source_key=source,
            external_id=external_id,
            url=f"https://example.test/{source}/{external_id}",
            price_pcm=price,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            postcode=postcode,
            postcode_district="SE16",
        ),
    )
    if days_ago:
        # `first_seen_at` defaults to now(), and the rule is about a day.
        conn.execute(
            "UPDATE listings SET first_seen_at = first_seen_at "
            "- make_interval(days => %s) WHERE id = %s",
            (days_ago, listing_id),
        )
    return listing_id


def duplicate_of(conn: Any, listing_id: int) -> int | None:
    row = conn.execute(
        "SELECT duplicate_of FROM listings WHERE id = %s", (listing_id,)
    ).fetchone()
    return None if row is None or row["duplicate_of"] is None else int(row["duplicate_of"])


def test_the_same_flat_from_a_second_portal_is_a_copy(conn: Any) -> None:
    first = add(conn, source="rightmove", external_id="1")
    assert store.mark_duplicate(conn, first) is None

    second = add(conn, source="zoopla", external_id="2")
    assert store.mark_duplicate(conn, second) == first

    # The one that arrived first is the one that counts.
    assert duplicate_of(conn, first) is None
    assert duplicate_of(conn, second) == first


def test_two_from_one_portal_are_two_flats(conn: Any) -> None:
    # A build-to-rent block: forty identical studios at one address, one price,
    # all really available. Collapsing these would hide most of a new
    # development's stock, which is why the rule requires the sources to differ.
    first = add(conn, source="rightmove", external_id="1")
    store.mark_duplicate(conn, first)
    second = add(conn, source="rightmove", external_id="2")

    assert store.mark_duplicate(conn, second) is None
    assert duplicate_of(conn, second) is None


def test_a_copy_points_at_the_oldest_and_never_forms_a_chain(conn: Any) -> None:
    first = add(conn, source="rightmove", external_id="1")
    store.mark_duplicate(conn, first)
    second = add(conn, source="zoopla", external_id="2")
    store.mark_duplicate(conn, second)
    third = add(conn, source="openrent", external_id="3")

    assert store.mark_duplicate(conn, third) == first
    # Not at `second`, which is itself a copy: following a chain to find the
    # original is a bug waiting for the day a chain is long enough to matter.
    assert duplicate_of(conn, third) == first


def test_yesterdays_listing_is_not_todays_copy(conn: Any) -> None:
    # Two portals publishing the same flat do so within minutes. A match a day
    # apart is far more likely to be a real re-letting of the same flat at the
    # same rent, which is a listing somebody wants to hear about.
    old = add(conn, source="rightmove", external_id="1", days_ago=1)
    store.mark_duplicate(conn, old)
    today = add(conn, source="zoopla", external_id="2")

    assert store.mark_duplicate(conn, today) is None


def test_without_a_postcode_nothing_is_compared(conn: Any) -> None:
    # The district alone would make every 2-bed at £2,100 in SE16 one flat,
    # which throws away a busy district's worth of real listings.
    first = add(conn, source="rightmove", external_id="1", postcode=None)
    store.mark_duplicate(conn, first)
    second = add(conn, source="zoopla", external_id="2", postcode=None)

    assert store.mark_duplicate(conn, second) is None


def test_a_different_rent_or_room_count_is_a_different_flat(conn: Any) -> None:
    first = add(conn, source="rightmove", external_id="1")
    store.mark_duplicate(conn, first)

    cheaper = add(conn, source="zoopla", external_id="2", price=2000)
    assert store.mark_duplicate(conn, cheaper) is None

    roomier = add(conn, source="zoopla", external_id="3", bedrooms=3)
    assert store.mark_duplicate(conn, roomier) is None

    other_bath = add(conn, source="zoopla", external_id="4", bathrooms=2)
    assert store.mark_duplicate(conn, other_bath) is None


def test_a_bathroom_count_neither_portal_states_still_matches(conn: Any) -> None:
    # NULL has to equal NULL here, or the commonest shape of all — neither
    # portal saying — would never be found.
    first = add(conn, source="rightmove", external_id="1", bathrooms=None)
    store.mark_duplicate(conn, first)
    second = add(conn, source="zoopla", external_id="2", bathrooms=None)

    assert store.mark_duplicate(conn, second) == first


def test_a_copy_is_never_offered_for_matching(conn: Any) -> None:
    # The filter that actually stops the second alert going out. It lives in
    # the query rather than in the matcher so that every path into the outbox
    # inherits it.
    first = add(conn, source="rightmove", external_id="1")
    store.mark_duplicate(conn, first)
    second = add(conn, source="zoopla", external_id="2")
    store.mark_duplicate(conn, second)

    offered = store.listings_for_matching(conn, [first, second])
    assert [int(row["id"]) for row in offered] == [first]
