"""Matching and delivery against a real Postgres.

Skipped unless TEST_DATABASE_URL is set, because the behaviour worth testing here
is behaviour a stub cannot have: the unique constraint that makes a repeat send
impossible, `ON CONFLICT DO NOTHING`, the plan expiry condition, and what a
LEFT JOIN yields when a user has no address. Every one of those has been a real
bug in this project, and each looked like working code until a database was
involved.

    docker run -d --name pg -e POSTGRES_PASSWORD=x -p 5433:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:x@localhost:5433/postgres uv run pytest tests/test_outbox_db.py

The database is wiped: point this at a throwaway instance, never at Supabase.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from worker import store  # noqa: E402
from worker.contracts.notify import SendResult  # noqa: E402
from worker.obs import Run  # noqa: E402
from worker.pipeline import outbox  # noqa: E402

URL = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "db" / "migrations"
CRITERIA = {
    "price_pcm": {"max": 2000},
    "bedrooms": {"min": 1, "max": 2},
    "areas": {"postcode_districts": ["SE16"]},
}
WIPE = """
TRUNCATE notifications, subscriptions, user_channels, user_tokens, payments, users,
         listing_price_log, listings, job_events, job_stages, job_runs
    RESTART IDENTITY CASCADE
"""


@pytest.fixture(scope="module")
def schema() -> Iterator[None]:
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        for name in ("0001_init.sql", "0004_source_failures.sql",
                     "0005_web.sql", "0006_plans.sql", "0007_no_alert_cap.sql"):
            conn.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
    yield


@pytest.fixture
def conn(schema: None) -> Iterator[Any]:
    with psycopg.connect(URL, autocommit=True, row_factory=psycopg.rows.dict_row) as c:
        c.execute(WIPE)
        yield c


@pytest.fixture
def run(conn: Any) -> Run:
    return Run(conn, job="hot", trigger="manual")


# ── fixtures for the rows under test ──────────────────────────────────────


def make_user(
    conn: Any, *, address: str = "555", status: str = "active",
    plan: str = "paid", plan_days: int | None = 14, plan_hours: int | None = None,
) -> int:
    # `plan_hours` wins when given. The day and hour warnings fire inside a
    # 24-hour window, which days cannot address.
    row = conn.execute(
        """
        INSERT INTO users (status, consent_at, consent_source, plan, plan_until)
        VALUES (%s, now(), 'test', %s,
                CASE WHEN %s::int IS NOT NULL THEN now() + make_interval(hours => %s::int)
                     WHEN %s::int IS NULL THEN NULL
                     ELSE now() + make_interval(days => %s::int) END)
        RETURNING id
        """,
        (status, plan, plan_hours, plan_hours, plan_days, plan_days),
    ).fetchone()
    user_id = int(row["id"])
    if address:
        conn.execute(
            "INSERT INTO user_channels (user_id, channel, address, verified_at) "
            "VALUES (%s, 'telegram', %s, now())",
            (user_id, address),
        )
    return user_id


def make_subscription(
    conn: Any, user_id: int, *, criteria: dict[str, Any] | None = None,
    backfill_hours: int = 1, active: bool = True,
) -> int:
    row = conn.execute(
        """
        INSERT INTO subscriptions (user_id, criteria, backfill_from, active)
        VALUES (%s, %s, now() - make_interval(hours => %s), %s)
        RETURNING id
        """,
        (
            user_id,
            psycopg.types.json.Jsonb(criteria if criteria is not None else CRITERIA),
            backfill_hours, active,
        ),
    ).fetchone()
    return int(row["id"])


def make_listing(
    conn: Any, external_id: str, *, price: int = 1500, bedrooms: int = 2,
    district: str = "SE16", minutes_old: int = 0,
) -> int:
    row = conn.execute(
        """
        INSERT INTO listings (source_key, external_id, url, price_pcm, bedrooms,
                              postcode_district, property_type, raw, first_seen_at)
        VALUES ('openrent', %s, %s, %s, %s, %s, 'flat', '{}'::jsonb,
                now() - make_interval(mins => %s))
        RETURNING id
        """,
        (external_id, f"https://example.test/{external_id}", price, bedrooms,
         district, minutes_old),
    ).fetchone()
    return int(row["id"])


class FakeNotifier:
    """A channel that records what it was asked to send."""

    key = "telegram"

    def __init__(self, result: SendResult | None = None) -> None:
        self.result = result or SendResult(ok=True, provider_msg_id="42")
        self.sent: list[tuple[str, Any]] = []

    def supports(self, kind: str) -> bool:
        return True

    def send(self, to: Any, alert: Any) -> SendResult:
        self.sent.append((to.address, alert))
        return self.result


@pytest.fixture
def notifier(monkeypatch: pytest.MonkeyPatch) -> FakeNotifier:
    fake = FakeNotifier()
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)
    return fake


def notification(conn: Any) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM notifications ORDER BY id LIMIT 1").fetchone()


def counts(conn: Any) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, count(*) AS n FROM notifications GROUP BY status"
    ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


# ── matching ──────────────────────────────────────────────────────────────


def test_a_matching_listing_is_queued(conn: Any, run: Run) -> None:
    user_id = make_user(conn)
    subscription_id = make_subscription(conn, user_id)
    listing_id = make_listing(conn, "1")

    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])

    queued = notification(conn)
    assert queued is not None
    assert queued["status"] == "queued"
    assert queued["user_id"] == user_id
    assert queued["subscription_id"] == subscription_id
    assert queued["channel"] == "telegram"


def test_a_listing_outside_the_criteria_is_not_queued(conn: Any, run: Run) -> None:
    make_subscription(conn, make_user(conn))
    ids = [
        make_listing(conn, "expensive", price=3000),
        make_listing(conn, "elsewhere", district="E14"),
        make_listing(conn, "too_big", bedrooms=4),
    ]
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=ids)
    assert counts(conn) == {}


def test_a_listing_is_never_queued_twice_for_the_same_user(conn: Any, run: Run) -> None:
    """The guarantee the outbox exists for. Running the stage again — after a
    crash, or because two subscriptions of one user both match — must not produce
    a second message."""
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    make_subscription(conn, user_id)  # a second filter of the same person
    listing_id = make_listing(conn, "1")

    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])

    assert counts(conn) == {"queued": 1}


def test_two_users_both_get_the_same_listing(conn: Any, run: Run) -> None:
    """Idempotency is per user, not per listing."""
    make_subscription(conn, make_user(conn, address="1"))
    make_subscription(conn, make_user(conn, address="2"))
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {"queued": 2}


def test_existing_stock_is_not_replayed_to_a_new_subscription(conn: Any, run: Run) -> None:
    """Otherwise the relationship opens with a burst of listings nobody asked for."""
    make_subscription(conn, make_user(conn), backfill_hours=0)
    old = make_listing(conn, "old", minutes_old=120)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[old])
    assert counts(conn) == {}




def test_every_match_is_delivered_however_many_there_are(conn: Any, run: Run) -> None:
    """There is no daily cap. Withholding a listing that matched is invisible to
    the person waiting for it; the way to get fewer messages is a narrower
    filter, which they control."""
    make_subscription(conn, make_user(conn))
    ids = [make_listing(conn, str(i)) for i in range(25)]
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=ids)
    assert counts(conn) == {"queued": 25}


def test_an_inactive_subscription_receives_nothing(conn: Any, run: Run) -> None:
    make_subscription(conn, make_user(conn), active=False)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {}


def test_a_stopped_user_receives_nothing(conn: Any, run: Run) -> None:
    make_subscription(conn, make_user(conn, status="stopped"))
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {}


def test_a_user_without_a_delivery_channel_is_skipped(conn: Any, run: Run) -> None:
    """The join is what excludes them. Queueing a message with nowhere to send it
    would fail later, per message, for ever."""
    make_subscription(conn, make_user(conn, address=""))
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {}


def test_an_empty_criteria_object_matches_everything(conn: Any, run: Run) -> None:
    """"No filters" must mean "send me everything", not "send me nothing"."""
    make_subscription(conn, make_user(conn), criteria={})
    outbox.queue_matches(conn, run, source_key="openrent",
                         listing_ids=[make_listing(conn, "1", price=9000, district="N1")])
    assert counts(conn) == {"queued": 1}


# ── delivery ──────────────────────────────────────────────────────────────


def queue_one(conn: Any, run: Run, **listing: Any) -> int:
    make_subscription(conn, make_user(conn))
    listing_id = make_listing(conn, "1", **listing)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])
    return listing_id


def test_a_queued_message_is_sent_and_recorded(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    queue_one(conn, run)
    assert outbox.drain(conn, run) == "ok"

    assert len(notifier.sent) == 1
    address, alert = notifier.sent[0]
    assert address == "555"
    assert alert.listing is not None and alert.listing.district == "SE16"

    sent = notification(conn)
    assert sent is not None
    assert sent["status"] == "sent"
    assert sent["provider_msg_id"] == "42"
    assert sent["sent_at"] is not None
    assert sent["attempts"] == 1


def test_a_sent_message_is_not_claimed_again(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    queue_one(conn, run)
    outbox.drain(conn, run)
    outbox.drain(conn, run)
    assert len(notifier.sent) == 1


def test_a_transient_failure_stays_queued_with_the_attempt_counted(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeNotifier(SendResult(ok=False, error="429: slow down", retryable=True))
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)
    queue_one(conn, run)

    assert outbox.drain(conn, run) == "degraded"
    row = notification(conn)
    assert row is not None
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["error"] is not None and "429" in row["error"]

    outbox.drain(conn, run)
    row = notification(conn)
    assert row is not None and row["attempts"] == 2


def test_retrying_gives_up_at_the_ceiling(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeNotifier(SendResult(ok=False, error="500: broken", retryable=True))
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)
    queue_one(conn, run)

    for _ in range(outbox.MAX_ATTEMPTS + 2):
        outbox.drain(conn, run)

    row = notification(conn)
    assert row is not None
    assert row["status"] == "failed"
    assert row["attempts"] == outbox.MAX_ATTEMPTS
    # The ceiling has to stop the sending too, not only the status.
    assert len(fake.sent) == outbox.MAX_ATTEMPTS


def test_an_unreachable_recipient_stops_collecting_for_them(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeNotifier(
        SendResult(ok=False, error="403: bot was blocked by the user", recipient_gone=True)
    )
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    ids = [make_listing(conn, "a"), make_listing(conn, "b")]
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=ids)

    outbox.drain(conn, run)

    user = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    assert user["status"] == "blocked"
    assert user["stopped_at"] is not None
    active = conn.execute(
        "SELECT count(*) AS n FROM subscriptions WHERE user_id = %s AND active", (user_id,)
    ).fetchone()
    assert active["n"] == 0
    # The rest of their queue is abandoned rather than retried one by one — and,
    # crucially, not sent: one message per queued row would reach someone who has
    # just blocked the bot before anything stopped it.
    assert counts(conn) == {"failed": 1, "skipped": 1}
    assert len(fake.sent) == 1


def test_an_unimplemented_channel_fails_only_its_own_message(conn: Any, run: Run) -> None:
    """`channels` holds rows for WhatsApp and email before either exists."""
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    conn.execute("UPDATE channels SET enabled = true WHERE key = 'whatsapp'")
    conn.execute("UPDATE notifications SET channel = 'whatsapp'")

    assert outbox.drain(conn, run) == "degraded"
    row = notification(conn)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] is not None and "whatsapp" in row["error"]


def test_a_dry_run_holds_everything(conn: Any, run: Run, notifier: FakeNotifier) -> None:
    queue_one(conn, run)
    assert outbox.drain(conn, run, dry_run=True) == "ok"
    assert notifier.sent == []
    assert counts(conn) == {"queued": 1}


def test_quiet_hours_hold_without_losing_anything(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    """Deferred, not dropped: nobody is messaged at 03:00 and nothing is lost."""
    queue_one(conn, run)
    assert outbox.drain(conn, run, suppress=True) == "ok"
    assert notifier.sent == []
    assert outbox.drain(conn, run) == "ok"
    assert len(notifier.sent) == 1


def test_an_empty_queue_is_not_a_problem(conn: Any, run: Run, notifier: FakeNotifier) -> None:
    assert outbox.drain(conn, run) == "ok"
    assert notifier.sent == []


# ── the run log ───────────────────────────────────────────────────────────


def test_the_stages_leave_rows_even_with_nothing_to_do(conn: Any, run: Run) -> None:
    """A missing stage row is how a crash looks. An empty one must look different."""
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[])
    outbox.drain(conn, run)
    stages = conn.execute(
        "SELECT stage, status FROM job_stages WHERE run_id = %s ORDER BY id", (run.id,)
    ).fetchall()
    assert [s["stage"] for s in stages] == ["match", "notify"]
    assert all(s["status"] == "ok" for s in stages)


def test_no_address_reaches_the_run_log(conn: Any, run: Run, notifier: FakeNotifier) -> None:
    """The repository is public and this table outlives the CI log."""
    queue_one(conn, run)
    outbox.drain(conn, run)
    events = conn.execute(
        "SELECT message, ctx FROM job_events WHERE run_id = %s", (run.id,)
    ).fetchall()
    blob = " ".join(f"{e['message']} {e['ctx']}" for e in events)
    assert "555" not in blob


# ── plans ─────────────────────────────────────────────────────────────────


def test_an_expired_plan_stops_producing_messages(conn: Any, run: Run) -> None:
    """Enforced in the matcher's own query, so it holds even if no expiry job has
    run. A sweeper that failed to run would keep sending, and that is the failure
    mode that costs money."""
    make_subscription(conn, make_user(conn, plan_days=-1))
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {}


def test_a_plan_without_an_expiry_keeps_working(conn: Any, run: Run) -> None:
    make_subscription(conn, make_user(conn, plan="comp", plan_days=None))
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {"queued": 1}




def test_an_expiry_is_announced_once(conn: Any, run: Run, notifier: FakeNotifier) -> None:
    user_id = make_user(conn, plan="trial", plan_days=-1)
    make_subscription(conn, user_id)

    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1
    address, alert = notifier.sent[0]
    assert address == "555"
    assert alert.kind == "expired"
    assert "trial has ended" in (alert.text or "")

    # Running again must not tell them a second time; an apologetic message
    # arriving every few minutes is worse than none.
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1


def test_a_day_out_is_warned_before_the_alerts_stop(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    """The point of warning ahead: "your alerts stopped an hour ago" is a message
    about a decision the person no longer gets to make."""
    make_subscription(conn, make_user(conn, plan="trial", plan_hours=23))

    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1
    _, alert = notifier.sent[0]
    assert alert.kind == "expiring"
    assert "ends tomorrow" in (alert.text or "")

    # And not again on the next tick.
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1


def test_an_hour_out_is_warned_separately_from_the_day(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    """Two stages, not one flag: the hour warning must still fire for someone who
    already had the day warning."""
    user_id = make_user(conn, plan="trial", plan_hours=23)
    make_subscription(conn, user_id)
    outbox.notify_plan_changes(conn, run)

    conn.execute(
        "UPDATE users SET plan_until = now() + interval '30 minutes' WHERE id = %s", (user_id,)
    )
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 2
    assert "about an hour" in (notifier.sent[1][1].text or "")


def test_paying_re_arms_the_warnings_with_nothing_reset(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    """The reason `plan_until` is in the unique key. A renewal moves the expiry,
    which makes a different key, which makes the warnings due again — no flag is
    cleared anywhere, so no code path can forget to clear one."""
    user_id = make_user(conn, plan="trial", plan_hours=23)
    make_subscription(conn, user_id)
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1

    # Paid mid-trial: the plan is extended well past the warning window.
    conn.execute(
        "UPDATE users SET plan = 'paid', plan_until = now() + interval '14 days' WHERE id = %s",
        (user_id,),
    )
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1, "nothing is due while the paid period is far off"

    # And when the paid period is itself a day out, they are warned again.
    conn.execute(
        "UPDATE users SET plan_until = now() + interval '20 hours' WHERE id = %s", (user_id,)
    )
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 2
    assert "subscription ends tomorrow" in (notifier.sent[1][1].text or "")


def test_a_plan_that_lapsed_unnoticed_is_told_it_ended_not_that_it_will(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    """Only the most urgent unsent stage is claimed. A worker that was down for a
    day must not send "ends tomorrow" about an expiry that already happened."""
    make_subscription(conn, make_user(conn, plan="trial", plan_hours=-5))
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1
    assert notifier.sent[0][1].kind == "expired"
    assert "has ended" in (notifier.sent[0][1].text or "")


def test_an_expiry_leaves_the_filter_in_place(conn: Any, run: Run, notifier: FakeNotifier) -> None:
    """Someone who renews should find their filter as they left it. Deleting it
    here would make an expiry indistinguishable from a /stop."""
    user_id = make_user(conn, plan_days=-1)
    make_subscription(conn, user_id)
    outbox.notify_plan_changes(conn, run)
    remaining = conn.execute(
        "SELECT count(*) AS n FROM subscriptions WHERE user_id = %s AND active", (user_id,)
    ).fetchone()
    assert remaining["n"] == 1


def test_a_live_plan_is_not_announced_as_expired(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    make_subscription(conn, make_user(conn, plan_days=14))
    outbox.notify_plan_changes(conn, run)
    assert notifier.sent == []


def test_renewing_starts_the_alerts_again(conn: Any, run: Run) -> None:
    """The whole point of enforcing in the query: nothing has to be undone."""
    user_id = make_user(conn, plan_days=-1)
    make_subscription(conn, user_id)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {}

    conn.execute(
        "UPDATE users SET plan_until = now() + interval '14 days' WHERE id = %s", (user_id,)
    )
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "2")])
    assert counts(conn) == {"queued": 1}
