from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any

import pytest

from worker.contracts.notify import Action, Alert, ListingView, Recipient
from worker.notify.plans import GIFT_BUTTON, GIFT_TAP, gift_offer
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

# The portal url the fixture above carries, named so a test can say "this link
# is the button" without writing it out again.
LISTING = "https://www.openrent.co.uk/property-to-rent/london/x/123"

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
    # No gap before it, and none before the link either: the message reads as
    # one block.
    assert "" not in text.split("\n")

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

def sent_in_turn(*responses: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:

    # A different answer per call, which sent_through cannot do: the fallback
    # only means anything when the first attempt is refused and the second is
    # not.
    calls: list[dict[str, Any]] = []

    def sender(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        calls.append({"url": url, "payload": payload, "headers": headers})
        return responses[min(len(calls) - 1, len(responses) - 1)]

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
    assert body["to"] == "447700900123"
    assert "🏠 *New listing spotted!*" in body["interactive"]["body"]["text"]
    assert calls[0]["headers"]["Authorization"] == "Bearer EAAG-secret"
    assert calls[0]["url"].endswith("/1234567890/messages")

def test_the_listing_link_is_a_view_button_rather_than_a_line_of_url() -> None:

    # A url written out in the body is a url somebody has to select and copy,
    # and on a listing the tap is the whole point.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view()),
    )
    body = calls[0]["payload"]
    assert body["type"] == "interactive"
    assert body["interactive"]["type"] == "cta_url"
    assert body["interactive"]["action"] == {
        "name": "cta_url",
        "parameters": {"display_text": "View", "url": LISTING},
    }
    # Once, as the button. Repeating it in the body would be the same link twice.
    assert LISTING not in body["interactive"]["body"]["text"]

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
    header = calls[0]["payload"]["interactive"]["header"]
    assert header == {"type": "image", "image": {"id": MEDIA_ID}}

def test_the_portal_url_is_the_fallback_when_there_is_no_upload() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=PICTURE, wa_media_id=None)),
    )
    header = calls[0]["payload"]["interactive"]["header"]
    assert header == {"type": "image", "image": {"link": PICTURE}}

def test_a_template_header_takes_the_upload_too() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender, image_header=True).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="listing", listing=view(wa_media_id=MEDIA_ID, listing_id=7)),
    )
    header = calls[0]["payload"]["template"]["components"][0]
    assert header["type"] == "header"
    assert header["parameters"][0]["image"] == {"id": MEDIA_ID}

def test_with_no_photograph_the_button_message_simply_has_no_header() -> None:

    # The View button does not depend on the picture, and the picture is the
    # part that can be absent.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=None, wa_media_id=None)),
    )
    interactive = calls[0]["payload"]["interactive"]
    assert interactive["type"] == "cta_url"
    assert "header" not in interactive

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

def test_inside_the_window_a_picture_is_still_a_picture_beside_the_button() -> None:
    calls, sender = sent_through(messages=[{"id": "wamid.3"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=PICTURE)),
    )
    assert result.ok
    interactive = calls[0]["payload"]["interactive"]

    # Not a link preview: WhatsApp's own thumbnail is small and compressed, and
    # there is no setting for it. A cta_url header is a real image message, so
    # the View button costs nothing here.
    assert interactive["header"] == {"type": "image", "image": {"link": PICTURE}}
    assert "🏠 *New listing spotted!*" in interactive["body"]["text"]
    assert "text" not in calls[0]["payload"]

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

def test_a_message_too_long_for_a_button_keeps_its_words_and_its_link() -> None:

    # The same trade as the caption above, for the same reason: a message cut
    # off mid-fact is worse than a link somebody has to copy.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=None, address="x" * 4000)),
    )
    body = calls[0]["payload"]
    assert body["type"] == "text"
    assert body["text"]["preview_url"] is True
    assert len(body["text"]["body"]) > 1024
    assert "x" * 100 in body["text"]["body"]

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
                  Action(label="✏️ Change search", short="Change search", callback="change"),
                  Action(label="⏸️ Pause alerts", short="Pause alerts", callback="pause"),
                  Action(label="🔔 Keep searching", short="Keep searching",
                         callback="continue"),
              ]),
    )
    assert result.ok
    body = calls[0]["payload"]
    assert body["type"] == "interactive"
    assert body["interactive"]["type"] == "button"

    buttons = body["interactive"]["action"]["buttons"]
    # Three is the ceiling WhatsApp imposes, and the url is not one of them.
    assert len(buttons) == 3
    assert [b["reply"]["id"] for b in buttons] == ["change", "pause", "continue"]
    assert [b["reply"]["title"] for b in buttons] == [
        "Change search", "Pause alerts", "Keep searching",
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

def test_an_unofferable_action_does_not_cost_the_listing_its_view_button() -> None:

    # Ignore is not drawn here — WhatsApp cannot delete a delivered message —
    # so it must not count as "this message already has buttons" either.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=None),
              actions=[Action(label="Ignore", callback="ignore:7")]),
    )
    interactive = calls[0]["payload"]["interactive"]
    assert interactive["type"] == "cta_url"
    assert interactive["action"]["parameters"]["display_text"] == "View"

def restricted(**extra: Any) -> Alert:
    return Alert(
        kind="listing",
        listing=view(share=20, lapsed="trial", **extra),
        actions=[Action(label="Get full access", url="https://pay.test/upgrade?t=abc")],
    )

def test_the_payment_link_is_a_button_not_a_line_of_text() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        restricted(image_url=None),
    )
    interactive = calls[0]["payload"]["interactive"]
    assert interactive["type"] == "cta_url"
    assert interactive["action"] == {
        "name": "cta_url",
        "parameters": {
            "display_text": "Get full access",
            "url": "https://pay.test/upgrade?t=abc",
        },
    }
    # Once, as the button. Repeating it in the body would be the same link twice.
    assert "pay.test" not in interactive["body"]["text"]

def test_the_button_keeps_the_photograph_which_reply_buttons_cannot() -> None:
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        restricted(image_url=PICTURE),
    )
    interactive = calls[0]["payload"]["interactive"]
    assert interactive["header"] == {"type": "image", "image": {"link": PICTURE}}
    assert interactive["type"] == "cta_url"

def test_a_digest_keeps_its_three_taps_and_leaves_the_link_in_the_text() -> None:

    # One message cannot hold both, and three taps are worth more than one.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="expiring", text="🔔 12 new listings matched your filter today.",
              actions=[
                  Action(label="Upgrade today for full access", url="https://pay.test/u"),
                  Action(label="⏸️ Pause alerts", short="Pause alerts", callback="pause"),
              ]),
    )
    interactive = calls[0]["payload"]["interactive"]
    assert interactive["type"] == "button"
    assert "https://pay.test/u" in interactive["body"]["text"]

def test_a_refused_button_falls_back_to_the_plain_message() -> None:

    # A listing nobody sees is worse than a listing with no button, and the
    # cta_url shape is the newest thing sent here.
    calls, sender = sent_in_turn(
        {"error": {"code": 132000, "message": "unsupported header"}},
        {"messages": [{"id": "x"}]},
    )
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        restricted(image_url=PICTURE),
    )
    assert result.ok
    assert [call["payload"]["type"] for call in calls] == ["interactive", "image"]
    assert "pay.test" in calls[1]["payload"]["image"]["caption"]

def test_a_refused_plain_message_is_not_retried_in_a_loop() -> None:
    calls, sender = sent_in_turn({"error": {"code": 132000, "message": "nope"}})
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        restricted(image_url=PICTURE),
    )
    assert not result.ok
    assert len(calls) == 2

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

def test_a_listing_with_a_picture_keeps_both_the_picture_and_the_button() -> None:

    # Reply buttons cannot share a message with a photograph; the one url
    # button can, as the header of the same message. So nothing is traded.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view(image_url=PICTURE),
              actions=[Action(label="Ignore", callback="ignore:7")]),
    )
    interactive = calls[0]["payload"]["interactive"]
    assert interactive["header"] == {"type": "image", "image": {"link": PICTURE}}
    assert interactive["action"]["parameters"]["display_text"] == "View"

def test_an_ended_paid_plan_reaches_a_closed_window_through_the_template() -> None:

    # Somebody whose access just changed has, by definition, not written in for
    # a day — which is exactly when free-form text is shut off. Without this the
    # one notice that matters is the one that never arrives.
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(sender, notice_template="plan_notice_paid").send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(
            kind="expired",
            text="Your subscription has ended.",
            billable_template=True,
            checkout_token="AbC123xyz",
        ),
    )
    assert result.ok
    body = calls[0]["payload"]
    assert body["type"] == "template"
    assert body["template"]["name"] == "plan_notice_paid"

    # One component and no body among them: the template declares no variables
    # in its text, and a component it does not declare is 132000 on every send.
    components = body["template"]["components"]
    assert [c["type"] for c in components] == ["button"]
    assert components[0]["parameters"] == [{"type": "text", "text": "AbC123xyz"}]

def test_an_ended_trial_is_not_worth_a_marketing_template() -> None:

    # The template is a marketing message at three times the price of one
    # inside the window, and somebody who never paid has already had the
    # warning that went out five minutes before their window shut. So the
    # notice waits for a window that may reopen rather than being bought.
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(sender, notice_template="plan_notice_paid").send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="expired", text="Your free trial has ended."),
    )
    assert not result.ok and result.retryable
    assert "not worth the template" in (result.error or "")
    assert calls == []

def test_the_notice_template_is_never_sent_without_its_button_parameter() -> None:

    # The button is declared as a fixed url plus `{{1}}`, and a declared
    # variable is not optional: sending the template without it is 132000. So a
    # missing token is a send that is not attempted, and retryable, because
    # `upgrade_token` issues one on every drain.
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(sender, notice_template="plan_notice_paid").send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="expired", text="ended", billable_template=True),
    )
    assert not result.ok and result.retryable
    assert "checkout token" in (result.error or "")
    assert calls == []

def test_the_quick_reply_notice_sends_a_payload_and_needs_no_token() -> None:

    # The shape worth having, and the one thing it changes is the thing that
    # matters: a quick reply is an inbound message, so the tap reopens the
    # 24-hour window *before* the payment. With the url button it stays shut
    # through the payment, Meta refuses the receipt with 131047, and every
    # alert behind it waits in the queue. See 0070.
    #
    # No token travels with it: the site mints the link when the tap arrives,
    # which is also how it stops being most of a day old by the time somebody
    # presses it.
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(
        sender, notice_template="plan_notice_paid", notice_button="reply"
    ).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(kind="expired", text="ended", billable_template=True),
    )

    assert result.ok
    button = calls[0]["payload"]["template"]["components"][0]
    assert button["sub_type"] == "quick_reply"
    # What the site's webhook matches on — `pressed` there, NOTICE_TAP here.
    # Both halves are ours, and a payload nobody handles is a button that
    # visibly does nothing.
    assert button["parameters"] == [{"type": "payload", "payload": "pay"}]

def test_a_mistyped_button_shape_falls_back_to_the_live_one() -> None:

    # A template's buttons are declared, so the wrong shape is 132000 on every
    # notice rather than a cosmetic fault. `url` is what is live, which makes
    # it the safe landing place for a typo.
    assert notifier(notice_button="Reply").notice_button == "reply"
    assert notifier(notice_button="cta_url").notice_button == "url"
    assert notifier().notice_button == "url"

def test_the_rate_that_was_charged_travels_back_with_the_result() -> None:

    # Meta prices the template that reaches a shut window differently from
    # free-form text inside one, and the notifier is the only thing that knows
    # which it sent: `body` chose by asking about the window, and the ledger
    # needs the answer to price the row. Read off the payload rather than
    # recomputed from the clock — see 0063.
    ended = Alert(
        kind="expired", text="ended", billable_template=True, checkout_token="tok"
    )

    calls, sender = sent_through(messages=[{"id": "x"}])
    outside = notifier(sender, notice_template="plan_notice_paid").send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        ended,
    )
    assert calls[0]["payload"]["type"] == "template"
    # A utility template since Meta's second look at it, which is the same
    # $0.022 as free-form text. Told to the notifier rather than inferred,
    # because the category is Meta's to change and it has changed twice. See
    # 0066 and 0070.
    assert outside.billed_as == "utility"

    calls, sender = sent_through(messages=[{"id": "y"}])
    inside = notifier(sender, notice_template="plan_notice_paid").send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        ended,
    )
    assert calls[0]["payload"]["type"] == "text"
    assert inside.billed_as == "service"

def test_the_listing_template_is_still_a_utility_one() -> None:

    # Two templates and one rate between them today, but the notice's is a
    # setting — so a listing sent as a template must not pick it up. Pinned
    # with the notice set to marketing, because that is the arrangement where
    # the mistake would cost money and the only one where it is visible.
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(
        sender, notice_template="plan_notice_paid", notice_category="marketing"
    ).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=None),
        Alert(kind="listing", listing=view()),
    )
    assert calls[0]["payload"]["template"]["name"] == "new_listing"
    assert result.ok and result.billed_as == "utility"

def test_a_mistyped_category_falls_back_to_the_reviewed_one() -> None:

    # A typo in a settings file must not reach the ledger: a row Meta's
    # invoice has no such line for is worse than a row at the wrong one of two
    # known rates, because the first cannot be reconciled at all.
    assert notifier(notice_category="Marketing").notice_category == "marketing"
    assert notifier(notice_category="promotional").notice_category == "utility"

def test_the_reviewed_category_needs_no_setting() -> None:

    # Meta's latest review put the notice template at utility, so that is what
    # an unconfigured notifier bills it at. The variable exists to follow Meta
    # if it moves the template again — which it has, in both directions — not
    # to be remembered on every deploy. See 0070.
    assert notifier().notice_category == "utility"

def test_a_listing_is_always_the_cheaper_rate() -> None:

    # Which is a property of the queue rather than of this file: anything whose
    # window is shut is held rather than sent, so a delivered alert is always
    # free-form. Asserted here so that a change to the holding rule shows up as
    # a change in what we are billed.
    calls, sender = sent_through(messages=[{"id": "z"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(kind="listing", listing=view()),
    )
    assert result.ok and result.billed_as == "service"
    assert calls[0]["payload"]["type"] != "template"

def test_without_that_template_nothing_is_invented() -> None:

    # A template WhatsApp has not approved is a message it refuses, so saying so
    # beats sending something that cannot arrive.
    result = notifier().send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(
            kind="expired", text="ended", billable_template=True,
            checkout_token="tok",
        ),
    )
    assert not result.ok and "WA_NOTICE_TEMPLATE is not set" in (result.error or "")

def test_inside_the_window_the_notice_is_still_plain_text() -> None:

    # The template exists for the closed window only: free text is free, reads
    # better, and can carry the link.
    calls, sender = sent_through(messages=[{"id": "x"}])
    notifier(sender, notice_template="plan_notice").send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(
            kind="expired", text="Your subscription has ended.",
            billable_template=True, checkout_token="tok",
        ),
    )
    assert calls[0]["payload"]["type"] == "text"

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


def test_the_free_day_is_offered_with_a_button_that_sends_something_back() -> None:

    # The tap has to do two things and a url button does neither: it grants the
    # day and, being an inbound message, it reopens the 24-hour window the day
    # is spent in. A link would have left us with a trial we had just extended
    # and no route to the person on it — the mistake 0070 records about the
    # notice template.
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(1)),
        Alert(
            kind="expiring",
            text=gift_offer(),
            actions=[Action(label=GIFT_BUTTON, short=GIFT_BUTTON, callback=GIFT_TAP)],
        ),
    )

    assert result.ok
    interactive = calls[0]["payload"]["interactive"]
    assert interactive["type"] == "button"
    assert interactive["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": "gift", "title": "Extend 1 Day Free"}}
    ]
    assert "1 extra free day for you!" in interactive["body"]["text"]

def test_the_free_day_cannot_be_offered_to_a_shut_window() -> None:

    # Which is why it rides on the warning five minutes before the end rather
    # than on the expiry: there is no template for it and there never will be,
    # so outside the window there is nothing to send.
    calls, sender = sent_through(messages=[{"id": "x"}])
    result = notifier(sender).send(
        Recipient(channel="whatsapp", address="447700900123", last_inbound=hours_ago(30)),
        Alert(
            kind="expiring",
            text=gift_offer(),
            actions=[Action(label=GIFT_BUTTON, short=GIFT_BUTTON, callback=GIFT_TAP)],
        ),
    )

    assert result.ok is False
    assert calls == []
