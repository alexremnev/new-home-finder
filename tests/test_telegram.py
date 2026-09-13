from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from worker.contracts.notify import Action, Alert, ListingView, Recipient
from worker.notify.telegram import (
    TelegramNotifier,
    is_blocked_by_user,
    money,
    plural,
    render,
    render_listing,
)

def view(**overrides: Any) -> ListingView:
    values: dict[str, Any] = {
        "price_pcm": 1950,
        "bedrooms": 2,
        "property_type": "flat",
        "district": "SE16",
        "zone": 2,
        "available_from": date(2026, 9, 12),
        "furnished": "furnished",
        "pets_allowed": True,
        "bills_included": False,
        "min_tenancy_months": 12,
        "source_display": "OpenRent",
        "is_landlord_direct": True,
        "url": "https://www.openrent.co.uk/property-to-rent/london/x/123",
    }
    values.update(overrides)
    return ListingView(**values)

def test_a_full_listing_renders_every_line() -> None:
    text = render_listing(view())
    print(text)
    assert "🏠 <b>New listing spotted!</b>" in text
    assert "<b>£1,950/month</b>" in text
    assert "2 Bedrooms" in text
    assert "SE16" in text
    assert "Available from 12 September 2026" in text
    assert "🐾 Pets allowed" in text
    assert "🛋 Furnished" in text
    assert "https://www.openrent.co.uk" in text

def test_unknown_values_drop_their_line_rather_than_saying_unknown() -> None:
    text = render_listing(
        view(available_from=None, min_tenancy_months=None, pets_allowed=None,
             bills_included=None, furnished="unknown", district=None, zone=None)
    )
    assert "📅" not in text
    assert "📍" not in text
    assert "📮" not in text
    assert "Pets" not in text
    assert "Bills" not in text
    assert "🛋" not in text
    assert "<b>£1,950/month</b>" in text and "https://" in text

def test_nothing_is_said_about_what_the_listing_did_not_say() -> None:
    text = render_listing(view(pets_allowed=False, bills_included=True))
    assert "not allowed" not in text
    assert "Bills" not in text

def test_neither_photographs_nor_a_description_are_reproduced() -> None:

    text = render_listing(view())
    assert "img" not in text.lower()
    assert text.count("http") == 1

def test_pets_appear_only_when_the_listing_allows_them() -> None:
    assert "🐾 Pets allowed" in render_listing(view(pets_allowed=True))
    assert "Pets" not in render_listing(view(pets_allowed=False))
    assert "Pets" not in render_listing(view(pets_allowed=None))

def test_the_share_is_named_when_a_plan_withholds_matches() -> None:
    assert "20% of new listings" in render_listing(view(share=20))
    assert "💎" not in render_listing(view(share=100))
    assert "💎" not in render_listing(view())

def test_the_postcode_links_to_a_map_and_the_listing_link_is_bare() -> None:
    text = render_listing(view(postcode="SE16 4TH"))
    assert 'href="https://www.google.com/maps/search/?api=1&amp;query=SE16%204TH"' in text
    assert text.strip().endswith("/property-to-rent/london/x/123")

def test_every_interpolated_value_is_escaped() -> None:
    text = render_listing(view(area="Bow & Bromley", address="<Flat 3>, Ropery St"))
    assert "&amp;" in text and "&lt;Flat 3&gt;" in text
    assert text.count("<b>") == text.count("</b>")

def test_a_studio_is_not_called_zero_bedrooms() -> None:
    text = render_listing(view(bedrooms=0, property_type="studio"))
    assert "🛏️ Studio" in text
    assert "0 Bedroom" not in text

def test_a_room_in_a_shared_flat_is_not_called_a_one_bedroom() -> None:
    text = render_listing(view(bedrooms=1, property_type="room"))
    assert "Room in a shared flat" in text
    assert "1 Bedroom" not in text

@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "1 Bedroom"), (2, "2 Bedrooms"), (5, "5 Bedrooms")],
)
def test_plural_agreement(count: int, expected: str) -> None:
    assert plural(count, "Bedroom") == expected

def test_prices_are_grouped() -> None:
    assert money(1950) == "£1,950"
    assert money(12000) == "£12,000"
    assert money(900) == "£900"

def test_a_text_alert_renders_its_actions_as_plain_links() -> None:
    alert = Alert(
        kind="stopped", text="Thanks for using the service.",
        actions=[Action(label="Leave a review", url="https://x/review")],
    )
    text = render(alert)
    assert "Thanks for using the service." in text
    assert "Leave a review: https://x/review" in text

def test_a_listing_alert_without_a_listing_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        render(Alert(kind="listing"))

def test_a_message_is_cut_to_the_platform_limit() -> None:
    alert = Alert(kind="ops", text="x" * 9000)
    assert len(render(alert)) == 4096

def test_a_successful_send_returns_the_message_id() -> None:
    sent: list[dict[str, Any]] = []

    def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        sent.append(payload)
        return {"ok": True, "result": {"message_id": 42}}

    result = TelegramNotifier("token", sender).send(
        Recipient(channel="telegram", address="555"), Alert(kind="listing", listing=view())
    )
    assert result.ok
    assert result.provider_msg_id == "42"
    assert sent[0]["chat_id"] == "555"
    assert sent[0]["parse_mode"] == "HTML"
    assert sent[0]["link_preview_options"]["url"] == view().url
    assert sent[0]["link_preview_options"]["prefer_large_media"] is True
    assert "show_above_text" not in sent[0]["link_preview_options"]

def test_a_plan_notice_gets_no_preview_and_no_upgrade_button() -> None:
    sent: list[dict[str, Any]] = []

    def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        sent.append(payload)
        return {"ok": True, "result": {"message_id": 1}}

    TelegramNotifier("token", sender).send(
        Recipient(channel="telegram", address="1"),
        Alert(kind="expiring", text="Your trial ends tomorrow."),
    )
    assert sent[0]["link_preview_options"] == {"is_disabled": True}
    assert "reply_markup" not in sent[0]

def test_a_missing_token_fails_without_pretending_to_retry() -> None:
    result = TelegramNotifier(None).send(
        Recipient(channel="telegram", address="1"), Alert(kind="ops", text="hi")
    )
    assert not result.ok and not result.retryable

@pytest.mark.parametrize(
    ("code", "retryable"),
    [(429, True), (500, True), (503, True), (403, False), (400, False)],
)
def test_which_failures_are_worth_retrying(code: int, retryable: bool) -> None:

    def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error_code": code, "description": "nope"}

    result = TelegramNotifier("token", sender).send(
        Recipient(channel="telegram", address="1"), Alert(kind="ops", text="hi")
    )
    assert not result.ok
    assert result.retryable is retryable

def test_a_transport_failure_is_retryable() -> None:
    def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError("no route")

    result = TelegramNotifier("token", sender).send(
        Recipient(channel="telegram", address="1"), Alert(kind="ops", text="hi")
    )
    assert not result.ok and result.retryable

def test_a_blocked_recipient_is_recognised() -> None:
    def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error_code": 403, "description": "bot was blocked by the user"}

    result = TelegramNotifier("token", sender).send(
        Recipient(channel="telegram", address="1"), Alert(kind="ops", text="hi")
    )
    assert is_blocked_by_user(result)

def test_the_notifier_registered_itself() -> None:
    from worker.contracts.notify import NOTIFIERS

    assert "telegram" in NOTIFIERS
