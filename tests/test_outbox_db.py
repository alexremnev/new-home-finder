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
TRUNCATE wa_messages, notifications, subscriptions, user_channels, user_tokens,
         payments, users, listing_price_log, listings,
         job_events, job_stages, job_runs
    RESTART IDENTITY CASCADE
"""

@pytest.fixture(scope="module")
def schema() -> Iterator[None]:
    # Every migration, in order. A short list was enough when these tests only
    # covered matching and retries; the WhatsApp ones read `source_messages`,
    # `last_inbound_at` and `window_asked_at`, none of which exist that early —
    # and the whole chain is the only setup that matches what the server has.
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
    yield

@pytest.fixture
def conn(schema: None) -> Iterator[Any]:
    with psycopg.connect(URL, autocommit=True, row_factory=psycopg.rows.dict_row) as c:
        c.execute(WIPE)
        # `sources` is seed data and cannot be truncated, so the one column a
        # test here changes is put back by hand. See 0060.
        c.execute("UPDATE sources SET announces = true WHERE NOT announces")
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
        # Kept apart from `sent` so the address assertions above stay as they
        # are. What the recipient carries is its own fact: on WhatsApp
        # `last_inbound` is what decides between a sentence with a button on it
        # and nothing at all, and the plan notices were building it without.
        self.to: list[Any] = []

    def supports(self, kind: str) -> bool:
        return True

    def send(self, to: Any, alert: Any) -> SendResult:
        self.sent.append((to.address, alert))
        self.to.append(to)
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

def test_a_muted_source_is_matched_against_nobody(conn: Any, run: Run) -> None:
    """A source read and stored but never sent. See 0060.

    The whole of what retiring the Telegram feed does: it keeps arriving, it
    keeps being compared against the scrapers in `listing_sightings`, and it
    reaches no subscriber. The listing itself matches perfectly — the only
    reason nothing is queued is the source it came through.
    """

    make_subscription(conn, make_user(conn))
    conn.execute("UPDATE sources SET announces = false WHERE key = 'tg_feed'")

    outbox.queue_matches(
        conn, run, source_key="tg_feed", listing_ids=[make_listing(conn, "1")]
    )

    assert counts(conn) == {}

def test_a_source_the_reference_table_has_never_heard_of_announces_nothing(
    conn: Any, run: Run
) -> None:
    """Fail closed. A key with no row is a caller's mistake, not permission."""

    make_subscription(conn, make_user(conn))

    outbox.queue_matches(
        conn, run, source_key="nosuchportal", listing_ids=[make_listing(conn, "1")]
    )

    assert counts(conn) == {}

def test_muting_one_source_leaves_the_others_sending(conn: Any, run: Run) -> None:
    make_subscription(conn, make_user(conn))
    conn.execute("UPDATE sources SET announces = false WHERE key = 'tg_feed'")

    outbox.queue_matches(
        conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")]
    )

    assert counts(conn) == {"queued": 1}

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

# ── the WhatsApp allowance: thirty days or nine hundred messages ───────────
#
# Every one of these asks the same question through `user_entitlement`, because
# that is the point of the view: the share, the daily cap, the notice stage and
# the dashboard all read one answer, and a test that bypassed it would be
# testing a copy nobody uses.
#
# What is counted changed in 0063: Meta bills every message, so the ledger
# `wa_messages` is the thing the allowance is measured over and a listing alert
# is only one of the six kinds in it.


def wa_subscriber(conn: Any, *, allowance: int | None = 900,
                  days_left: int = 20, used: int = 0, kind: str = "listing") -> int:
    """A paying WhatsApp account, this far into a period, having had `used`."""

    user_id = make_user(conn, address="", plan="paid", plan_hours=days_left * 24)
    conn.execute(
        "UPDATE users SET plan_from = now() - interval '10 days' WHERE id = %s",
        (user_id,),
    )
    conn.execute("UPDATE plans SET message_allowance = %s WHERE key = 'paid'", (allowance,))
    conn.execute(
        "INSERT INTO user_channels (user_id, channel, address, verified_at, "
        "last_inbound_at) VALUES (%s, 'whatsapp', %s, now(), now())",
        (user_id, f"4477009{user_id:05d}"),
    )
    # Straight into the ledger, which is what the view reads, and in one
    # statement: `used` is nine hundred in half these tests and nine hundred
    # round trips apiece is a slow suite for no extra coverage. The two paths
    # that really write it — `mark_sent` and `record_wa_message` — have their
    # own tests below; these are about what the number then means.
    if used:
        conn.execute(
            """
            INSERT INTO wa_messages (user_id, kind, cost_micros)
            SELECT %s, %s, coalesce((SELECT wa_service_micros FROM plan_settings), 0)
              FROM generate_series(1, %s)
            """,
            (user_id, kind, used),
        )
    return user_id


def entitlement(conn: Any, user_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM user_entitlement WHERE user_id = %s", (user_id,)
    ).fetchone()
    assert row is not None
    return row


def test_telegram_has_no_message_allowance_whatever_the_plan_says(
    conn: Any,
) -> None:
    # A message allowance is a property of the channel that charges per
    # message, and Telegram is not it. The trial row is shared by both
    # messengers — one row, two products — so without the gate in
    # `user_entitlement` giving the trial thirty messages would have given
    # them to Telegram too, where delivery is free and a limit means nothing.
    #
    # It is also what the counter already did: `messages_used` counts
    # `wa_messages`, which holds nothing but WhatsApp, so on Telegram the
    # allowance could never be reached. 0068 says so instead of relying on it.
    telegram = make_user(conn, plan="trial", plan_hours=48)
    conn.execute("UPDATE plans SET message_allowance = 30 WHERE key = 'trial'")

    one = entitlement(conn, telegram)
    assert one["message_allowance"] is None
    assert one["messages_left"] is None
    assert one["out_of_messages"] is False
    # And so it is live on days alone, which is the whole of the rule there.
    assert one["live"] is True


def test_a_whatsapp_trial_is_metered_like_any_other_plan(conn: Any) -> None:
    # The other half: on the channel that bills us, the trial has the same
    # shape as the week and the month — used against included — so the
    # dashboard can show 5/30 beside 55/200 and 350/900 as one kind of fact.
    conn.execute("UPDATE plans SET message_allowance = 30 WHERE key = 'trial'")
    user_id = make_user(conn, address="", plan="trial", plan_hours=24)
    conn.execute(
        "INSERT INTO user_channels (user_id, channel, address, verified_at) "
        "VALUES (%s, 'whatsapp', %s, now())",
        (user_id, f"4477009{user_id:05d}"),
    )
    conn.execute(
        """
        INSERT INTO wa_messages (user_id, kind, cost_micros)
        SELECT %s, 'listing', 0 FROM generate_series(1, 5)
        """,
        (user_id,),
    )

    one = entitlement(conn, user_id)
    assert (int(one["messages_used"]), int(one["message_allowance"])) == (5, 30)
    assert int(one["messages_left"]) == 25
    assert one["live"] is True


def test_a_month_with_messages_left_is_live(conn: Any) -> None:
    one = entitlement(conn, wa_subscriber(conn, used=10))

    assert one["live"] is True
    assert (one["messages_used"], one["messages_left"]) == (10, 890)
    assert one["delivery_share"] == 100


def test_the_period_ends_on_the_allowance_with_days_still_left(conn: Any) -> None:
    # The case the date cannot explain, and the reason none of this could stay
    # as `plan_until > now()`.
    one = entitlement(conn, wa_subscriber(conn, used=900, days_left=20))

    assert one["live"] is False
    assert one["out_of_messages"] is True
    assert one["out_of_days"] is False
    # And to nothing, not to the lapsed fifth: this is WhatsApp, where the
    # fallback would be a standing bill for somebody who has stopped paying.
    assert one["delivery_share"] == 0


def test_going_past_the_allowance_does_not_go_negative(conn: Any) -> None:
    # Delivery is claimed in batches, so the count can overshoot by a batch.
    # "-3 alerts left" on the dashboard would read as a bug in the counting.
    one = entitlement(conn, wa_subscriber(conn, used=903))

    assert one["messages_left"] == 0
    assert one["live"] is False


def test_an_unmetered_plan_is_never_out_of_messages(conn: Any) -> None:
    # Telegram. Nothing is counted, so a busy month cannot end a plan that is
    # free to deliver on.
    one = entitlement(conn, wa_subscriber(conn, allowance=None, used=50))

    assert one["out_of_messages"] is False
    assert one["live"] is True
    assert one["messages_left"] is None


def test_only_what_was_sent_in_this_period_counts(conn: Any) -> None:
    # Paying again moves `plan_from`, which is what makes the next month a
    # fresh nine hundred rather than a continuation of the last one.
    user_id = wa_subscriber(conn, used=900)
    assert entitlement(conn, user_id)["live"] is False

    conn.execute("UPDATE users SET plan_from = now() WHERE id = %s", (user_id,))
    renewed = entitlement(conn, user_id)

    assert (renewed["messages_used"], renewed["live"]) == (0, True)


def test_every_kind_of_message_is_charged_to_the_allowance(conn: Any) -> None:
    # The inverse of what 0057 did, and for the reason 0063 gives: Meta bills
    # every message, ours and the ones answering theirs, so the bound has to
    # be on the thing the bill is on. A digest the subscriber never asked for
    # costs exactly as much as a listing they did.
    user_id = wa_subscriber(conn, used=0)
    for kind in ("listing", "digest", "checkin", "notice", "reply", "receipt"):
        store.record_wa_message(conn, user_id, kind)

    assert entitlement(conn, user_id)["messages_used"] == 6


def test_a_listing_alert_writes_itself_into_the_ledger(conn: Any) -> None:
    # The path that matters most, because it is the one that runs thousands of
    # times a day: `mark_sent` is where a delivered alert becomes a metered
    # message, so that no caller has to remember to do it.
    user_id = wa_subscriber(conn, used=0)
    listing_id = make_listing(conn, "metered")
    row = conn.execute(
        "INSERT INTO notifications (user_id, listing_id, channel, kind, status) "
        "VALUES (%s, %s, 'whatsapp', 'new_listing', 'queued') RETURNING id",
        (user_id, listing_id),
    ).fetchone()
    assert row is not None

    store.mark_sent(conn, int(row["id"]), provider_msg_id="wamid.test")

    ledger = conn.execute(
        "SELECT kind, provider_msg_id, billed_as, cost_micros FROM wa_messages "
        "WHERE user_id = %s",
        (user_id,),
    ).fetchall()
    # `service`, and not by default: the queue holds any alert whose window is
    # shut, so a delivered listing is always the free-form rate.
    assert [
        (r["kind"], r["provider_msg_id"], r["billed_as"], int(r["cost_micros"]))
        for r in ledger
    ] == [("listing", "wamid.test", "service", 22000)]
    assert entitlement(conn, user_id)["messages_used"] == 1

    # Idempotent through `notification_id`: a row marked sent twice is one
    # message and must not spend two of the nine hundred.
    store.mark_sent(conn, int(row["id"]), provider_msg_id="wamid.test")
    assert entitlement(conn, user_id)["messages_used"] == 1


def test_a_telegram_alert_is_not_metered(conn: Any) -> None:
    # Nothing to meter where delivery is free, and a row here would make the
    # Telegram plans look like they had a cost to control.
    user_id = make_user(conn, plan="paid")
    listing_id = make_listing(conn, "free-channel")
    row = conn.execute(
        "INSERT INTO notifications (user_id, listing_id, channel, kind, status) "
        "VALUES (%s, %s, 'telegram', 'new_listing', 'queued') RETURNING id",
        (user_id, listing_id),
    ).fetchone()
    assert row is not None

    store.mark_sent(conn, int(row["id"]), provider_msg_id="tg.1")

    assert conn.execute("SELECT count(*) AS n FROM wa_messages").fetchone()["n"] == 0


def test_the_price_is_stamped_on_each_message_as_it_is_sent(conn: Any) -> None:
    # So that a price change does not rewrite a month that has already been
    # billed. `plan_settings` is one row and is not in WIPE, hence the reset.
    user_id = wa_subscriber(conn, used=0)
    conn.execute("UPDATE plan_settings SET wa_service_micros = 4200")
    try:
        store.record_wa_message(conn, user_id, "reply")
    finally:
        conn.execute("UPDATE plan_settings SET wa_service_micros = 22000")

    # The second message is charged at the restored price, and the first keeps
    # what it was sent under.
    store.record_wa_message(conn, user_id, "reply")

    spent = conn.execute(
        "SELECT count(*) AS n, sum(cost_micros) AS micros FROM wa_messages "
        "WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    assert spent is not None
    assert (int(spent["n"]), int(spent["micros"])) == (2, 26200)


def test_the_three_rates_are_charged_apart(conn: Any) -> None:
    # Meta prices a free-form message inside the window, the listing template,
    # and the marketing template that carries a payment button, all
    # differently — and the dearest is the rarest, so one price for all three
    # would misreport exactly the messages that cost the most. The notifier
    # says which went out; this is what the ledger does with the answer.
    user_id = wa_subscriber(conn, used=0)
    conn.execute("UPDATE plan_settings SET wa_template_micros = 50000")
    try:
        store.record_wa_message(conn, user_id, "notice", billed_as="service")
        store.record_wa_message(conn, user_id, "notice", billed_as="utility")
        store.record_wa_message(conn, user_id, "notice", billed_as="marketing")
    finally:
        conn.execute("UPDATE plan_settings SET wa_template_micros = 0")

    rows = conn.execute(
        "SELECT billed_as, cost_micros FROM wa_messages WHERE user_id = %s "
        "ORDER BY id",
        (user_id,),
    ).fetchall()
    # The marketing rate is the one figure anybody has read off a price list —
    # $0.0635, set by 0066 — so unlike the other template rate it is not 0.
    assert [(r["billed_as"], int(r["cost_micros"])) for r in rows] == [
        ("service", 22000),
        ("utility", 50000),
        ("marketing", 63500),
    ]
    # All three still count against the allowance: the bound is on messages.
    assert entitlement(conn, user_id)["messages_used"] == 3


def test_a_template_with_no_rate_is_counted_and_not_costed(conn: Any) -> None:
    # The honest shape of an unset price: the message is in the ledger, so the
    # allowance and the volume are right, and the money is understated rather
    # than invented. The dashboard says how many are uncosted.
    user_id = wa_subscriber(conn, used=0)
    store.record_wa_message(conn, user_id, "notice", billed_as="utility")

    row = conn.execute(
        "SELECT billed_as, cost_micros FROM wa_messages WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    assert row is not None
    assert (row["billed_as"], int(row["cost_micros"])) == ("utility", 0)
    assert entitlement(conn, user_id)["messages_used"] == 1


def test_a_lapsed_whatsapp_plan_is_not_sent_a_digest(conn: Any) -> None:
    # The digest is the one message nobody asked for, and on WhatsApp it is
    # billed like the rest. A finished plan there falls back to no listings at
    # all (0058), so this was a nightly charge to tell somebody who had
    # stopped paying that nothing had been sent to them.
    user_id = wa_subscriber(conn, used=900)
    make_subscription(conn, user_id)

    assert store.daily_digests(conn) == []


def test_a_live_whatsapp_plan_is_still_sent_its_digest(conn: Any) -> None:
    # The other side of it, so the rule above is "not to somebody receiving
    # nothing" rather than "not on WhatsApp".
    user_id = wa_subscriber(conn, used=10)
    make_subscription(conn, user_id)

    assert [int(r["user_id"]) for r in store.daily_digests(conn)] == [user_id]


def test_a_spent_allowance_brings_the_daily_cap_back(conn: Any) -> None:
    # "Paid" exempts somebody from the thirty-a-day cap, and a spent allowance
    # has to stop meaning paid — or the period would end with no consequence
    # for what it costs.
    user_id = wa_subscriber(conn, used=900)
    exempt = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM user_entitlement e "
        "WHERE e.user_id = %s AND e.priced AND e.live) AS exempt",
        (user_id,),
    ).fetchone()

    assert exempt is not None and exempt["exempt"] is False


def test_a_finished_telegram_plan_still_gets_a_share(conn: Any) -> None:
    # The free tier is the best argument for the paid one, and on Telegram it
    # costs nothing to make — so the fallback stays, at the tenth 0069 put it
    # back to.
    user_id = make_user(conn, plan="paid", plan_days=None, plan_hours=None)
    conn.execute(
        "UPDATE users SET plan_until = now() - interval '1 day' WHERE id = %s",
        (user_id,),
    )

    assert entitlement(conn, user_id)["delivery_share"] == 10


def test_a_live_trial_is_told_what_it_will_fall_back_to(conn: Any) -> None:
    # The notice goes out the day before the end, while the trial is still
    # delivering everything. `delivery_share` is therefore a hundred, and
    # reading it for "what happens next" is what made the message say the
    # alerts would stop — see 0058. `lapsed_share` answers the question the
    # notice is actually asking, and answers it whether or not the plan has
    # ended yet.
    user_id = make_user(conn, plan="trial", plan_days=None, plan_hours=1)
    one = entitlement(conn, user_id)

    assert one["live"] is True
    assert one["delivery_share"] == 100
    assert one["lapsed_share"] == 10

    conn.execute(
        "UPDATE users SET plan_until = now() + interval '3 minutes' WHERE id = %s", (user_id,)
    )
    notices = store.claim_plan_notices(conn)
    assert [row["stage"] for row in notices] == ["hour"]
    assert int(notices[0]["lapsed_share"]) == 10


def test_a_live_whatsapp_month_falls_back_to_nothing(conn: Any) -> None:
    # Nought there, and for the reason 0057 gives: every WhatsApp message is
    # billed, so a free share is a standing bill for somebody who has stopped
    # paying. The notice says the alerts stop, which is what happens.
    user_id = wa_subscriber(conn, used=10)

    assert entitlement(conn, user_id)["lapsed_share"] == 0


def test_the_notice_for_a_spent_allowance_is_claimed_once(conn: Any) -> None:
    user_id = wa_subscriber(conn, used=900)
    make_subscription(conn, user_id)

    first = store.claim_plan_notices(conn)
    assert [row["stage"] for row in first] == ["spent"]
    assert int(first[0]["message_allowance"]) == 900
    # Said once per period: the unique key is (user, stage, plan_until), so a
    # drain every minute does not say it sixty times an hour.
    assert store.claim_plan_notices(conn) == []


def test_a_period_that_ended_on_its_allowance_is_not_announced_twice(
    conn: Any,
) -> None:
    # `spent` and `expired` are two names for one event — the period is over —
    # and they used to be two claims. A subscriber who used their allowance
    # early got both: "the alerts have stopped" when the messages ran out, and
    # the same sentence again when the days did, weeks later. The second was
    # also the marketing template, so the duplicate was the expensive one.
    user_id = wa_subscriber(conn, used=900, days_left=20)
    make_subscription(conn, user_id)

    claimed = store.claim_plan_notices(conn)
    assert [row["stage"] for row in claimed] == ["spent"]
    store.mark_plan_notice_delivered(
        conn, user_id, "spent", claimed[0]["plan_until"]
    )

    # The days run out too. Nothing more to say, and nothing more to buy.
    conn.execute(
        "UPDATE users SET plan_until = now() - interval '1 hour' WHERE id = %s",
        (user_id,),
    )
    assert store.claim_plan_notices(conn) == []


def test_a_period_nobody_was_told_about_is_announced_when_the_days_run_out(
    conn: Any,
) -> None:
    # The other half of the rule above, and the reason it asks about delivery
    # rather than about the claim. A row in `plan_notices` is written before
    # the send and kept even when the send failed for good — so if `spent`
    # cancelled `expired` on the strength of the row alone, a period that
    # ended would be announced to nobody.
    user_id = wa_subscriber(conn, used=900, days_left=20)
    make_subscription(conn, user_id)

    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["spent"]
    # Claimed and never delivered: no `mark_plan_notice_delivered` call.

    conn.execute(
        "UPDATE users SET plan_until = now() - interval '1 hour' WHERE id = %s",
        (user_id,),
    )
    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["expired"]


def test_a_notice_still_says_itself_once_per_drain(conn: Any) -> None:
    # The same-stage guard asks nothing about delivery, because it is what
    # stops a drain every five minutes repeating itself — and a send that
    # failed for good would otherwise be retried for ever.
    user_id = wa_subscriber(conn, used=900, days_left=20)
    make_subscription(conn, user_id)

    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["spent"]
    assert store.claim_plan_notices(conn) == []


def test_the_warning_before_the_end_is_not_the_same_claim(conn: Any) -> None:
    # `hour` is the warning, not a second account of the end, so it keeps its
    # own claim and the expiry still follows it.
    user_id = wa_subscriber(conn, used=0, days_left=20)
    make_subscription(conn, user_id)
    conn.execute(
        "UPDATE users SET plan_until = now() + interval '3 minutes' WHERE id = %s",
        (user_id,),
    )
    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["hour"]

    conn.execute(
        "UPDATE users SET plan_until = now() - interval '1 minute' WHERE id = %s",
        (user_id,),
    )
    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["expired"]


def test_out_of_days_outranks_out_of_messages(conn: Any) -> None:
    # Both true at once. "You have used all 900" would be answering the
    # smaller question about a period that is over either way.
    user_id = wa_subscriber(conn, used=900, days_left=20)
    make_subscription(conn, user_id)
    conn.execute(
        "UPDATE users SET plan_until = now() - interval '1 hour' WHERE id = %s",
        (user_id,),
    )

    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["expired"]


def test_a_notice_that_could_not_be_sent_is_offered_again(conn: Any) -> None:
    # The claim is an INSERT, so a failed send used to be a notice nobody ever
    # heard: the row stood and the stage never came round again. On WhatsApp
    # that is the ordinary case for a paying subscriber — the hour warning
    # travels as `expiring`, which has no template, and a month's subscriber
    # has not written in for weeks, so the window is shut.
    user_id = make_user(conn, plan="paid", plan_days=None, plan_hours=1)
    make_subscription(conn, user_id)
    conn.execute(
        "UPDATE users SET plan_until = now() + interval '3 minutes' WHERE id = %s",
        (user_id,),
    )

    first = store.claim_plan_notices(conn)
    assert [row["stage"] for row in first] == ["hour"]
    assert store.claim_plan_notices(conn) == []

    store.release_plan_notice(conn, user_id, "hour", first[0]["plan_until"])

    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["hour"]

def test_a_released_notice_becomes_the_expiry_once_the_plan_has_gone(conn: Any) -> None:
    # Which is why releasing cannot loop for ever: the stage is worked out
    # afresh, so an hour warning nobody could be told turns into the expiry
    # notice — a different stage, a fresh claim, and the one template that can
    # reach a shut window.
    user_id = make_user(conn, plan="paid", plan_days=None, plan_hours=1)
    make_subscription(conn, user_id)
    conn.execute(
        "UPDATE users SET plan_until = now() + interval '3 minutes' WHERE id = %s",
        (user_id,),
    )
    claimed = store.claim_plan_notices(conn)
    store.release_plan_notice(conn, user_id, "hour", claimed[0]["plan_until"])
    conn.execute(
        "UPDATE users SET plan_until = now() - interval '1 minute' WHERE id = %s",
        (user_id,),
    )

    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["expired"]

def wa_plan_ending(
    conn: Any, *, plan: str = "paid", minutes_left: int = 3,
    inbound_minutes: int = 60,
) -> int:
    """A WhatsApp account whose plan ends shortly, and whose window is open.

    `inbound_minutes` is how long ago they last wrote in, so a value above
    24 * 60 is the same account with a shut window. It is the only difference
    that matters to a plan notice on WhatsApp: there are no templates, so an
    open window is a sentence with a payment button on it and a shut one is
    nothing.
    """

    user_id = make_user(conn, address="", plan=plan, plan_days=None,
                        plan_hours=None)
    conn.execute(
        "UPDATE users SET plan_from = now() - interval '10 days', "
        "plan_until = now() + make_interval(mins => %s) WHERE id = %s",
        (minutes_left, user_id),
    )
    make_subscription(conn, user_id)
    conn.execute(
        """
        INSERT INTO user_channels (user_id, channel, address, verified_at,
                                   last_inbound_at)
        VALUES (%s, 'whatsapp', %s, now(), now() - make_interval(mins => %s))
        """,
        (user_id, f"4477008{user_id:05d}", inbound_minutes),
    )
    return user_id

def test_a_plan_notice_carries_when_they_last_wrote_in(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    # The bug this exists for: the recipient was built from the channel and the
    # address alone, so `window_open(None)` was false for everybody and every
    # plan notice on WhatsApp took the closed-window path. The hour warning has
    # no template, so the one message carrying the payment button was refused
    # even for somebody who had written in minutes earlier.
    wa_plan_ending(conn, inbound_minutes=60)

    outbox.notify_plan_changes(conn, run)

    assert len(notifier.to) == 1
    assert notifier.to[0].last_inbound is not None

def test_a_paid_month_is_warned_before_it_ends_not_only_a_trial(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    # Same stage, same path, same button — the only thing the plan changes is
    # the noun. Worth its own test because the trial's window is open by
    # construction (it runs a day from the message that started it) and a
    # month's is open only because the check-in has been holding it so: the
    # paying subscriber is who the warning was being lost for.
    wa_plan_ending(conn, plan="paid", minutes_left=3)

    outbox.notify_plan_changes(conn, run)

    assert len(notifier.sent) == 1
    _, alert = notifier.sent[0]
    assert alert.kind == "expiring"
    assert "subscription ends in a few minutes" in (alert.text or "")
    assert [a.url is not None for a in alert.actions] == [True]
    assert notifier.to[0].last_inbound is not None

def test_accounts_that_lapsed_long_ago_do_not_crowd_out_todays_warning(
    conn: Any
) -> None:
    # `due` was limited before anything asked who still needed telling, and it
    # is ordered by the oldest expiry first — so every account that had ever
    # lapsed sat at the front of the batch for ever, claimed, returning
    # nothing. Past the limit the INSERT had nothing left to claim and somebody
    # whose plan ends in minutes was never produced at all.
    for n in range(3):
        old = make_user(conn, address="", plan="paid", plan_days=None,
                        plan_hours=None)
        conn.execute(
            "UPDATE users SET plan_until = now() - make_interval(days => %s) "
            "WHERE id = %s",
            (n + 1, old),
        )
        make_subscription(conn, old)
        conn.execute(
            "INSERT INTO user_channels (user_id, channel, address, verified_at) "
            "VALUES (%s, 'whatsapp', %s, now())",
            (old, f"4477007{old:05d}"),
        )
        # Already told, or told as far as a shut window allows — which is the
        # state they stay in.
        conn.execute(
            "INSERT INTO plan_notices (user_id, stage, plan_until, plan) "
            "SELECT %s, 'expired', plan_until, plan FROM users WHERE id = %s",
            (old, old),
        )

    today = wa_plan_ending(conn, minutes_left=3)

    # A limit smaller than the crowd, so the test fails on the ordering rather
    # than on the two hundred the worker uses.
    claimed = store.claim_plan_notices(conn, limit=2)

    assert [int(row["user_id"]) for row in claimed] == [today]
    assert [row["stage"] for row in claimed] == ["hour"]

def test_a_notice_that_failed_with_the_window_open_is_offered_again(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An open window and a send that failed anyway is a failure of the moment —
    # a timeout, a 5xx, a rate limit. The claim goes back so the next drain
    # tries again, which is what it is for.
    wa_plan_ending(conn, inbound_minutes=60)
    fake = FakeNotifier(SendResult(ok=False, error="503", retryable=True))
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)

    outbox.notify_plan_changes(conn, run)
    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["hour"]

def test_a_notice_that_failed_with_the_window_shut_keeps_its_claim(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A shut window is not a delay. With no templates there is no second path,
    # and nothing reopens it for the person a plan notice is about: the
    # check-in is not asked within the hour before a plan ends, nor of a lapsed
    # account at all. Handed back, this would be the same impossible question
    # every two minutes for ever — and the stage turns into `expired` by
    # itself, which is a fresh claim and another look at the window.
    wa_plan_ending(conn, inbound_minutes=30 * 60)
    fake = FakeNotifier(
        SendResult(ok=False, error="outside the 24h window", retryable=True)
    )
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)

    outbox.notify_plan_changes(conn, run)
    assert store.claim_plan_notices(conn) == []

def test_a_plan_ending_with_its_whatsapp_window_is_warned_while_it_still_can_be(
    conn: Any
) -> None:
    # On WhatsApp the deadline is the 24 hours, not the plan: outside the window
    # there is no route at all without an approved template, so a notice that
    # waits for `plan_until` to come within the lead is a notice that waits
    # until nothing can be sent. The one-day trial is exactly this shape — it
    # starts on the message that connected the number, so its last minutes and
    # the window's are the same minutes.
    wa_plan_ending(conn, plan="trial", minutes_left=8, inbound_minutes=24 * 60 - 3)

    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["hour"]

def test_the_same_plan_is_not_warned_early_while_the_window_is_wide_open(
    conn: Any
) -> None:
    # The shutting window is the only reason the notice above went out ahead of
    # the lead. With twenty-three hours of window left there is nothing to race,
    # and eight minutes before the end is eight minutes before the end.
    wa_plan_ending(conn, plan="trial", minutes_left=8, inbound_minutes=60)

    assert store.claim_plan_notices(conn) == []

def test_a_subscriber_with_weeks_left_is_not_warned_when_their_window_shuts(
    conn: Any
) -> None:
    # The bound on the window rule, and the reason it is bounded by
    # `plan_until`: a window shutting on somebody who has alerts to carry on
    # with is the check-in's business — `closing_windows` — and "your plan is
    # ending" every evening for three weeks is not true on any of them.
    wa_plan_ending(conn, plan="paid", minutes_left=20 * 24 * 60,
                   inbound_minutes=24 * 60 - 3)

    assert store.claim_plan_notices(conn) == []

def test_a_notice_nobody_can_receive_is_not_logged_as_a_fault(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # It was a warning for every lapsed WhatsApp account, about something that
    # cannot be fixed from here — there is no template and nothing reopens the
    # window — and that the warning before the window shut has already said.
    # Counted so it stays visible, at info so the log is not asking anybody to
    # act on a decision.
    wa_plan_ending(conn, plan="trial", minutes_left=-1, inbound_minutes=30 * 60)
    fake = FakeNotifier(
        SendResult(
            ok=False,
            error="outside the 24h window and expired has no template",
            retryable=True,
        )
    )
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)

    outbox.notify_plan_changes(conn, run)

    events = conn.execute(
        "SELECT level FROM job_events WHERE run_id = %s AND stage = 'expire'",
        (run.id,),
    ).fetchall()
    assert [e["level"] for e in events] == ["info"]
    stage = conn.execute(
        "SELECT counters FROM job_stages WHERE run_id = %s AND stage = 'expire'",
        (run.id,),
    ).fetchone()
    assert stage["counters"]["window_shut"] == 1

def test_telegram_still_gets_its_claim_back(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The window rule is WhatsApp's alone. On Telegram a retryable failure is
    # always worth repeating, and there is no `last_inbound_at` to read.
    user_id = make_user(conn, plan="trial", plan_hours=None)
    conn.execute(
        "UPDATE users SET plan_until = now() + interval '3 minutes' WHERE id = %s",
        (user_id,),
    )
    make_subscription(conn, user_id)
    fake = FakeNotifier(SendResult(ok=False, error="503", retryable=True))
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)

    outbox.notify_plan_changes(conn, run)
    assert [row["stage"] for row in store.claim_plan_notices(conn)] == ["hour"]

def test_a_nought_share_withholds_everything(conn: Any, run: Run) -> None:
    # A finished plan on WhatsApp falls back to nothing, and `or 100` read that
    # nought as "no share was set, so send the lot" — so the one group that was
    # meant to receive nothing received all of it, each message billed and
    # carrying a notice saying their access was limited to 0% of listings.
    user_id = make_user(conn, address="", plan="paid", plan_days=None, plan_hours=None)
    conn.execute(
        "UPDATE users SET plan_until = now() - interval '1 day' WHERE id = %s", (user_id,)
    )
    make_subscription(conn, user_id)
    conn.execute(
        "INSERT INTO user_channels (user_id, channel, address, verified_at, "
        "last_inbound_at) VALUES (%s, 'whatsapp', '447700900111', now(), now())",
        (user_id,),
    )
    conn.execute("UPDATE channels SET enabled = true WHERE key = 'whatsapp'")

    assert [row["delivery_share"] for row in store.active_subscriptions(conn)] == [0]

    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])

    assert counts(conn) == {"skipped": 1}
    assert notification(conn)["error"] == "share"

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

def whatsapp_queued(
    conn: Any, run: Run, *, inbound_hours: float = 1, asked: bool = False
) -> int:
    """One queued WhatsApp alert for a number whose window is in some state.

    `inbound_hours` is how long ago they last wrote in — under 24 and the
    window is open. `asked` is whether this window's check-in has gone out.
    """

    user_id = make_user(conn)
    make_subscription(conn, user_id)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])
    conn.execute("UPDATE channels SET enabled = true WHERE key = 'whatsapp'")
    conn.execute("UPDATE notifications SET channel = 'whatsapp'")
    conn.execute(
        """
        INSERT INTO user_channels
               (user_id, channel, address, verified_at, last_inbound_at, window_asked_at)
        VALUES (%s, 'whatsapp', '447700900000', now(),
                now() - make_interval(secs => %s),
                CASE WHEN %s THEN now() ELSE NULL END)
        """,
        (user_id, int(inbound_hours * 3600), asked),
    )
    return user_id

def test_an_open_window_delivers_as_usual(conn: Any, run: Run) -> None:
    whatsapp_queued(conn, run, inbound_hours=1)

    assert len(store.claim_queued(conn, limit=10, max_attempts=3)) == 1

def test_a_shut_window_holds_without_spending_an_attempt(conn: Any, run: Run) -> None:
    # Nothing can be sent outside the 24 hours and there are no templates, so
    # claiming would only burn one of five attempts on a message that cannot go.
    whatsapp_queued(conn, run, inbound_hours=25)

    assert store.claim_queued(conn, limit=10, max_attempts=3) == []
    assert int(notification(conn)["attempts"]) == 0

def test_nothing_lands_on_top_of_the_check_in(conn: Any, run: Run) -> None:
    # The question goes out five minutes before the window shuts and is useless
    # buried: an alert after it pushes the buttons up the conversation, and a
    # question nobody taps costs two days of silence.
    whatsapp_queued(conn, run, inbound_hours=23.9, asked=True)

    assert store.claim_queued(conn, limit=10, max_attempts=3) == []
    assert int(notification(conn)["attempts"]) == 0

def test_the_tap_releases_what_the_question_was_holding(conn: Any, run: Run) -> None:
    # The webhook writes `last_inbound_at` on every inbound, a tap included,
    # which puts it past the question — all the release needs.
    user_id = whatsapp_queued(conn, run, inbound_hours=23.9, asked=True)
    conn.execute(
        "UPDATE user_channels SET last_inbound_at = now() "
        "WHERE user_id = %s AND channel = 'whatsapp'",
        (user_id,),
    )

    assert len(store.claim_queued(conn, limit=10, max_attempts=3)) == 1

def test_a_check_in_that_was_never_delivered_holds_nothing(conn: Any, run: Run) -> None:
    # `ask_before_the_window_shuts` marks before sending and undoes it if the
    # send fails. Left standing it would hold the queue for a question the
    # person never saw and can therefore never answer.
    user_id = whatsapp_queued(conn, run, inbound_hours=23.9, asked=True)
    store.unmark_window_asked(conn, user_id)

    assert len(store.claim_queued(conn, limit=10, max_attempts=3)) == 1

def test_a_window_reopened_before_the_old_question_was_answered(
    conn: Any, run: Run
) -> None:
    # Asked about a window that has since been replaced: the answer is stale by
    # itself, and holding on it would hold for ever.
    user_id = whatsapp_queued(conn, run, inbound_hours=1)
    conn.execute(
        "UPDATE user_channels SET window_asked_at = now() - interval '25 hours' "
        "WHERE user_id = %s AND channel = 'whatsapp'",
        (user_id,),
    )

    assert len(store.claim_queued(conn, limit=10, max_attempts=3)) == 1

def wa_window_closing(
    conn: Any, *, plan: str = "paid", plan_hours: int | None = 20 * 24,
    plan_minutes: int | None = None, inbound_minutes: int = 24 * 60 - 4,
) -> int:
    """A WhatsApp account whose 24-hour window is about to shut.

    `plan_minutes` overrides `plan_hours` for the cases that turn on how close
    the end of the plan is; a negative value is a plan that has already ended.
    """

    user_id = make_user(conn, address="", plan=plan, plan_days=None, plan_hours=plan_hours)
    if plan_minutes is not None:
        conn.execute(
            "UPDATE users SET plan_until = now() + make_interval(mins => %s) WHERE id = %s",
            (plan_minutes, user_id),
        )
    conn.execute("UPDATE users SET plan_from = now() - interval '10 days' WHERE id = %s",
                 (user_id,))
    make_subscription(conn, user_id)
    conn.execute(
        """
        INSERT INTO user_channels (user_id, channel, address, verified_at, last_inbound_at)
        VALUES (%s, 'whatsapp', %s, now(), now() - make_interval(mins => %s))
        """,
        (user_id, f"4477009{user_id:05d}", inbound_minutes),
    )
    return user_id

def asked(conn: Any) -> list[int]:
    return [int(row["user_id"]) for row in store.closing_windows(conn, minutes=5)]

def test_a_paying_subscriber_is_asked_before_the_window_shuts(conn: Any) -> None:
    # The case the question exists for: there are weeks of alerts left and the
    # only thing about to stop them is WhatsApp's window.
    user_id = wa_window_closing(conn)

    assert asked(conn) == [user_id]

def test_a_whatsapp_trial_is_not_asked_to_carry_on(conn: Any) -> None:
    # The trial runs one day from the first alert, so its end lands inside the
    # window it started. "Tap below and the alerts carry on" is then false —
    # and it arrived just after the notice saying the trial was ending, which
    # is the one message that is true.
    wa_window_closing(conn, plan="trial", plan_hours=None, plan_minutes=4)

    assert asked(conn) == []

def test_nobody_is_asked_within_an_hour_of_their_plan_ending(conn: Any) -> None:
    # Not a trial rule. Inside the hour the plan notice is the message, and
    # this one would contradict it on the same conversation.
    wa_window_closing(conn, plan_hours=None, plan_minutes=30)

    assert asked(conn) == []

def test_a_lapsed_whatsapp_plan_is_not_asked(conn: Any) -> None:
    # A finished plan falls back to nothing on WhatsApp — 0058 — so there is
    # nothing for a reopened window to carry.
    wa_window_closing(conn, plan_hours=None, plan_minutes=-60)

    assert asked(conn) == []

def test_a_spent_allowance_is_not_asked_either(conn: Any) -> None:
    # The other half of `live`: three weeks still on the clock and no messages
    # left to send, so the window is worth nothing.
    user_id = wa_window_closing(conn)
    conn.execute("UPDATE plans SET message_allowance = 2 WHERE key = 'paid'")
    for _ in range(2):
        store.record_wa_message(conn, user_id, "listing")

    try:
        assert asked(conn) == []
    finally:
        # `plans` is not in WIPE, so an allowance of two left behind would be
        # the allowance every later test runs under.
        conn.execute("UPDATE plans SET message_allowance = NULL WHERE key = 'paid'")

def test_the_check_in_asks_how_the_search_is_going(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:
    # The whole of the WhatsApp story rests on this message. With no templates
    # nothing can be sent outside the 24-hour window, so the only thing keeping
    # a subscriber reachable at all is a question asked before it shuts, whose
    # answer — either button — is an inbound message that opens the next one.
    user_id = wa_window_closing(conn)

    outbox.ask_before_the_window_shuts(conn, run)

    assert len(notifier.sent) == 1
    address, alert = notifier.sent[0]
    assert address == f"4477009{user_id:05d}"
    assert "How is the search going?" in (alert.text or "")
    # Two taps, and both of them replies rather than links: a url opens a page
    # and leaves the window shutting, a reply is what reopens it.
    assert [a.callback for a in alert.actions] == ["continue", "found"]
    # Inside the window, so it goes as free text — which is the only way it
    # can go, and the reason it is asked while there is still window left.
    assert notifier.to[0].last_inbound is not None

    # Asked once per window, and holding the queue while it waits: nothing
    # should land on top of the question and push the buttons up the chat.
    asked_at = conn.execute(
        "SELECT window_asked_at FROM user_channels WHERE user_id = %s", (user_id,)
    ).fetchone()
    assert asked_at is not None and asked_at["window_asked_at"] is not None

    outbox.ask_before_the_window_shuts(conn, run)
    assert len(notifier.sent) == 1

def test_a_check_in_that_could_not_be_sent_is_asked_again(
    conn: Any, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Marked before the send and undone after a failed one. Left standing it
    # would hold the alerts for a question nobody saw, and with no templates
    # the person could not be reached again until they wrote in themselves.
    user_id = wa_window_closing(conn)
    fake = FakeNotifier(SendResult(ok=False, error="503", retryable=True))
    monkeypatch.setattr(outbox, "build_notifier", lambda channel: fake)

    outbox.ask_before_the_window_shuts(conn, run)

    assert asked(conn) == [user_id]

def held_reasons(conn: Any) -> list[str | None]:
    return [
        r["held"]
        for r in conn.execute(
            "SELECT held FROM queued_notifications ORDER BY id"
        ).fetchall()
    ]

def test_a_shut_window_says_so_by_name(conn: Any, run: Run) -> None:
    # The admin dashboard prints these strings and the hourly report decides
    # what is a fault by them, so the wording is part of the contract.
    whatsapp_queued(conn, run, inbound_hours=25)

    assert held_reasons(conn) == ["window shut"]

def test_an_unanswered_check_in_is_its_own_reason(conn: Any, run: Run) -> None:
    # Separable on purpose: a shut window means nobody could be asked, and an
    # unanswered one means they were and have not replied yet. The first is
    # worth watching, the second resolves itself.
    whatsapp_queued(conn, run, inbound_hours=23.9, asked=True)

    assert held_reasons(conn) == ["check-in unanswered"]

def test_the_daily_cap_counts_every_message_not_only_the_alerts(
    conn: Any, run: Run
) -> None:
    # Thirty a day bounds what a non-payer costs. Every message is billed, so
    # counting only the listing alerts let somebody chatting to the bot cost a
    # second thirty on top of the first — see 0063. The subscriber here is on
    # a free plan, so the cap applies.
    user_id = whatsapp_queued(conn, run, inbound_hours=1)
    conn.execute("UPDATE users SET plan = 'free' WHERE id = %s", (user_id,))
    for _ in range(30):
        store.record_wa_message(conn, user_id, "reply")

    assert held_reasons(conn) == ["daily cap"]


def test_a_paying_subscriber_is_exempt_from_the_daily_cap(
    conn: Any, run: Run
) -> None:
    # The other side of the same rule, so that the test above is about what is
    # counted rather than about who it applies to.
    user_id = whatsapp_queued(conn, run, inbound_hours=1)
    for _ in range(30):
        store.record_wa_message(conn, user_id, "reply")

    assert held_reasons(conn) == [None]


def test_yesterdays_messages_do_not_count_against_today(
    conn: Any, run: Run
) -> None:
    # London days, like every other date in here. Counted from the ledger now,
    # so the boundary is worth asserting again.
    user_id = whatsapp_queued(conn, run, inbound_hours=1)
    conn.execute("UPDATE users SET plan = 'free' WHERE id = %s", (user_id,))
    for _ in range(30):
        store.record_wa_message(conn, user_id, "reply")
    conn.execute(
        "UPDATE wa_messages SET sent_at = sent_at - interval '1 day' "
        "WHERE user_id = %s",
        (user_id,),
    )

    assert held_reasons(conn) == [None]


def wa_alert_queued_while_live(
    conn: Any, run: Run, *, plan: str = "trial", plan_hours: int = 1
) -> int:
    """One WhatsApp listing alert, queued while the plan was still running.

    WhatsApp only, so the account has one primary channel and the entitlement
    is the one its own messenger is priced at.

    The window is open and stays open for every test below, because that is
    the case that was wrong: new matches stop being queued the moment a plan
    ends — `queue_matches` reads the same share — and the backlog behind a
    shut window went out in full as soon as anything reopened it.
    """

    user_id = make_user(conn, address="", plan=plan, plan_hours=plan_hours)
    conn.execute("UPDATE channels SET enabled = true WHERE key = 'whatsapp'")
    conn.execute(
        "INSERT INTO user_channels (user_id, channel, address, verified_at, "
        "last_inbound_at) VALUES (%s, 'whatsapp', '447700900123', now(), now())",
        (user_id,),
    )
    make_subscription(conn, user_id)
    outbox.queue_matches(
        conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")]
    )
    # Nothing is holding it yet: the plan is live and the window is open.
    assert held_reasons(conn) == [None]
    return user_id


def ended(conn: Any, user_id: int) -> None:
    conn.execute(
        "UPDATE users SET plan_until = now() - interval '1 minute' WHERE id = %s",
        (user_id,),
    )


def test_a_finished_whatsapp_trial_holds_the_backlog_an_open_window_would_release(
    conn: Any, run: Run
) -> None:
    # The hole 0065 closes. Alerts queue up behind a shut window during the
    # trial, the trial ends, the person taps "Keep searching" — and a day of
    # listings went out to somebody with no plan, billed to us, each one
    # footed with "you are currently receiving only 0% of available
    # properties".
    user_id = wa_alert_queued_while_live(conn, run)
    ended(conn, user_id)

    assert held_reasons(conn) == ["plan ended"]
    assert store.claim_queued(conn, limit=10, max_attempts=3) == []
    # Held, not claimed: claiming spends one of five attempts on a message
    # that is not being sent, and renewing must not find it out of them.
    assert int(notification(conn)["attempts"]) == 0


def test_a_finished_whatsapp_subscription_is_no_different_from_a_trial(
    conn: Any, run: Run
) -> None:
    # The rule is about the plan being over, not about which plan it was. A
    # month that ran out is a month somebody has stopped paying for.
    user_id = wa_alert_queued_while_live(conn, run, plan="wa_month", plan_hours=1)
    ended(conn, user_id)

    assert held_reasons(conn) == ["plan ended"]


def test_a_spent_whatsapp_allowance_holds_the_backlog_with_days_still_left(
    conn: Any, run: Run
) -> None:
    # The other half of `live`, and the half a date cannot explain: the period
    # ended on its messages with three weeks still on the calendar. Written
    # straight into the ledger, which is what the view counts — see 0063.
    #
    # The same nine hundred messages trip the daily cap as well, so this also
    # pins the order: the plan is the reason, and the cap — which is only ever
    # "not until tomorrow" — is not.
    user_id = wa_alert_queued_while_live(conn, run, plan="wa_month", plan_hours=20 * 24)
    allowance = conn.execute(
        "SELECT message_allowance FROM plans WHERE key = 'wa_month'"
    ).fetchone()["message_allowance"]
    conn.execute(
        "INSERT INTO wa_messages (user_id, kind) "
        "SELECT %s, 'listing' FROM generate_series(1, %s)",
        (user_id, allowance),
    )

    assert held_reasons(conn) == ["plan ended"]


def test_paying_again_releases_what_the_ended_plan_was_holding(
    conn: Any, run: Run
) -> None:
    # Held rather than skipped, for this: somebody who renews inside the
    # two-day backlog window gets the listings they paid to see. What is not
    # renewed is cleared by `drop_stale_whatsapp` on the same two days.
    user_id = wa_alert_queued_while_live(conn, run)
    ended(conn, user_id)
    assert held_reasons(conn) == ["plan ended"]

    conn.execute(
        "UPDATE users SET plan = 'wa_month', plan_from = now(), "
        "plan_until = now() + interval '30 days' WHERE id = %s",
        (user_id,),
    )

    assert held_reasons(conn) == [None]
    assert len(store.claim_queued(conn, limit=10, max_attempts=3)) == 1


def test_a_lapsed_telegram_plan_keeps_receiving_its_share(
    conn: Any, run: Run
) -> None:
    # Untouched, deliberately. The lapsed tier's share of the matches costs
    # nothing to deliver on Telegram and is the best argument for paying; what
    # says so in the message is the share, which puts the limited-access line
    # at its foot.
    user_id = make_user(conn, plan="trial", plan_hours=1)
    make_subscription(conn, user_id)
    outbox.queue_matches(
        conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")]
    )
    ended(conn, user_id)

    assert held_reasons(conn) == [None]
    claimed = store.claim_queued(conn, limit=10, max_attempts=3)
    assert len(claimed) == 1
    assert outbox.withheld_share(claimed[0]) == 10


def test_nothing_holds_a_telegram_message(conn: Any, run: Run) -> None:
    # Which is what keeps `Oldest queued` honest: it ages the rows with no
    # reason, and for Telegram there is never one.
    user_id = make_user(conn)
    make_subscription(conn, user_id)
    outbox.queue_matches(conn, run, source_key="openrent", listing_ids=[make_listing(conn, "1")])

    assert held_reasons(conn) == [None]

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

def test_a_day_out_is_not_warned_at_all(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:

    # There was a warning here, with its own wording — "ends tomorrow". Two
    # messages about one ending is one more than anybody asked for, so the day
    # out is silent and the hour out is the whole of the warning.
    make_subscription(conn, make_user(conn, plan="trial", plan_hours=23))

    outbox.notify_plan_changes(conn, run)
    assert notifier.sent == []

def test_an_hour_out_is_warned_once(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:

    user_id = make_user(conn, plan="trial", plan_hours=23)
    make_subscription(conn, user_id)
    conn.execute(
        "UPDATE users SET plan_until = now() + interval '3 minutes' WHERE id = %s", (user_id,)
    )

    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1
    _, alert = notifier.sent[0]
    assert alert.kind == "expiring"
    assert "in a few minutes" in (alert.text or "")
    # The payment link is the button, not a line of url in the body.
    assert len(alert.actions) == 1
    assert alert.actions[0].url
    assert "http" not in (alert.text or "")

    # Said once per period: the unique key is (user, stage, plan_until), so a
    # drain every minute does not say it sixty times an hour.
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1

def test_paying_re_arms_the_warning_with_nothing_reset(
    conn: Any, run: Run, notifier: FakeNotifier
) -> None:

    user_id = make_user(conn, plan="trial", plan_hours=1)
    make_subscription(conn, user_id)
    conn.execute(
        "UPDATE users SET plan_until = now() + interval '3 minutes' WHERE id = %s", (user_id,)
    )
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1

    conn.execute(
        "UPDATE users SET plan = 'paid', plan_until = now() + interval '14 days' WHERE id = %s",
        (user_id,),
    )
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 1, "nothing is due while the paid period is far off"

    # `plan_until` is in the unique key, so moving it is what re-arms the
    # warning — there is no flag to reset.
    conn.execute(
        "UPDATE users SET plan_until = now() + interval '3 minutes' WHERE id = %s", (user_id,)
    )
    outbox.notify_plan_changes(conn, run)
    assert len(notifier.sent) == 2
    assert "subscription ends in a few minutes" in (notifier.sent[1][1].text or "")

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


# ── the starter batch is the other way a listing reaches somebody ────────
#
# `queue_matches` asks `sources.announces` of the reader handing its catch
# over, which is the right place for a live alert and reaches nothing else. A
# new subscriber's first five flats are chosen from `listings` directly, by
# which time the reader is long gone — so the same rule is asked of the row,
# through `listing_sightings`. Pinned on `recent_listings` rather than through
# `seed_new_subscriptions`, because the pool is where the rule now lives and
# the seeding around it announces itself to ops over the network.


def seen_by(conn: Any, listing_id: int, *readers: str) -> None:
    for reader in readers:
        store.record_sightings(conn, [listing_id], reader)


def pool(conn: Any) -> list[str]:
    return [
        str(row["url"]).rsplit("/", 1)[-1]
        for row in store.recent_listings(conn, days=3, limit=400)
    ]


def test_the_starter_pool_offers_what_a_scraper_saw(conn: Any) -> None:
    seen_by(conn, make_listing(conn, "scraped"), "openrent")

    assert pool(conn) == ["scraped"]


def test_the_starter_pool_leaves_out_what_only_a_muted_source_saw(conn: Any) -> None:
    """The leak this closes. See 0060.

    Muting the feed muted the alerts and not the welcome: a flat only the feed
    ever found was still one of the five a new subscriber was greeted with,
    which also spoiled the measurement the muting exists for.
    """

    conn.execute("UPDATE sources SET announces = false WHERE key = 'tg_feed'")
    seen_by(conn, make_listing(conn, "feed_only"), "tg_feed")

    assert pool(conn) == []


def test_a_flat_both_of_them_saw_is_offered_exactly_once(conn: Any) -> None:
    # Two sightings of one listing, and the filter must not turn that into two
    # rows in the pool — which is what a join in place of EXISTS would do, and
    # would spend two of the five offers on one flat.
    conn.execute("UPDATE sources SET announces = false WHERE key = 'tg_feed'")
    seen_by(conn, make_listing(conn, "both"), "tg_feed", "openrent")

    assert pool(conn) == ["both"]


def test_the_feed_s_own_finds_are_offered_while_it_still_announces(conn: Any) -> None:
    # The rule is `announces`, not the name of the feed. Un-muting has to put
    # the welcome back along with the alerts, in one UPDATE and no deploy.
    seen_by(conn, make_listing(conn, "from_the_feed"), "tg_feed")

    assert pool(conn) == ["from_the_feed"]


def test_a_listing_no_reader_recorded_is_not_offered(conn: Any) -> None:
    # The honest cost of reading the evidence rather than guessing: nothing
    # before 0052 was backfilled, so a row with no sighting is not offered.
    # Safe only because the pool is three days wide and that table is months
    # old — which is why `recent_listings` says so.
    make_listing(conn, "unwitnessed")

    assert pool(conn) == []
