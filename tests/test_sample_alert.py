from __future__ import annotations

import json
import pathlib
from datetime import date

from worker.contracts.notify import ListingView
from worker.notify.telegram import render_listing as render_telegram
from worker.notify.whatsapp import render_listing as render_whatsapp

# The home page shows two phones, and the promise on that page is that each one
# shows the alert that channel really sends. A picture of a message we no longer
# send is a lie told to every visitor, so both texts live in one file that the
# page reads and this test is what stops them drifting apart.
FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "apps/web/lib/sample-alert.json"

def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))

def listing(values: dict) -> ListingView:
    values = dict(values)
    values["available_from"] = date.fromisoformat(values["available_from"])
    return ListingView(**values)

REGENERATE = (
    "regenerate apps/web/lib/sample-alert.json: the renderer and the phones on "
    "the home page no longer agree"
)

def test_the_whatsapp_phone_shows_the_message_we_really_send() -> None:
    saved = fixture()
    assert render_whatsapp(listing(saved["listing"])) == saved["whatsapp"], REGENERATE

def test_the_telegram_phone_shows_the_message_we_really_send() -> None:
    saved = fixture()
    assert render_telegram(listing(saved["listing"])) == saved["telegram"], REGENERATE

def test_the_two_channels_are_not_assumed_to_be_identical() -> None:

    # They are not: Telegram links the postcode to a map and WhatsApp leaves it
    # plain, so that its own link preview is the flat. Showing one text twice
    # would hide that.
    saved = fixture()
    assert saved["telegram"] != saved["whatsapp"]

def test_neither_phone_shows_a_restricted_alert() -> None:

    # The lock notice belongs to somebody whose plan ran out, not to the shop
    # window.
    saved = fixture()
    assert "🔒" not in saved["whatsapp"]
    assert "🔒" not in saved["telegram"]
