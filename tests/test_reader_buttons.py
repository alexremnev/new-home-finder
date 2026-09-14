from __future__ import annotations

import types

from worker.ingest.reader import button_url, urls_of

def button(text: str, **fields: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(text=text, **fields)

def message(buttons: list[object], *, text: str = "", entities: list[object] | None = None):
    return types.SimpleNamespace(
        reply_markup=types.SimpleNamespace(
            rows=[types.SimpleNamespace(buttons=[b]) for b in buttons]
        ),
        entities=entities or [],
        text=text,
    )

def test_flat_url_button_still_read():
    assert button_url(button("View", url="https://example.test/a")) == "https://example.test/a"

def test_nested_url_button_is_read():
    nested = button(
        "View",
        type=types.SimpleNamespace(url="https://www.rightmove.co.uk/properties/93136290"),
    )
    assert button_url(nested) == "https://www.rightmove.co.uk/properties/93136290"

def test_callback_button_has_no_url():
    callback = button(
        "Upgrade",
        type=types.SimpleNamespace(data=b"_subscription_payment_", requires_password=False),
    )
    assert button_url(callback) is None

def test_button_without_a_type_at_all():
    assert button_url(button("Nothing")) is None

def test_listing_message_yields_portal_link_before_map_pin():
    found = urls_of(
        message(
            [
                button(
                    "\U0001f3e0 View Listing",
                    type=types.SimpleNamespace(
                        url="https://www.zoopla.co.uk/to-rent/details/74238813"
                    ),
                ),
                button(
                    "\U0001f4b3 Upgrade to Paid Plan",
                    type=types.SimpleNamespace(data=b"_subscription_payment_"),
                ),
            ],
            entities=[types.SimpleNamespace(url="https://maps.google.com/?q=51.49,-0.24")],
        )
    )
    assert found == [
        "https://www.zoopla.co.uk/to-rent/details/74238813",
        "https://maps.google.com/?q=51.49,-0.24",
    ]

def test_daily_digest_yields_nothing():
    assert urls_of(
        message([
            button("Upgrade", type=types.SimpleNamespace(data=b"_subscription_payment_")),
            button("Pause", type=types.SimpleNamespace(data=b"_searches_pause_all_")),
        ])
    ) == []
