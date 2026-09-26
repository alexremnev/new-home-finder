from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from worker.contracts.notify import NOTIFIERS, SendResult, build_notifier
from worker.notify.telegram import render_listing
from worker.notify.plans import digest_notice, notice_for
from worker.pipeline.outbox import (
    MAX_ATTEMPTS,
    alert_for,
    digest_due,
    interleave_by_user,
    checkout_for,
    listing_actions,
    listing_view,
    outcome_for,
)

def row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": 1,
        "kind": "new_listing",
        "price_pcm": 1950,
        "bedrooms": 2,
        "property_type": "flat",
        "postcode_district": "SE16",
        "tfl_zone": 2,
        "available_from": date(2026, 9, 12),
        "furnished": "furnished",
        "pets_allowed": True,
        "bills_included": False,
        "min_tenancy_months": 12,
        "is_landlord_direct": True,
        "source_display": "OpenRent",
        "url": "https://www.openrent.co.uk/property-to-rent/london/x/123",
    }
    values.update(overrides)
    return values

def test_a_view_carries_the_facts_and_the_link() -> None:
    view = listing_view(row())
    assert view.price_pcm == 1950
    assert view.district == "SE16"
    assert view.zone == 2
    assert view.url.endswith("/123")

def test_a_view_cannot_carry_the_description_or_photographs() -> None:

    view = listing_view(row())
    assert not hasattr(view, "description")
    assert not hasattr(view, "photo_count")
    assert render_listing(view).count("http") == 1

def test_a_missing_furnishing_becomes_unknown_rather_than_null() -> None:

    assert listing_view(row(furnished=None)).furnished == "unknown"

def test_a_new_listing_row_becomes_a_listing_alert() -> None:
    alert = alert_for(row())
    assert alert is not None
    assert alert.kind == "listing"
    assert alert.listing is not None

def test_a_kind_with_nothing_to_render_is_refused() -> None:

    assert alert_for(row(kind="welcome")) is None
    assert alert_for(row(kind="nonsense")) is None

def test_full_access_gets_an_ignore_button_keyed_to_the_notification() -> None:
    alert = alert_for(row(id=77))
    assert alert is not None
    assert [(a.label, a.callback, a.url) for a in alert.actions] == [
        ("Ignore", "ignore:77", None)
    ]

def test_a_restricted_listing_offers_payment_instead_of_dismissal() -> None:
    alert = alert_for(
        row(delivery_share=20, plan_key="trial"), "https://example.test/upgrade?t=abc"
    )
    assert alert is not None
    assert [a.label for a in alert.actions] == ["Get full access"]
    assert alert.actions[0].url == "https://example.test/upgrade?t=abc"
    assert alert.actions[0].callback is None

class FakeConn:

    # Enough of a connection for upgrade_token: no live token, so it issues one.
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str, params: Any = None) -> Any:
        self.statements.append(" ".join(sql.split()))
        self.params = params
        return self

    def fetchone(self) -> None:
        return None

def test_telegram_gets_the_deep_link_and_whatsapp_gets_the_checkout_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    # A t.me link sent to WhatsApp walks the person into a Telegram bot rather
    # than to the payment, which is where the money stopped.
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "londonhomefinderbot")
    monkeypatch.setenv("SITE_URL", "https://londonhomefinder.co.uk")

    assert checkout_for(FakeConn(), 5, "telegram") == (
        "https://t.me/londonhomefinderbot?start=pay"
    )

    link = checkout_for(FakeConn(), 5, "whatsapp")
    assert link is not None
    assert link.startswith("https://londonhomefinder.co.uk/upgrade?t=")
    assert "t.me" not in link

def test_a_pushed_checkout_token_outlives_the_evening() -> None:

    # The digest goes out at 20:00 and is read whenever it is read. An hour,
    # which is right for a link somebody just asked for, would leave a dead
    # button in the message.
    from worker.store import PUSHED_TOKEN_MINUTES

    assert PUSHED_TOKEN_MINUTES >= 24 * 60

def test_a_reusable_token_is_preferred_to_a_fresh_one() -> None:
    from worker import store

    class Existing(FakeConn):
        def fetchone(self) -> dict[str, str]:
            return {"token": "still-good"}

    conn = Existing()
    assert store.upgrade_token(conn, 5) == "still-good"
    # Nothing was replaced: the link in the message sent a moment ago still works.
    assert not any("INSERT" in sql for sql in conn.statements)

def test_without_a_bot_username_there_is_no_broken_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
    assert listing_actions(listing_view(row(delivery_share=20)), 1) == []

def test_a_lapsed_trial_and_a_lapsed_plan_are_told_apart() -> None:
    assert listing_view(row(delivery_share=20, plan_key="trial")).lapsed == "trial"
    assert listing_view(row(delivery_share=20, plan_key="month")).lapsed == "plan"

def test_nothing_is_said_to_lapse_when_nothing_is_withheld() -> None:

    assert listing_view(row(delivery_share=100, plan_key="trial")).lapsed is None
    assert listing_view(row(plan_key="trial")).lapsed is None

@pytest.mark.parametrize(
    ("hour", "due"),
    [(0, False), (9, False), (19, False), (20, True), (21, True), (23, True)],
)
def test_the_digest_waits_for_the_end_of_the_london_day(hour: int, due: bool) -> None:
    london = ZoneInfo("Europe/London")
    assert digest_due(datetime(2026, 9, 15, hour, 30, tzinfo=london)) is due

def test_the_digest_hour_is_london_not_utc() -> None:

    # 19:30 UTC in September is 20:30 in London: due, though UTC says otherwise.
    assert digest_due(datetime(2026, 9, 15, 19, 30, tzinfo=timezone.utc)) is True
    assert digest_due(datetime(2026, 9, 15, 18, 30, tzinfo=timezone.utc)) is False

def test_the_digest_counts_what_matched_and_names_what_is_missing() -> None:
    assert digest_notice(12, 20) == (
        "🔒 12 new listings today — you're missing 80%! "
        "Upgrade now to unlock instant notifications."
    )

def test_the_digest_agrees_with_itself_about_one_listing() -> None:
    assert "1 new listing today" in digest_notice(1, 20)
    assert "1 new listings" not in digest_notice(1, 20)

def test_the_digest_names_the_average_rent_when_there_is_one() -> None:
    text = digest_notice(12, 20, avg_price=1840)
    # "rooms aside", because the figure leaves them out: a £900 room averaged
    # with a £2,100 flat describes neither.
    assert "💷 Average rent, rooms aside: £1,840/month" in text
    assert "missing 80%" in text

def test_a_filter_for_rooms_alone_is_told_the_room_average() -> None:
    # Somebody searching only for rooms wants the rooms figure, and saying which
    # one it is matters: the two numbers are not comparable.
    text = digest_notice(12, 100, avg_price=910, rooms_only=True)
    assert "💷 Average room rent: £910/month" in text
    assert "rooms aside" not in text

def test_no_average_means_no_line_at_all() -> None:
    # A rooms-only filter that matched no room, or a filter whose only matches
    # were rooms: better silent than a figure invented to fill the line.
    assert "💷" not in digest_notice(12, 100, avg_price=None)
    assert "💷" not in digest_notice(12, 100, avg_price=None, rooms_only=True)

def test_a_paying_subscriber_is_not_told_what_they_are_missing() -> None:

    # They are missing nothing, and an upgrade line to somebody who pays reads
    # as a bill.
    text = digest_notice(12, 100, avg_price=1840, paid=True)
    assert text.startswith("🔔 12 new listings matched your filter today.")
    assert "missing" not in text
    assert "Upgrade" not in text

def test_full_access_on_a_trial_is_told_the_same_thing() -> None:
    assert "missing" not in digest_notice(5, 100)

def test_a_quiet_day_says_so_rather_than_saying_nothing() -> None:

    # Silence is indistinguishable from a broken bot, and the usual cause is a
    # filter nobody can match.
    text = digest_notice(0, 20)
    assert "Nothing matched your filter today." in text
    assert "/update" in text
    assert "missing" not in text

def test_a_quiet_day_names_no_average_it_cannot_have() -> None:
    assert "Average" not in digest_notice(0, 20, avg_price=None)
    assert "£" not in digest_notice(0, 20)

def test_an_ended_plan_says_what_arrives_instead_of_claiming_silence() -> None:
    text = notice_for("month", datetime(2026, 9, 15, 12, tzinfo=timezone.utc), "expired", 20)
    assert "You now receive 20% of what matches your filter." in text
    assert "have stopped" not in text

def test_an_ending_plan_names_the_share_it_falls_back_to() -> None:
    for stage in ("day", "hour"):
        text = notice_for("trial", datetime(2026, 9, 15, 12, tzinfo=timezone.utc), stage, 20)
        assert "you receive 20% of what matches, until you renew." in text
        assert "the alerts stop" not in text

def test_without_a_lapsed_tier_the_old_wording_stands() -> None:

    # A share of 100, or none configured, means there is no fallback: then the
    # alerts really do stop, and saying otherwise would be the lie.
    for share in (None, 100):
        assert "alerts have stopped" in notice_for("month", None, "expired", share)
        assert "the alerts stop until you renew" in notice_for("month", None, "day", share)

def test_no_notice_promises_a_period_no_plan_sells() -> None:

    moment = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    for stage in ("day", "hour", "expired"):
        for plan in ("trial", "week", "month"):
            assert "2 weeks" not in notice_for(plan, moment, stage, 20)
            assert "2 more weeks" not in notice_for(plan, moment, stage, 20)

def test_an_ended_plan_carries_a_link_that_actually_opens_checkout() -> None:

    # The notice used to print a bare /upgrade with no token, and that page can
    # only answer "that link has expired".
    text = notice_for("month", None, "expired", 20, "https://x.test/upgrade?t=abc")
    assert "https://x.test/upgrade?t=abc" in text
    assert "/upgrade\n" not in text

def test_without_a_link_the_notice_falls_back_to_the_command() -> None:
    assert "Send /pay for a payment link." in notice_for("month", None, "expired", 20)

def test_an_ended_plan_says_what_to_do_and_where_to_read_about_it() -> None:
    from worker.notify.plans import SITE

    # The moment a plan runs out is the moment somebody decides whether to pay.
    # A statement that it ended, with no instruction, left people asking how.
    text = notice_for("month", None, "expired", 20, "https://x.test/upgrade?t=abc")
    assert "To carry on:" in text
    assert "1. Open https://x.test/upgrade?t=abc and pay by card." in text
    assert "2. The alerts start again straight away" in text
    # Somewhere to read about it rather than deciding inside a chat window.
    assert f"More about the service: {SITE}" in text
    # And the two ways out, so neither is a thing you have to remember.
    assert "/update" in text
    assert "/stop" in text

def test_a_price_drop_reuses_the_listing_shape() -> None:

    alert = alert_for(row(kind="price_drop"))
    assert alert is not None and alert.kind == "listing"

def test_a_success_records_the_provider_id() -> None:
    decision = outcome_for(SendResult(ok=True, provider_msg_id="42"), attempts=1)
    assert decision.status == "sent"
    assert decision.provider_msg_id == "42"
    assert not decision.stop_user

def test_a_transient_failure_stays_in_the_queue() -> None:
    result = SendResult(ok=False, error="429: Too Many Requests", retryable=True)
    decision = outcome_for(result, attempts=1)
    assert decision.status == "queued"
    assert decision.error is not None and "429" in decision.error

def test_a_permanent_failure_is_not_retried() -> None:
    result = SendResult(ok=False, error="400: Bad Request", retryable=False)
    assert outcome_for(result, attempts=1).status == "failed"

def test_retrying_stops_at_the_attempt_ceiling() -> None:

    result = SendResult(ok=False, error="500: Internal", retryable=True)
    assert outcome_for(result, attempts=MAX_ATTEMPTS - 1).status == "queued"
    decision = outcome_for(result, attempts=MAX_ATTEMPTS)
    assert decision.status == "failed"
    assert decision.error is not None and "gave up" in decision.error

def test_an_unreachable_recipient_stops_the_subscription() -> None:

    result = SendResult(ok=False, error="403: Forbidden: bot was blocked by the user",
                        retryable=False, recipient_gone=True)
    decision = outcome_for(result, attempts=1)
    assert decision.status == "failed"
    assert decision.stop_user

def test_a_gone_recipient_wins_over_a_retryable_code() -> None:

    result = SendResult(ok=False, error="403: Forbidden", retryable=True, recipient_gone=True)
    decision = outcome_for(result, attempts=1)
    assert decision.status == "failed" and decision.stop_user

def test_a_channel_is_built_from_its_own_configuration() -> None:

    assert "telegram" in NOTIFIERS
    notifier = build_notifier("telegram")
    assert notifier is not None and notifier.key == "telegram"

def test_an_unimplemented_channel_returns_nothing_rather_than_raising() -> None:

    assert build_notifier("nonsense") is None

def test_the_token_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    notifier = build_notifier("telegram")
    assert notifier is not None
    assert notifier.token == "test-token"

def test_an_absent_token_is_none_and_not_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setenv("TELEGRAM_TOKEN", "")
    notifier = build_notifier("telegram")
    assert notifier is not None
    assert notifier.token is None

def queued(user_id: int, notification_id: int) -> dict[str, Any]:
    return {"user_id": user_id, "id": notification_id}

def test_one_persons_messages_are_spread_out_rather_than_sent_back_to_back() -> None:

    batch = [queued(1, 1), queued(1, 2), queued(2, 3), queued(2, 4)]
    order = [(r["user_id"], r["id"]) for r in interleave_by_user(batch)]
    assert order == [(1, 1), (2, 3), (1, 2), (2, 4)]

def test_a_persons_own_messages_keep_their_order() -> None:

    batch = [queued(1, 10), queued(2, 11), queued(1, 12), queued(1, 13)]
    mine = [r["id"] for r in interleave_by_user(batch) if r["user_id"] == 1]
    assert mine == [10, 12, 13]

def test_nothing_is_reordered_when_there_is_only_one_recipient() -> None:
    batch = [queued(1, 1), queued(1, 2), queued(1, 3)]
    assert interleave_by_user(batch) == batch

def test_every_message_survives_the_reordering() -> None:

    batch = [queued(user, user * 100 + n) for user in range(1, 6) for n in range(user)]
    result = interleave_by_user(batch)
    assert sorted(r["id"] for r in result) == sorted(r["id"] for r in batch)
    assert len(result) == len(batch)

def test_an_empty_batch_is_handled() -> None:
    assert interleave_by_user([]) == []

def test_the_starter_batch_skips_whatsapp_but_still_settles_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    # Five listings at sign-up is five billed messages on WhatsApp, sent before
    # the person has decided whether they want the service. Telegram is free and
    # keeps them.
    from worker import store
    from worker.pipeline import outbox

    owed = [
        {"id": 1, "user_id": 10, "criteria": {}, "channel": "whatsapp"},
        {"id": 2, "user_id": 20, "criteria": {}, "channel": "telegram"},
    ]
    settled: list[int] = []
    queued: list[dict[str, Any]] = []

    monkeypatch.setattr(store, "unseeded_subscriptions", lambda conn: owed)
    monkeypatch.setattr(
        store, "recent_listings",
        lambda conn, days, limit: [row(id=n, delivery_share=None) for n in (1, 2, 3)],
    )
    monkeypatch.setattr(
        store, "mark_seeded", lambda conn, sid: settled.append(sid)
    )
    monkeypatch.setattr(
        store, "queue_notifications",
        lambda conn, rows: (queued.extend(rows), set())[1],
    )

    outbox.seed_new_subscriptions(None, FakeRun())

    # Both are settled, so neither is reconsidered on every later run.
    assert sorted(settled) == [1, 2]
    # Only the free channel was actually sent anything.
    assert {one["channel"] for one in queued} == {"telegram"}

class FakeRun:

    class Stage:
        def set(self, *_: object, **__: object) -> None: ...
        def count(self, *_: object, **__: object) -> None: ...
        def log(self, *_: object, **__: object) -> None: ...
        def degrade(self, *_: object) -> None: ...

    def stage(self, *_: object, **__: object) -> object:
        held = self.Stage()

        class Open:
            def __enter__(self) -> object:
                return held

            def __exit__(self, *_: object) -> bool:
                return False

        return Open()
