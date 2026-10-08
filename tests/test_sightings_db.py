from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from worker import store
from worker.contracts.listing import Listing
from worker.sources.sweep import CATCH_UP

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


def stored_by(
    conn: Any,
    *,
    reader: str,
    source: str = "rightmove",
    external_id: str = "93625473",
    hours_ago: float = 0.0,
) -> int:
    """A listing one reader has stored and seen, as the worker would leave it."""

    listing_id = store.insert_listing(
        conn,
        Listing(
            source_key=source,
            external_id=external_id,
            url=f"https://example.test/{source}/{external_id}",
            price_pcm=2100,
            bedrooms=2,
            postcode="SE16 4TH",
            postcode_district="SE16",
        ),
    )
    store.record_sightings(conn, [listing_id], reader)
    if hours_ago:
        conn.execute(
            "UPDATE listings SET first_seen_at = first_seen_at "
            "- make_interval(secs => %s) WHERE id = %s",
            (hours_ago * 3600, listing_id),
        )
        conn.execute(
            "UPDATE listing_sightings SET first_at = first_at "
            "- make_interval(secs => %s) WHERE listing_id = %s",
            (hours_ago * 3600, listing_id),
        )
    return listing_id


def ask(conn: Any, *, reader: str = "rightmove", ids: list[str]) -> store.Sighted:
    return store.sighted_by(
        conn,
        source_key="rightmove",
        reader=reader,
        external_ids=ids,
        catch_up=CATCH_UP,
    )


def test_an_id_we_have_never_stored_is_news(conn: Any) -> None:
    sighted = ask(conn, ids=["93625473"])

    assert sighted.known == frozenset()
    assert sighted.ids == {}


def test_an_id_this_reader_has_seen_is_not_news(conn: Any) -> None:
    listing_id = stored_by(conn, reader="rightmove")

    sighted = ask(conn, ids=["93625473"])

    assert sighted.known == frozenset({"93625473"})
    assert sighted.ids == {"93625473": listing_id}


def test_a_listing_the_feed_got_to_first_is_still_news_to_the_scraper(
    conn: Any,
) -> None:
    """The whole point of asking sightings rather than rows.

    The Telegram feed stores a flat under the portal that hosts it, as a push,
    so it beats a five-minute timer nearly every time. Read as "is there a
    row", the scraper called this already known and never decided whether to
    announce it — harmless only while the feed did the announcing.
    """

    listing_id = stored_by(conn, reader="tg_feed")

    sighted = ask(conn, ids=["93625473"])

    assert sighted.known == frozenset()
    # Stored, so the sighting has a row to go against — which is what
    # separates this from an id we have never seen at all.
    assert sighted.ids == {"93625473": listing_id}


def test_the_catching_up_does_not_reach_back_into_the_backlog(conn: Any) -> None:
    """`CATCH_UP` is what keeps the change above from emptying the backlog.

    An undated portal announces anything in a settled district, so without a
    bound the first run after this rule changed would have sent a subscriber
    every OpenRent flat the feed had ever posted.
    """

    stored_by(
        conn,
        reader="tg_feed",
        hours_ago=CATCH_UP.total_seconds() / 3600 + 1,
    )

    sighted = ask(conn, ids=["93625473"])

    assert sighted.known == frozenset({"93625473"})


def test_the_bound_is_read_from_the_argument_and_not_from_the_clock(
    conn: Any,
) -> None:
    stored_by(conn, reader="tg_feed", hours_ago=4)

    assert store.sighted_by(
        conn,
        source_key="rightmove",
        reader="rightmove",
        external_ids=["93625473"],
        catch_up=timedelta(hours=1),
    ).known == frozenset({"93625473"})

    assert store.sighted_by(
        conn,
        source_key="rightmove",
        reader="rightmove",
        external_ids=["93625473"],
        catch_up=timedelta(hours=8),
    ).known == frozenset()


def test_another_portals_copy_of_the_id_is_not_ours(conn: Any) -> None:
    """`external_id` only means anything beside its source key.

    OpenRent and Rightmove both number their listings, and the numbers collide.
    """

    stored_by(conn, reader="openrent", source="openrent")

    sighted = ask(conn, ids=["93625473"])

    assert sighted.known == frozenset()
    assert sighted.ids == {}


def test_nothing_asked_is_one_fewer_round_trip(conn: Any) -> None:
    sighted = ask(conn, ids=[])

    assert sighted.ids == {}
    assert sighted.known == frozenset()
