from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from worker import store
from worker.contracts.notify import SendResult
from worker.obs import Run
from worker.pipeline import outbox

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
    return Run(conn, job="drain", trigger="manual")

def make_user(
    conn: Any, *, address: str = "555", status: str = "active",
    plan: str = "paid", plan_days: int | None = 14, plan_hours: int | None = None,
) -> int:

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

    user_id = make_user(conn)
    make_subscription(conn, user_id)
    make_subscription(conn, user_id)
    listing_id = make_listing(conn, "1")

    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])

    assert counts(conn) == {"queued": 1}

def test_two_users_both_get_the_same_listing(conn: Any, run: Run) -> None:

    make_subscription(conn, make_user(conn, address="1"))
    make_subscription(conn, make_user(conn, address="2"))
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {"queued": 2}

def test_existing_stock_is_not_replayed_to_a_new_subscription(conn: Any, run: Run) -> None:

    make_subscription(conn, make_user(conn), backfill_hours=0)
    old = make_listing(conn, "old", minutes_old=120)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[old])
    assert counts(conn) == {}

def test_every_match_is_delivered_however_many_there_are(conn: Any, run: Run) -> None:

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

    make_subscription(conn, make_user(conn, address=""))
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {}

def test_an_empty_criteria_object_matches_everything(conn: Any, run: Run) -> None:

    make_subscription(conn, make_user(conn), criteria={})
    outbox.queue_matches(conn, run, source_key="openrent",
                         listing_ids=[make_listing(conn, "1", price=9000, district="N1")])
    assert counts(conn) == {"queued": 1}

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

    assert counts(conn) == {"failed": 1, "skipped": 1}
    assert len(fake.sent) == 1

def test_an_unimplemented_channel_fails_only_its_own_message(conn: Any, run: Run) -> None:

    # Email, not WhatsApp: WhatsApp has been implemented since this was written,
    # and the test went on asserting it had not. It never noticed, because it
    # only runs with TEST_DATABASE_URL set.
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    conn.execute("UPDATE channels SET enabled = true WHERE key = 'email'")
    conn.execute("UPDATE notifications SET channel = 'email'")

    assert outbox.drain(conn, run) == "degraded"
    row = notification(conn)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] is not None and "email" in row["error"]

def photo_pending(conn: Any, listing_id: int, *, looked: bool) -> None:

    # A source message carrying a photograph, which either has or has not been
    # handed to WhatsApp yet.
    conn.execute(
        """
        INSERT INTO source_messages
               (source_key, reader, chat, external_id, received_at, body, links,
                media_kinds, content_hash, status, listing_id, wa_media_checked_at)
        VALUES ('openrent', 'r1', 'feed', %s, now(), 'body', '{}',
                ARRAY['photo'], %s, 'parsed', %s,
                CASE WHEN %s THEN now() ELSE NULL END)
        """,
        (f"m{listing_id}", f"hash-{listing_id}", listing_id, looked),
    )

def test_a_whatsapp_alert_waits_for_its_photograph(conn: Any, run: Run) -> None:

    # WhatsApp is sent the picture itself, so an alert that overtakes its own
    # upload arrives as plain text and is never revisited.
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    listing_id = make_listing(conn, "1")
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])
    conn.execute("UPDATE channels SET enabled = true WHERE key = 'whatsapp'")
    conn.execute("UPDATE notifications SET channel = 'whatsapp'")
    photo_pending(conn, listing_id, looked=False)

    assert store.claim_queued(conn, limit=10, max_attempts=3) == []
    # Held back, not claimed: claiming spends an attempt, and waiting is not a
    # failed attempt at anything.
    assert int(notification(conn)["attempts"]) == 0

def test_it_stops_waiting_once_the_photograph_is_settled(conn: Any, run: Run) -> None:
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    listing_id = make_listing(conn, "1")
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])
    conn.execute("UPDATE channels SET enabled = true WHERE key = 'whatsapp'")
    conn.execute("UPDATE notifications SET channel = 'whatsapp'")
    # Looked at — whether a photograph came back or not, there is nothing left
    # to wait for.
    photo_pending(conn, listing_id, looked=True)

    assert len(store.claim_queued(conn, limit=10, max_attempts=3)) == 1

def test_it_gives_up_waiting_rather_than_holding_a_listing_for_ever(
    conn: Any, run: Run
) -> None:
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    listing_id = make_listing(conn, "1")
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])
    conn.execute("UPDATE channels SET enabled = true WHERE key = 'whatsapp'")
    conn.execute(
        "UPDATE notifications SET channel = 'whatsapp', created_at = now() - interval '1 hour'"
    )
    photo_pending(conn, listing_id, looked=False)

    # A stuck upload must not silence the alerts. Late and plain beats never.
    assert len(store.claim_queued(conn, limit=10, max_attempts=3)) == 1

def test_telegram_never_waits_because_it_previews_the_link(conn: Any, run: Run) -> None:
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    listing_id = make_listing(conn, "1")
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[listing_id])
    photo_pending(conn, listing_id, looked=False)

    assert len(store.claim_queued(conn, limit=10, max_attempts=3)) == 1

def test_a_dry_run_holds_everything(conn: Any, run: Run, notifier: FakeNotifier) -> None:
    queue_one(conn, run)
    assert outbox.drain(conn, run, dry_run=True) == "ok"
    assert notifier.sent == []
    assert counts(conn) == {"queued": 1}

def test_quiet_hours_hold_without_losing_anything(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:

    queue_one(conn, run)
    assert outbox.drain(conn, run, suppress=True) == "ok"
    assert notifier.sent == []
    assert outbox.drain(conn, run) == "ok"
    assert len(notifier.sent) == 1

def test_an_empty_queue_is_not_a_problem(conn: Any, run: Run, notifier: FakeNotifier) -> None:
    assert outbox.drain(conn, run) == "ok"
    assert notifier.sent == []

def test_the_stages_leave_rows_even_with_nothing_to_do(conn: Any, run: Run) -> None:

    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[])
    outbox.drain(conn, run)
    stages = conn.execute(
        "SELECT stage, status FROM job_stages WHERE run_id = %s ORDER BY id", (run.id,)
    ).fetchall()
    assert [s["stage"] for s in stages] == ["match", "notify"]
    assert all(s["status"] == "ok" for s in stages)

def test_no_address_reaches_the_run_log(conn: Any, run: Run, notifier: FakeNotifier) -> None:

    queue_one(conn, run)
    outbox.drain(conn, run)
    events = conn.execute(
        "SELECT message, ctx FROM job_events WHERE run_id = %s", (run.id,)
    ).fetchall()
    blob = " ".join(f"{e['message']} {e['ctx']}" for e in events)
    assert "555" not in blob

def test_an_expired_plan_stops_producing_messages(conn: Any, run: Run) -> None:

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

    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1

def test_a_day_out_is_warned_before_the_alerts_stop(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:

    make_subscription(conn, make_user(conn, plan="trial", plan_hours=23))

    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1
    _, alert = notifier.sent[0]
    assert alert.kind == "expiring"
    assert "ends tomorrow" in (alert.text or "")

    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1

def test_an_hour_out_is_warned_separately_from_the_day(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:

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

    user_id = make_user(conn, plan="trial", plan_hours=23)
    make_subscription(conn, user_id)
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1

    conn.execute(
        "UPDATE users SET plan = 'paid', plan_until = now() + interval '14 days' WHERE id = %s",
        (user_id,),
    )
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1, "nothing is due while the paid period is far off"

    conn.execute(
        "UPDATE users SET plan_until = now() + interval '20 hours' WHERE id = %s", (user_id,)
    )
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 2
    assert "subscription ends tomorrow" in (notifier.sent[1][1].text or "")

def test_a_plan_that_lapsed_unnoticed_is_told_it_ended_not_that_it_will(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:

    make_subscription(conn, make_user(conn, plan="trial", plan_hours=-5))
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1
    assert notifier.sent[0][1].kind == "expired"
    assert "has ended" in (notifier.sent[0][1].text or "")

def test_an_expiry_leaves_the_filter_in_place(conn: Any, run: Run, notifier: FakeNotifier) -> None:

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

    user_id = make_user(conn, plan_days=-1)
    make_subscription(conn, user_id)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    assert counts(conn) == {}

    conn.execute(
        "UPDATE users SET plan_until = now() + interval '14 days' WHERE id = %s", (user_id,)
    )
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "2")])
    assert counts(conn) == {"queued": 1}
