from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from worker.contracts.notify import NOTIFIERS, SendResult, build_notifier
from worker.notify.telegram import render_listing
from worker.notify.plans import withheld_notice
from worker.pipeline.outbox import (
    MAX_ATTEMPTS,
    alert_for,
    digest_due,
    interleave_by_user,
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

def test_a_restricted_listing_offers_payment_instead_of_dismissal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "londonhomefinderbot")
    alert = alert_for(row(delivery_share=20, plan_key="trial"))
    assert alert is not None
    assert [a.label for a in alert.actions] == ["Get full access"]
    assert alert.actions[0].url == "https://t.me/londonhomefinderbot?start=pay"
    assert alert.actions[0].callback is None

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
    assert withheld_notice(12, 20) == (
        "🔒 12 new listings today — you're missing 80%! "
        "Upgrade now to unlock instant notifications."
    )

def test_the_digest_agrees_with_itself_about_one_listing() -> None:
    assert "1 new listing today" in withheld_notice(1, 20)
    assert "1 new listings" not in withheld_notice(1, 20)

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

    assert build_notifier("whatsapp") is None

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
