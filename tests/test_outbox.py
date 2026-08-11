"""The decisions in delivery that do not need a database or a bot token."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from worker.contracts.notify import NOTIFIERS, SendResult, build_notifier
from worker.notify.telegram import render_listing
from worker.pipeline.outbox import (
    MAX_ATTEMPTS,
    alert_for,
    interleave_by_user,
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


# ── what a message may contain ────────────────────────────────────────────


def test_a_view_carries_the_facts_and_the_link() -> None:
    view = listing_view(row())
    assert view.price_pcm == 1950
    assert view.district == "SE16"
    assert view.zone == 2
    assert view.url.endswith("/123")


def test_a_view_cannot_carry_the_description_or_photographs() -> None:
    """The narrowing is the point: `ListingView` has no field to put them in, so
    reproducing a site's content is impossible rather than merely discouraged."""
    view = listing_view(row())
    assert not hasattr(view, "description")
    assert not hasattr(view, "photo_count")
    assert render_listing(view).count("http") == 1


def test_a_missing_furnishing_becomes_unknown_rather_than_null() -> None:
    """The column is NOT NULL in the schema, but a NULL arriving from a join must
    not crash delivery."""
    assert listing_view(row(furnished=None)).furnished == "unknown"


def test_a_new_listing_row_becomes_a_listing_alert() -> None:
    alert = alert_for(row())
    assert alert is not None
    assert alert.kind == "listing"
    assert alert.listing is not None


def test_a_kind_with_nothing_to_render_is_refused() -> None:
    """Welcome and stop confirmations are sent in the moment by the web app. One
    queued here is a bad row, and sending an empty message is worse than failing."""
    assert alert_for(row(kind="welcome")) is None
    assert alert_for(row(kind="nonsense")) is None


def test_a_price_drop_reuses_the_listing_shape() -> None:
    """Adding an event kind must not require touching a channel."""
    alert = alert_for(row(kind="price_drop"))
    assert alert is not None and alert.kind == "listing"


# ── what happens after one attempt ────────────────────────────────────────


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
    """Otherwise one undeliverable message is tried on every run, for ever."""
    result = SendResult(ok=False, error="500: Internal", retryable=True)
    assert outcome_for(result, attempts=MAX_ATTEMPTS - 1).status == "queued"
    decision = outcome_for(result, attempts=MAX_ATTEMPTS)
    assert decision.status == "failed"
    assert decision.error is not None and "gave up" in decision.error


def test_an_unreachable_recipient_stops_the_subscription() -> None:
    """A blocked bot is not a delivery problem to retry; it is a person who left.

    Continuing to message them counts against the bot's standing with Telegram.
    """
    result = SendResult(ok=False, error="403: Forbidden: bot was blocked by the user",
                        retryable=False, recipient_gone=True)
    decision = outcome_for(result, attempts=1)
    assert decision.status == "failed"
    assert decision.stop_user


def test_a_gone_recipient_wins_over_a_retryable_code() -> None:
    """Order matters: were retryability checked first, a gone recipient with a
    retryable-looking code would be tried five more times."""
    result = SendResult(ok=False, error="403: Forbidden", retryable=True, recipient_gone=True)
    decision = outcome_for(result, attempts=1)
    assert decision.status == "failed" and decision.stop_user


# ── the channel registry ──────────────────────────────────────────────────


def test_a_channel_is_built_from_its_own_configuration() -> None:
    """The delivery stage names a channel and gets something that can send.

    It never learns what that channel needs to authenticate, which is what makes
    adding one a new module rather than an edit to the pipeline.
    """
    assert "telegram" in NOTIFIERS
    notifier = build_notifier("telegram")
    assert notifier is not None and notifier.key == "telegram"


def test_an_unimplemented_channel_returns_nothing_rather_than_raising() -> None:
    """`channels` holds rows for WhatsApp and email before either is built. A
    queued message for one must fail on its own, not stop the run."""
    assert build_notifier("whatsapp") is None


def test_the_token_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    notifier = build_notifier("telegram")
    assert notifier is not None
    assert notifier.token == "test-token"  # type: ignore[attr-defined]


def test_an_absent_token_is_none_and_not_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty string would pass a truthiness check and produce a request to
    `/bot/sendMessage`, which fails confusingly instead of clearly."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "")
    notifier = build_notifier("telegram")
    assert notifier is not None
    assert notifier.token is None  # type: ignore[attr-defined]


# ── the order a batch is sent in ──────────────────────────────────────────


def queued(user_id: int, notification_id: int) -> dict[str, Any]:
    return {"user_id": user_id, "id": notification_id}


def test_one_persons_messages_are_spread_out_rather_than_sent_back_to_back() -> None:
    """Telegram's per-chat limit is about one message a second while its overall
    limit is about thirty, so four messages to one person in a row is the shape
    that earns a 429 — and the retry then delays everyone queued behind them."""
    batch = [queued(1, 1), queued(1, 2), queued(2, 3), queued(2, 4)]
    order = [(r["user_id"], r["id"]) for r in interleave_by_user(batch)]
    assert order == [(1, 1), (2, 3), (1, 2), (2, 4)]


def test_a_persons_own_messages_keep_their_order() -> None:
    """Interleaving is about spacing, not shuffling: listings must still arrive
    oldest first for the person reading them."""
    batch = [queued(1, 10), queued(2, 11), queued(1, 12), queued(1, 13)]
    mine = [r["id"] for r in interleave_by_user(batch) if r["user_id"] == 1]
    assert mine == [10, 12, 13]


def test_nothing_is_reordered_when_there_is_only_one_recipient() -> None:
    batch = [queued(1, 1), queued(1, 2), queued(1, 3)]
    assert interleave_by_user(batch) == batch


def test_every_message_survives_the_reordering() -> None:
    """The failure that would matter most here is a dropped row: it would look
    like a listing that simply never arrived."""
    batch = [queued(user, user * 100 + n) for user in range(1, 6) for n in range(user)]
    result = interleave_by_user(batch)
    assert sorted(r["id"] for r in result) == sorted(r["id"] for r in batch)
    assert len(result) == len(batch)


def test_an_empty_batch_is_handled() -> None:
    assert interleave_by_user([]) == []
