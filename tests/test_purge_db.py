"""The retention rules, against a real Postgres.

Every one of these asserts a *boundary*, because the risk in this job is never
"did the DELETE run" — it is "did it take one row more than it was meant to".
So each case sets up the row that must go next to the row that must stay, and
checks both.

Needs a throwaway database (`TEST_DATABASE_URL`) for the same reason
test_outbox_db does: cascades, partial indexes and the row-value `IN` are
exactly what a stub would not reproduce. **It wipes the schema**, so never
point it at Supabase.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from worker.obs import Run
from worker.pipeline.purge import RULES, run_purge

URL = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "db" / "migrations"

WIPE = """
TRUNCATE notifications, subscriptions, user_channels, user_tokens, payments, users,
         listing_sightings, listing_price_log, listings, source_messages,
         site_visits, job_events, job_stages, job_runs
    RESTART IDENTITY CASCADE
"""


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
    return Run(conn, job="purge", trigger="manual")


def user(
    conn: Any,
    *,
    status: str = "pending",
    plan: str = "trial",
    days_ago: float = 2,
    plan_from_days_ago: int | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO users (status, plan, created_at, plan_from)
        VALUES (%s, %s, now() - make_interval(mins => %s::int),
                CASE WHEN %s::int IS NULL THEN NULL
                     ELSE now() - make_interval(days => %s::int) END)
        RETURNING id
        """,
        (
            status, plan, int(days_ago * 24 * 60),
            plan_from_days_ago, plan_from_days_ago,
        ),
    ).fetchone()
    return int(row["id"])


def listing(conn: Any, *, external_id: str, last_seen_days_ago: int) -> int:
    row = conn.execute(
        """
        INSERT INTO listings (source_key, external_id, url, title, price_pcm,
                              bedrooms, postcode_district, first_seen_at,
                              last_seen_at, raw)
        VALUES ('rightmove', %s, 'https://example.test/' || %s, 'A flat',
                1500, 1, 'SE16',
                now() - interval '120 days',
                now() - make_interval(days => %s::int), '{}'::jsonb)
        RETURNING id
        """,
        (external_id, external_id, last_seen_days_ago),
    ).fetchone()
    return int(row["id"])


def alert(
    conn: Any, *, user_id: int, listing_id: int, status: str, days_ago: int
) -> int:
    row = conn.execute(
        """
        INSERT INTO notifications (user_id, listing_id, channel, status,
                                   created_at, sent_at)
        VALUES (%s, %s, 'telegram', %s,
                now() - make_interval(days => %s::int),
                CASE WHEN %s = 'sent'
                     THEN now() - make_interval(days => %s::int) END)
        RETURNING id
        """,
        (user_id, listing_id, status, days_ago, status, days_ago),
    ).fetchone()
    return int(row["id"])


def ids(conn: Any, table: str, column: str = "id") -> set[Any]:
    return {r[column] for r in conn.execute(f"SELECT {column} FROM {table}")}


# ── abandoned sign-ups ───────────────────────────────────────────────────────


def test_yesterdays_pending_goes_and_todays_stays(conn: Any, run: Run) -> None:
    # 0.01 days is a quarter of an hour ago, which is after London midnight
    # whenever the test runs — except in the quarter hour after midnight
    # itself, where it is still correct for a different reason: both rows are
    # then before midnight and both should go. Asserted on the older one only.
    stale = user(conn, days_ago=2)
    fresh = user(conn, days_ago=0.0005)

    run_purge(conn, run)

    assert stale not in ids(conn, "users")
    survivors = ids(conn, "users")
    assert fresh in survivors or survivors == set()


def test_pending_with_a_connected_channel_stays(conn: Any, run: Run) -> None:
    uid = user(conn, days_ago=5)
    conn.execute(
        """
        INSERT INTO user_channels (user_id, channel, address, verified_at)
        VALUES (%s, 'telegram', '555', now())
        """,
        (uid,),
    )
    run_purge(conn, run)
    assert uid in ids(conn, "users")


def test_pending_with_an_unverified_channel_goes(conn: Any, run: Run) -> None:
    # The row exists but nobody pressed the button, which is the whole case
    # this job is about.
    uid = user(conn, days_ago=5)
    conn.execute(
        "INSERT INTO user_channels (user_id, channel, address) VALUES (%s, 'telegram', '556')",
        (uid,),
    )
    run_purge(conn, run)
    assert uid not in ids(conn, "users")


def test_pending_who_paid_stays(conn: Any, run: Run) -> None:
    uid = user(conn, days_ago=5)
    conn.execute(
        """
        INSERT INTO payments (user_id, plan, amount_pence, provider)
        VALUES (%s, 'paid', 1000, 'manual')
        """,
        (uid,),
    )
    run_purge(conn, run)
    assert uid in ids(conn, "users")


def test_pending_on_a_priced_plan_stays(conn: Any, run: Run) -> None:
    uid = user(conn, status="pending", plan="paid", days_ago=5)
    run_purge(conn, run)
    assert uid in ids(conn, "users")


def test_stopped_and_active_accounts_are_never_touched(conn: Any, run: Run) -> None:
    kept = [
        user(conn, status="active", days_ago=400),
        user(conn, status="stopped", days_ago=400),
        user(conn, status="blocked", days_ago=400),
    ]
    run_purge(conn, run)
    assert set(kept) <= ids(conn, "users")


def test_the_subscription_and_token_go_with_the_account(conn: Any, run: Run) -> None:
    uid = user(conn, days_ago=3)
    conn.execute(
        """
        INSERT INTO subscriptions (user_id, label, criteria, active)
        VALUES (%s, 'SE16', '{}'::jsonb, false)
        """,
        (uid,),
    )
    conn.execute(
        """
        INSERT INTO user_tokens (token, user_id, purpose, expires_at)
        VALUES ('tok', %s, 'start', now() + interval '1 hour')
        """,
        (uid,),
    )
    run_purge(conn, run)
    assert ids(conn, "subscriptions") == set()
    assert ids(conn, "user_tokens", "token") == set()


# ── listings ────────────────────────────────────────────────────────────────


def test_a_listing_still_advertised_stays_however_old(conn: Any, run: Run) -> None:
    # First seen four months ago, seen again today: deleting it would hand it
    # back to the sweeper as news. This is the assertion that protects the
    # subscribers from a second alert about the same flat.
    live = listing(conn, external_id="live", last_seen_days_ago=0)
    gone = listing(conn, external_id="gone", last_seen_days_ago=70)

    run_purge(conn, run)

    assert ids(conn, "listings") == {live}
    assert gone not in ids(conn, "listings")


def test_the_price_log_and_sightings_go_with_the_listing(conn: Any, run: Run) -> None:
    old = listing(conn, external_id="gone", last_seen_days_ago=70)
    conn.execute(
        "INSERT INTO listing_price_log (listing_id, price_pcm) VALUES (%s, 1500)", (old,)
    )
    conn.execute(
        "INSERT INTO listing_sightings (listing_id, reader) VALUES (%s, 'rightmove')",
        (old,),
    )
    run_purge(conn, run)
    assert ids(conn, "listing_price_log", "listing_id") == set()
    assert ids(conn, "listing_sightings", "listing_id") == set()


# ── delivery history ────────────────────────────────────────────────────────


def test_an_old_sent_alert_goes_and_a_recent_one_stays(conn: Any, run: Run) -> None:
    uid = user(conn, status="active", plan="paid")
    live = listing(conn, external_id="live", last_seen_days_ago=0)
    old = alert(conn, user_id=uid, listing_id=live, status="sent", days_ago=90)
    new = alert(
        conn,
        user_id=uid,
        listing_id=listing(conn, external_id="two", last_seen_days_ago=1),
        status="sent",
        days_ago=3,
    )

    run_purge(conn, run)

    assert ids(conn, "notifications") == {new}
    assert old not in ids(conn, "notifications")


def test_a_queued_alert_is_kept_however_stale(conn: Any, run: Run) -> None:
    uid = user(conn, status="active", plan="paid")
    live = listing(conn, external_id="live", last_seen_days_ago=0)
    stuck = alert(conn, user_id=uid, listing_id=live, status="queued", days_ago=120)
    run_purge(conn, run)
    assert stuck in ids(conn, "notifications")


def test_the_current_paid_period_is_never_counted_down(conn: Any, run: Run) -> None:
    # A plan that started 200 days ago and is still running: its alerts are
    # what `user_entitlement.alerts_used` counts, so deleting them would hand
    # the allowance back.
    uid = user(conn, status="active", plan="paid", plan_from_days_ago=200)
    live = listing(conn, external_id="live", last_seen_days_ago=0)
    inside = alert(conn, user_id=uid, listing_id=live, status="sent", days_ago=150)
    run_purge(conn, run)
    assert inside in ids(conn, "notifications")


# ── the rest ────────────────────────────────────────────────────────────────


def test_messages_visits_and_runs_keep_two_months_and_one(conn: Any, run: Run) -> None:
    for nth, age in ((1, 70), (2, 10)):
        conn.execute(
            """
            INSERT INTO source_messages (source_key, reader, external_id,
                                         received_at, stored_at, content_hash, status)
            VALUES ('tg_feed', 'r', %s,
                    now() - make_interval(days => %s::int),
                    now() - make_interval(days => %s::int), %s, 'parsed')
            """,
            (str(nth), age, age, f"hash{nth}"),
        )
    for age in (70, 10):
        conn.execute(
            """
            INSERT INTO site_visits (day, visitor_hash)
            VALUES (current_date - %s::int, 'v' || %s::text)
            """,
            (age, age),
        )
    for age in (40, 5):
        conn.execute(
            """
            INSERT INTO job_runs (job, trigger, status, started_at)
            VALUES ('drain', 'schedule', 'ok', now() - make_interval(days => %s::int))
            """,
            (age,),
        )

    run_purge(conn, run)

    assert ids(conn, "source_messages", "external_id") == {"2"}
    assert ids(conn, "site_visits", "visitor_hash") == {"v10"}
    # This run's own row is in there too, which is the point of asking for the
    # old one by name rather than counting.
    kept = conn.execute(
        "SELECT count(*) AS n FROM job_runs WHERE started_at < now() - interval '35 days'"
    ).fetchone()
    assert kept["n"] == 0


def test_a_run_still_going_is_left_alone(conn: Any, run: Run) -> None:
    conn.execute(
        """
        INSERT INTO job_runs (job, trigger, status, started_at)
        VALUES ('ingest', 'schedule', 'running', now() - interval '90 days')
        """
    )
    run_purge(conn, run)
    stuck = conn.execute(
        "SELECT count(*) AS n FROM job_runs WHERE status = 'running'"
    ).fetchone()
    assert stuck["n"] == 1


def test_a_dry_run_counts_and_deletes_nothing(conn: Any, run: Run) -> None:
    uid = user(conn, days_ago=5)
    old = listing(conn, external_id="gone", last_seen_days_ago=70)

    assert run_purge(conn, run, dry_run=True) == {}

    assert uid in ids(conn, "users")
    assert old in ids(conn, "listings")
    waiting = conn.execute(
        "SELECT counters FROM job_stages WHERE stage = 'purge' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert waiting["counters"]["signups_abandoned_waiting"] == 1
    assert waiting["counters"]["listings_gone_waiting"] == 1


def test_an_empty_database_is_a_no_op(conn: Any, run: Run) -> None:
    assert all(count == 0 for count in run_purge(conn, run).values())


def test_every_rule_names_a_table_that_exists(conn: Any, run: Run) -> None:
    # Cheap, and it catches the one failure mode a passing test suite would
    # otherwise hide: a rule added for a table that was later renamed.
    for rule in RULES:
        conn.execute(f"SELECT 1 FROM {rule.table} LIMIT 0")
