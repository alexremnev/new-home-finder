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


# ── the shape of a listing message ────────────────────────────────────────


def test_a_full_listing_renders_every_line() -> None:
    text = render_listing(view())
    print(text)
    assert "£1,950/мес" in text
    assert "2 спальни" in text
    assert "SE16" in text and "Zone 2" in text
    assert "12 сен" in text
    assert "мин. 12 мес" in text
    assert "Питомцы: можно" in text
    assert "Счета: не включены" in text
    assert "https://www.openrent.co.uk" in text
    assert "напрямую от собственника" in text
    assert "/stop" in text


def test_unknown_values_drop_their_line_rather_than_saying_unknown() -> None:
    """A shorter message that does not claim to know things it does not."""
    text = render_listing(
        view(available_from=None, min_tenancy_months=None, pets_allowed=None,
             bills_included=None, furnished="unknown", district=None, zone=None)
    )
    assert "📅" not in text
    assert "📍" not in text
    assert "Питомцы" not in text
    assert "Счета" not in text
    assert "🛋" not in text
    # The essentials survive.
    assert "£1,950/мес" in text and "https://" in text and "/stop" in text


def test_every_message_carries_the_unsubscribe_line() -> None:
    for extra in ({}, {"pets_allowed": None}, {"district": None, "zone": None}):
        assert "/stop" in render_listing(view(**extra))


def test_neither_photographs_nor_a_description_are_reproduced() -> None:
    """Facts and a link to the original, nothing of the listing's content."""
    text = render_listing(view())
    assert "img" not in text.lower()
    assert text.count("http") == 1


def test_the_three_states_of_a_flag_render_differently() -> None:
    assert "Питомцы: можно" in render_listing(view(pets_allowed=True))
    assert "Питомцы: нельзя" in render_listing(view(pets_allowed=False))
    text = render_listing(view(pets_allowed=None))
    assert "Питомцы" not in text


# ── wording details ───────────────────────────────────────────────────────


def test_a_studio_is_not_called_zero_bedrooms() -> None:
    text = render_listing(view(bedrooms=0, property_type="studio"))
    assert "студия" in text
    assert "0 " not in text
    # The word already names the type; repeating it reads as "студия · studio".
    assert "studio" not in text
    assert text.splitlines()[0].count("·") == 1


def test_a_room_in_a_shared_property_is_named_as_one() -> None:
    text = render_listing(view(bedrooms=1, property_type="room"))
    assert "комната" in text
    # The type is already in the word; it is not repeated.
    assert text.count("комната") == 1


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "спальня"), (2, "спальни"), (3, "спальни"), (4, "спальни"),
     (5, "спален"), (11, "спален"), (12, "спален"), (21, "спальня"), (22, "спальни")],
)
def test_plural_agreement(count: int, expected: str) -> None:
    assert plural(count, ("спальня", "спальни", "спален")) == expected


def test_prices_are_grouped() -> None:
    assert money(1950) == "£1,950"
    assert money(12000) == "£12,000"
    assert money(900) == "£900"


# ── other message kinds ───────────────────────────────────────────────────


def test_a_text_alert_renders_its_actions_as_plain_links() -> None:
    alert = Alert(
        kind="stopped", text="Спасибо!",
        actions=[Action(label="Отзыв", url="https://x/review")],
    )
    text = render(alert)
    assert "Спасибо!" in text
    assert "Отзыв: https://x/review" in text


def test_a_listing_alert_without_a_listing_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        render(Alert(kind="listing"))


def test_a_message_is_cut_to_the_platform_limit() -> None:
    alert = Alert(kind="ops", text="x" * 9000)
    assert len(render(alert)) == 4096


# ── sending ───────────────────────────────────────────────────────────────


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
    # A preview would pull the site's own photograph into our message.
    assert sent[0]["disable_web_page_preview"] is True
    # No parse mode: a listing line is full of MarkdownV2 syntax characters, and
    # one missed escape is a rejected message rather than an ugly one.
    assert "parse_mode" not in sent[0]


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
    """A blocked bot cannot be fixed by trying again, and repetition counts
    against the bot's standing."""
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
