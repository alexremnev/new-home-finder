from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any

import pytest

from worker.contracts.notify import Action, Alert, ListingView, Recipient
from worker.notify.telegram import render_listing as render_telegram
from worker.notify.whatsapp import (
    WhatsAppNotifier,
    configured,
    digits,
    offerable,
    render,
    render_listing,
    template_params,
    upload_image,
    window_open,
)

def view(**overrides: Any) -> ListingView:
    values: dict[str, Any] = {
        "price_pcm": 1950,
        "bedrooms": 2,
        "property_type": "flat",
        "district": "SE16",
        "postcode": "SE16 4TH",
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

def facts(text: str) -> list[str]:

    # The wording with the markup taken off, so the two channels can be compared
    # on what they say rather than on how they say it. The anchor goes too: the
    # map link is Telegram's way of showing a postcode, not a separate fact.
    stripped = re.sub(r"</?b>|\*|</a>", "", text)
    stripped = re.sub(r'<a href="[^"]*">', "", stripped)
    return [unescape(line) for line in stripped.split("\n")]

def test_both_channels_state_the_same_facts_in_the_same_order() -> None:
    subject = view(
        share=20, lapsed="trial", bathrooms=2, size_text="47.29 sq m",
        area="Bow & Bromley", address="Flat 3, Ropery St",
    )
    assert facts(render_listing(subject)) == facts(render_telegram(subject))

def test_they_agree_on_a_studio_a_room_and_full_access_too() -> None:

    for extra in (
        {"bedrooms": 0, "property_type": "studio"},
        {"bedrooms": 1, "property_type": "room"},
        {"share": None, "postcode": None},
        {"share": 10, "lapsed": "plan", "pets_allowed": None, "furnished": "unknown"},
    ):
        subject = view(**extra)
        assert facts(render_listing(subject)) == facts(render_telegram(subject)), extra

def test_emphasis_uses_the_marker_whatsapp_understands() -> None:
    text = render_listing(view())
    assert "🏠 *New listing spotted!*" in text
    assert "💷 *£1,950/month*" in text
    assert "<b>" not in text and "</b>" not in text

def test_the_only_link_is_the_listing_so_the_preview_is_the_flat() -> None:

    text = render_listing(view(postcode="SE16 4TH"))
    assert text.count("http") == 1
    assert "maps.google" not in text and "google.com/maps" not in text
    assert text.split("\n")[-1] == view().url

def test_the_restricted_notice_is_emphasised_and_last() -> None:
    text = render_listing(view(share=20, lapsed="plan"))
    assert text.endswith(
        "🔒 *Your plan has ended. Access is now limited to 20% of property "
        "listings. Upgrade today for full access* — you are missing 80% of what matches."
    )

def test_a_button_only_action_is_left_out_rather_than_described() -> None:

    alert = Alert(kind="listing", listing=view(), actions=[
        Action(label="Ignore", callback="ignore:7"),
    ])
    assert "Ignore" not in render(alert)

def test_a_link_action_becomes_a_line() -> None:
    alert = Alert(kind="expiring", text="Your plan ends tomorrow.", actions=[
        Action(label="Upgrade today for full access", url="https://t.me/bot?start=pay"),
        Action(label="Pause all notifications", callback="pause"),
    ])
    text = render(alert)
    assert text.startswith("Your plan ends tomorrow.")
    assert "Upgrade today for full access: https://t.me/bot?start=pay" in text
    assert "Pause all notifications" not in text

@pytest.mark.parametrize(
    ("given", "expected"),
    [("+44 7700 900123", "447700900123"), ("447700900123", "447700900123"),
     ("(44) 7700-900123", "447700900123"), ("no digits here", "")],
)
def test_a_number_is_reduced_to_its_digits(given: str, expected: str) -> None:
    assert digits(given) == expected

def sent_through(**response: Any) -> tuple[list[dict[str, Any]], Any]:
    calls: list[dict[str, Any]] = []

    def sender(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        calls.append({"url": url, "payload": payload, "headers": headers})
        return response

    return calls, sender

def notifier(sender: Any = None, **extra: Any) -> WhatsAppNotifier:
    settings: dict[str, Any] = {
        "phone_id": "1234567890",
        "token": "EAAG-secret",
        "template": "new_listing",
        "language": "en",
        "link_prefix": "https://londonhomefinder.co.uk/l",
    }
    settings.update(extra)
    return WhatsAppNotifier(sender=sender, **settings)

def hours_ago(count: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=count)

def test_the_window_is_open_for_a_day_and_not_a_minute_longer() -> None:
    assert window_open(hours_ago(0.1)) is True
    assert window_open(hours_ago(23.9)) is True
    assert window_open(hours_ago(24.1)) is False
    assert window_open(None) is False

def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing() -> None:

    # psycopg returns tz-aware values, but a hand-built view or a test fixture
    # may not, and comparing the two raises.
    assert window_open(datetime.utcnow().replace(tzinfo=None)) is True

def test_inside_the_window_the_message_is_the_same_one_telegram_gets() -> None:
    calls, sender = sent_through(messages=[{"id": "wamid.1"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="+44 7700 900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view()),
    )
    assert result.ok and result.provider_msg_id == "wamid.1"
    body = calls[0]["payload"]
    assert body["type"] == "text"
    assert body["to"] == "447700900123"
    assert body["text"]["preview_url"] is True
    assert "🏠 *New listing spotted!*" in body["text"]["body"]
    assert calls[0]["headers"]["Authorization"] == "Bearer EAAG-secret"
    assert calls[0]["url"].endswith("/1234567890/messages")

def test_outside_the_window_it_becomes_the_approved_template() -> None:
    calls, sender = sent_through(messages=[{"id": "wamid.2"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="listing", listing=view(listing_id=4242)),
    )
    assert result.ok
    body = calls[0]["payload"]
    assert body["type"] == "template"
    assert body["template"]["name"] == "new_listing"
    assert body["template"]["language"] == {"code": "en"}

    parts = body["template"]["components"]
    assert [p["type"] for p in parts] == ["body", "button"]
    assert [p["text"] for p in parts[0]["parameters"]] == [
        "SE16 4TH", "£1,950", "2 Bedrooms", "—", "Available from 12 September 2026",
        "Furnished",
    ]
    assert parts[1]["parameters"][0]["text"] == "4242"

def test_a_template_parameter_is_never_empty_and_never_wraps() -> None:

    # Meta rejects both, and an absent fact is the normal case.
    params = template_params(
        view(area=None, address=None, postcode=None, district=None, bathrooms=None,
             available_from=None, furnished="unknown", size_text=None)
    )
    assert all(p and "\n" not in p for p in params), params
    assert params.count("—") == 4

def test_a_long_address_is_flattened_rather_than_broken() -> None:
    params = template_params(view(area="Canary\nWharf", address="Flat 3,\n  Ropery St"))
    assert params[0] == "Canary Wharf, Flat 3, Ropery St"

def test_a_studio_and_a_room_keep_their_names_in_the_template() -> None:
    assert template_params(view(bedrooms=0, property_type="studio"))[2] == "Studio"
    assert (
        template_params(view(bedrooms=1, property_type="room"))[2]
        == "Room in a shared flat"
    )

def test_without_a_listing_id_the_button_is_left_off_rather_than_broken() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="listing", listing=view(listing_id=None)),
    )
    parts = calls[0]["payload"]["template"]["components"]
    assert [p["type"] for p in parts] == ["body"]

PICTURE = "https://media.rightmove.co.uk/dir/crop/10:9/93k/1_0.jpeg"

MEDIA_ID = "1234567890123456"

def test_the_uploaded_photograph_wins_over_the_portal_url() -> None:

    # The feed's own photograph exists for every portal; og:image only for the
    # ones that answer. So when both are present, the uploaded one is used.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=PICTURE, wa_media_id=MEDIA_ID)),
    )
    body = calls[0]["payload"]
    assert body["type"] == "image"
    assert body["image"] == {"id": MEDIA_ID, "caption": body["image"]["caption"]}
    assert "link" not in body["image"]

def test_the_portal_url_is_the_fallback_when_there_is_no_upload() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=PICTURE, wa_media_id=None)),
    )
    assert calls[0]["payload"]["image"]["link"] == PICTURE

def test_a_template_header_takes_the_upload_too() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender, image_header=True).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="listing", listing=view(wa_media_id=MEDIA_ID, listing_id=7)),
    )
    header = calls[0]["payload"]["template"]["components"][0]
    assert header["type"] == "header"
    assert header["parameters"][0]["image"] == {"id": MEDIA_ID}

def test_with_neither_a_photograph_nor_a_url_it_is_plain_text() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=None, wa_media_id=None)),
    )
    assert calls[0]["payload"]["type"] == "text"

def test_an_upload_needs_credentials_and_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WA_PHONE_NUMBER_ID", raising=False)
    monkeypatch.delenv("WA_ACCESS_TOKEN", raising=False)
    assert upload_image(b"abc") is None
    assert configured() is False

    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "1")
    monkeypatch.setenv("WA_ACCESS_TOKEN", "t")
    assert configured() is True
    # Credentials but nothing to send is still nothing to do.
    assert upload_image(b"") is None

def test_inside_the_window_a_picture_is_sent_as_a_picture() -> None:
    calls, sender = sent_through(messages=[{"id": "wamid.3"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=PICTURE)),
    )
    assert result.ok
    body = calls[0]["payload"]

    # Not a link preview: WhatsApp's own thumbnail is small and compressed, and
    # there is no setting for it.
    assert body["type"] == "image"
    assert body["image"]["link"] == PICTURE
    assert "🏠 *New listing spotted!*" in body["image"]["caption"]
    assert "text" not in body

def test_a_message_too_long_for_a_caption_keeps_its_words_not_its_picture() -> None:

    # Cutting the message to fit the picture would lose the price or the link.
    # The picture is the part that can be spared.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=PICTURE, address="x" * 4000)),
    )
    body = calls[0]["payload"]
    assert body["type"] == "text"
    assert len(body["text"]["body"]) > 1024
    assert "x" * 100 in body["text"]["body"]

def test_without_a_picture_it_falls_back_to_the_preview() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=None)),
    )
    assert calls[0]["payload"]["type"] == "text"
    assert calls[0]["payload"]["text"]["preview_url"] is True

def test_a_template_gets_no_header_until_the_template_has_one() -> None:

    # Sending a component the approved template does not declare is error
    # 132000, and every alert would fail. So it is a setting, not a guess.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="listing", listing=view(image_url=PICTURE, listing_id=7)),
    )
    parts = calls[0]["payload"]["template"]["components"]
    assert [p["type"] for p in parts] == ["body", "button"]

def test_a_template_that_declares_a_header_gets_the_picture() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender, image_header=True).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="listing", listing=view(image_url=PICTURE, listing_id=7)),
    )
    parts = calls[0]["payload"]["template"]["components"]
    assert [p["type"] for p in parts] == ["header", "body", "button"]
    assert parts[0]["parameters"][0]["image"]["link"] == PICTURE

def test_a_header_is_left_off_when_there_is_no_picture_to_put_in_it() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender, image_header=True).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="listing", listing=view(image_url=None, listing_id=7)),
    )
    parts = calls[0]["payload"]["template"]["components"]
    assert [p["type"] for p in parts] == ["body", "button"]

def test_the_image_header_is_off_unless_the_setting_says_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WA_TEMPLATE_IMAGE", raising=False)
    assert WhatsAppNotifier.from_env().image_header is False
    monkeypatch.setenv("WA_TEMPLATE_IMAGE", "true")
    assert WhatsAppNotifier.from_env().image_header is True

def test_inside_the_window_taps_become_reply_buttons() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="expiring", text="🔔 12 new listings matched your filter today.",
              actions=[
                  Action(label="Upgrade today for full access", url="https://t.me/b?start=pay"),
                  Action(label="✏️ Change my search", short="Change search", callback="change"),
                  Action(label="🏠 I found a place", short="Found a place", callback="found"),
                  Action(label="⏸️ Pause alerts", short="Pause alerts", callback="pause"),
              ]),
    )
    assert result.ok
    body = calls[0]["payload"]
    assert body["type"] == "interactive"
    assert body["interactive"]["type"] == "button"

    buttons = body["interactive"]["action"]["buttons"]
    # Three is the ceiling WhatsApp imposes, and the url is not one of them.
    assert len(buttons) == 3
    assert [b["reply"]["id"] for b in buttons] == ["change", "found", "pause"]
    assert [b["reply"]["title"] for b in buttons] == [
        "Change search", "Found a place", "Pause alerts",
    ]
    assert all(len(b["reply"]["title"]) <= 20 for b in buttons)
    # The link still reaches the person, in the text.
    assert "https://t.me/b?start=pay" in body["interactive"]["body"]["text"]

def test_whatsapp_does_not_offer_to_dismiss_a_listing() -> None:
    from worker.pipeline.outbox import listing_actions

    actions = listing_actions(view(), 77)
    # Telegram deletes the message, so "Ignore" is honest there and is built.
    assert [a.label for a in actions] == ["Ignore"]
    # WhatsApp cannot delete a delivered message, so it declines to offer it
    # rather than drawing a button that would visibly do nothing.
    assert not offerable(actions[0])

def test_a_pictureless_listing_inside_the_window_is_plain_text() -> None:

    # Nothing is offerable on a listing here, so there is no interactive
    # message to send even though the window is open.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=None),
              actions=[Action(label="Ignore", callback="ignore:7")]),
    )
    assert calls[0]["payload"]["type"] == "text"

def test_a_restricted_listing_still_offers_no_buttons_only_the_link() -> None:

    # "Get full access" is a url, and a url is never a reply button.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=None, share=20),
              actions=[Action(label="Get full access", url="https://t.me/b?start=pay")]),
    )
    assert calls[0]["payload"]["type"] == "text"
    assert "https://t.me/b?start=pay" in calls[0]["payload"]["text"]["body"]

def test_a_long_label_is_cut_to_what_a_button_holds() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="expiring", text="x", actions=[
            Action(label="Pause absolutely everything right now", callback="pause"),
        ]),
    )
    title = calls[0]["payload"]["interactive"]["action"]["buttons"][0]["reply"]["title"]
    assert len(title) == 20, title

def test_a_message_with_no_taps_stays_plain_text() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="expiring", text="Your plan ends tomorrow."),
    )
    assert calls[0]["payload"]["type"] == "text"

def test_a_listing_with_a_picture_is_still_a_picture_not_a_button_message() -> None:

    # The Ignore action is a callback, but a listing's picture matters more than
    # a button: an image message cannot carry reply buttons.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=PICTURE),
              actions=[Action(label="Ignore", callback="ignore:7")]),
    )
    assert calls[0]["payload"]["type"] == "image"

def test_a_plan_notice_outside_the_window_waits_rather_than_being_refused() -> None:
    result = notifier().send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="expiring", text="Your plan ends tomorrow."),
    )
    assert not result.ok and result.retryable
    assert "no template" in (result.error or "")

def test_a_plan_notice_inside_the_window_goes_as_text() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(2)),
        Alert(kind="expiring", text="Your plan ends tomorrow."),
    )
    assert result.ok
    assert calls[0]["payload"]["text"]["preview_url"] is False

def test_no_template_configured_is_a_setting_rather_than_a_retry() -> None:
    result = notifier(template=None).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=None),
        Alert(kind="listing", listing=view()),
    )
    assert not result.ok and not result.retryable
    assert "WA_TEMPLATE_NAME" in (result.error or "")

def test_missing_credentials_fail_without_pretending_to_retry() -> None:
    result = WhatsAppNotifier(None, None).send(
        Recipient(channel="whatsapp", address="447700900123"),
        Alert(kind="listing", listing=view()),
    )
    assert not result.ok and not result.retryable
    assert "WA_PHONE_NUMBER_ID" in (result.error or "")

def test_an_address_that_is_not_a_number_is_not_retried_forever() -> None:
    result = notifier().send(
        Recipient(channel="whatsapp", address="someone@example.com"),
        Alert(kind="listing", listing=view()),
    )
    assert not result.ok and not result.retryable and result.recipient_gone

@pytest.mark.parametrize(
    ("code", "retryable", "gone"),
    [
        (130429, True, False),
        (131048, True, False),
        (500, True, False),
        (503, True, False),
        (131026, False, True),
        (131052, False, True),
        (131047, False, False),
        (132000, False, False),
        (132015, False, False),
        (133010, False, False),
    ],
)
def test_which_graph_errors_are_worth_another_attempt(
    code: int, retryable: bool, gone: bool
) -> None:
    _, sender = sent_through(error={"code": code, "message": "no"})
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view()),
    )
    assert not result.ok
    assert result.retryable is retryable
    assert result.recipient_gone is gone

def test_a_whatsapp_code_is_not_mistaken_for_an_http_status() -> None:

    # Every WhatsApp error code has five or six digits, so treating "500 or more"
    # as a server error made all of them retryable — including a template that
    # does not match its parameters, which will never succeed.
    _, sender = sent_through(error={"code": 132000, "message": "parameter mismatch"})
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view()),
    )
    assert not result.retryable

def test_a_code_hidden_in_error_data_is_still_recognised() -> None:
    _, sender = sent_through(
        error={"code": 131000, "message": "generic", "error_data": {"details_code": 131026}}
    )
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view()),
    )
    assert result.recipient_gone and not result.retryable

def test_the_channel_reads_its_own_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "999")
    monkeypatch.setenv("WA_ACCESS_TOKEN", "from-env")
    monkeypatch.setenv("WA_TEMPLATE_NAME", "listing_v2")
    built = WhatsAppNotifier.from_env()
    assert built.phone_id == "999"
    assert built.token == "from-env"
    assert built.template == "listing_v2"

def test_ops_alerts_do_not_go_to_customers_on_whatsapp() -> None:

    assert notifier().supports("listing")
    assert not notifier().supports("ops")
