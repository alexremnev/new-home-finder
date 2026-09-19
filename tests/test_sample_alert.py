from __future__ import annotations

import json
import pathlib
from datetime import date

from worker.contracts.notify import ListingView
from worker.notify.whatsapp import render_listing

# The phone mockup on the home page shows an alert, and the promise on that page
# is that it is the alert we actually send. A picture of a message we no longer
# send is a lie told to every visitor, so the text is kept in one file that both
# sides read and this test is what stops the two drifting apart.
FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "apps/web/lib/sample-alert.json"

def test_the_mockup_shows_the_message_we_really_send() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    values = dict(fixture["listing"])
    values["available_from"] = date.fromisoformat(values["available_from"])

    assert render_listing(ListingView(**values)) == fixture["text"], (
        "regenerate apps/web/lib/sample-alert.json: the renderer and the mockup "
        "on the home page no longer agree"
    )

def test_the_mockup_is_not_a_restricted_alert() -> None:

    # The lock notice belongs to somebody whose plan ran out, not to the shop
    # window.
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert "🔒" not in fixture["text"]
