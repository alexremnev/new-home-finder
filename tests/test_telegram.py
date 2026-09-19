from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from worker.contracts.notify import Action, Alert, ListingView, Recipient
from worker.notify.fields import money, plural
from worker.notify.telegram import (
    TelegramNotifier,
    is_blocked_by_user,
    keyboard_for,
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

def test_an_ended_trial_is_named_as_a_trial() -> None:
    text = render_listing(view(share=20, lapsed="trial"))
    assert "🔒 <b>Your free trial has ended." in text
    assert "only 20% of available properties" in text
    assert "Please make a payment to restore full access.</b>" in text
    assert "plan has ended" not in text

def test_the_notice_sits_against_the_listing_with_no_gap() -> None:

    # The same shape as WhatsApp. A gap here and none there would be two
    # different messages about the same flat.
    assert "" not in render_listing(view(share=20, lapsed="trial")).split("\n")

def test_an_ended_plan_is_not_called_a_trial() -> None:
    text = render_listing(view(share=20, lapsed="plan"))
    assert "🔒 <b>Your plan has ended." in text
    assert "limited to 20% of property listings" in text
    assert "Upgrade today for full access</b>" in text
    assert "missing 80%" in text
    assert "free trial" not in text

def test_the_share_comes_from_the_plan_rather_than_a_fixed_number() -> None:

    assert "only 10% of available properties" in render_listing(
        view(share=10, lapsed="trial")
    )
    assert "missing 90%" in render_listing(view(share=10, lapsed="plan"))

def test_full_access_is_told_nothing_about_upgrading() -> None:
    for full in (view(share=100), view(share=None), view()):
        text = render_listing(full)
        assert "🔒" not in text
        assert "ended" not in text

def test_no_blank_line_separates_the_details_from_the_link() -> None:

    text = render_listing(view())
    lines = text.split("\n")
    assert lines[-1] == view().url
    assert lines[-2] != ""

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

def test_an_action_becomes_a_button_and_not_a_line_of_text() -> None:
    alert = Alert(
        kind="stopped", text="Thanks for using the service.",
        actions=[Action(label="Leave a review", url="https://x/review")],
    )
    assert render(alert) == "Thanks for using the service."
    assert keyboard_for(alert) == {
        "inline_keyboard": [[{"text": "Leave a review", "url": "https://x/review"}]]
    }

def test_a_callback_action_carries_data_rather_than_a_url() -> None:
    alert = Alert(kind="listing", listing=view(), actions=[
        Action(label="Ignore", callback="ignore:77"),
    ])
    assert keyboard_for(alert) == {
        "inline_keyboard": [[{"text": "Ignore", "callback_data": "ignore:77"}]]
    }

def test_each_action_gets_its_own_row_in_the_order_given() -> None:
    alert = Alert(kind="expiring", text="x", actions=[
        Action(label="Upgrade today for full access", url="https://t.me/bot?start=pay"),
        Action(label="Pause all notifications", callback="pause"),
    ])
    keyboard = keyboard_for(alert)
    assert keyboard is not None
    assert [row[0]["text"] for row in keyboard["inline_keyboard"]] == [
        "Upgrade today for full access",
        "Pause all notifications",
    ]

def test_an_action_with_neither_a_url_nor_a_callback_is_dropped() -> None:

    assert keyboard_for(Alert(kind="ops", text="x", actions=[Action(label="Nothing")])) is None

def test_no_actions_means_no_keyboard_at_all() -> None:
    assert keyboard_for(Alert(kind="ops", text="x")) is None

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
