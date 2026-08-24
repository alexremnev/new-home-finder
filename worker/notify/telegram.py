"""Telegram delivery.

Messages are sent as plain text with no parse mode. This is a change from the
earlier plan, which called for MarkdownV2 with escaping: a listing line contains
`£1,950 (E14)` and `-`, every one of which MarkdownV2 treats as syntax, and a
single missed escape is a rejected message rather than an ugly one. Plain text
removes that failure mode entirely, and Telegram links bare URLs by itself.

Link previews are enabled for listing alerts and off for everything else, and the
distinction is the whole point.

The content rule is that we do not reproduce the site's material: no copying its
photographs into our storage and re-uploading them under our own bot. A preview is
not that. Telegram fetches the portal's own `og:image` from the link, exactly as it
would for a link anybody pasted by hand, and the picture is served by the portal to
the reader. Nothing is copied, nothing is stored, and the image stops appearing the
moment the portal takes the listing down — which is the correct behaviour and the
opposite of what a stored copy would do.

Off for plan notices and confirmations because there is no listing in them, and a
preview of the site's home page under "your trial ends tomorrow" is noise.

Two things are separated on purpose: rendering is a pure function of an `Alert`,
and sending is injectable. The wording can therefore be tested without a network
and without a bot token.
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import quote

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from typing import Any

from worker.contracts.notify import Alert, AlertKind, ListingView, Recipient, SendResult, register_notifier

API = "https://api.telegram.org/bot{token}/sendMessage"
LIMIT = 4096

MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# Wording lives here so that changing the language, or the tone, is one edit
# rather than a hunt through the rendering code.
LABELS = {
    "per_month": "/mo",
    "studio": "studio",
    "room": "room in a share",
    "bedroom": "bedroom",
    "zone": "Zone",
    "available": "Available from",
    "min_term": "min",
    "months": "months",
    "month": "month",
    "pets_yes": "🐾 Pets: allowed",
    "pets_no": "🐾 Pets: not allowed",
    "bills_yes": "💡 Bills included",
    "bills_no": "💡 Bills: not included",
    "landlord_direct": "direct from landlord",
    "unsubscribe": "/stop to unsubscribe",
    "bathroom": "bathroom",
    "deposit": "Deposit",
    "size": "Size",
    "not_stated": "not stated",
}


def plural(count: int, word: str) -> str:
    """`1 bedroom`, `2 bedrooms`. Kept as a function so the count and the word
    cannot drift apart at a call site."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def money(amount: int) -> str:
    return f"£{amount:,}".replace(",", ",")


def long_date(value: date) -> str:
    """"1 October 2026". Spelled out because an alert is read once, at a glance,
    and "01/10/26" is a puzzle in a country that writes dates both ways."""
    return f"{value.day} {value:%B %Y}"


def short_date(value: date) -> str:
    return f"{value.day} {MONTHS[value.month - 1]}"


def maps_link(view: ListingView) -> str | None:
    """A Google Maps search for where this flat is.

    The postcode alone, not the address with it. A UK postcode covers a handful of
    addresses, which is close enough to walk from — and it is the one part of the
    location the feed states in a form Maps resolves without argument. Feeding the
    address as well made the query repeat itself ("…, CR4, CR4 1JG") and gave Maps
    two things to reconcile instead of one to look up.
    """
    if not view.postcode:
        return None
    return "https://www.google.com/maps/search/?api=1&query=" + quote(view.postcode)


def size_of(text: str | None) -> str | None:
    """The source's own size string, with the metric half made readable.

    "47.29 sq m" becomes "47 m²". The unit is a symbol because that is how it is
    written everywhere else, and the decimals go because two of them on a floor area
    is a precision the measurement does not have.

    Kept as a transformation of the source's text rather than a recalculation: the
    square footage is theirs, and converting it ourselves would invent a number.
    """
    if not text:
        return None
    return re.sub(
        r"(\d+(?:\.\d+)?)\s*sq\.?\s*m\b",
        lambda m: f"{round(float(m.group(1)))} m²",
        text,
        flags=re.IGNORECASE,
    )


def _bot_username() -> str:
    """The bot's @name, for a deep link back into the chat.

    Empty when unset, and the button is then simply not attached — a link to
    `t.me/?start=pay` goes nowhere, and a broken button on every free-tier alert is
    worse than no button. The 💎 line still says what is being withheld either way.
    """
    return (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")


def render_listing(view: ListingView) -> str:
    """Render one listing, as HTML.

    ── why HTML and not plain text ──────────────────────────────────────────
    This used to be plain text with no parse mode, because a listing line is full of
    MarkdownV2 syntax characters and one missed escape is a rejected message rather
    than an ugly one. That reasoning was sound and it was about *MarkdownV2*: HTML
    needs three characters escaped, not eighteen, and `escape` below does it to
    every interpolated value.
    
    What HTML buys is the two things the message needed and could not have: a price
    that stands out at a glance, and a postcode that opens a map. Both are things
    somebody scanning alerts on a phone actually uses.

    ── the order of the lines ───────────────────────────────────────────────
    Where, then what it costs, then the rooms. Location first because it is the
    field that disqualifies a listing fastest — no amount of good price fixes the
    wrong end of London — and price second because it is the next one that does.

    A line whose value is unknown is omitted rather than filled in. Nothing here
    says "not stated": that was tried, and on a feed that never states pets or bills
    it put three words of nothing on every message.
    """
    # No blank line after the heading, none after the postcode, none after the
    # furnishing. Each was there to group the message into blocks, and on a phone the
    # effect was the opposite: an eight-line alert became fourteen and stopped fitting
    # a screen. What separates the blocks now is the emoji at the head of each line.
    lines = ["🏠 <b>New listing spotted!</b>"]

    where = ", ".join(part for part in (view.area, view.address) if part)
    if where:
        lines.append("📍 " + escape(where))
    link = maps_link(view)
    if view.postcode and link:
        lines.append(f'📮 <a href="{escape(link)}">{escape(view.postcode)}</a>')
    elif view.district:
        lines.append("📮 " + escape(view.district))

    lines.append(f"💷 <b>{money(view.price_pcm)}/month</b>")

    # A studio names itself; "0 Bedrooms" is arithmetic, not a description.
    if view.bedrooms == 0:
        lines.append("🛏️ Studio")
    else:
        lines.append(f"🛏️ {plural(view.bedrooms, 'Bedroom')}")
    if view.bathrooms:
        lines.append(f"🛁 {plural(view.bathrooms, 'Bathroom')}")

    size = size_of(view.size_text)
    if size:
        lines.append("📐 " + escape(size))
    if view.available_from is not None:
        lines.append("📅 Available from " + long_date(view.available_from))

    # Only when the listing says yes. `False` is not shown either: "pets not
    # allowed" is the common case and printing it on most messages would bury the
    # line on the few where it is good news.
    if view.pets_allowed:
        lines.append("🐾 Pets allowed")
    if view.furnished and view.furnished != "unknown":
        lines.append("🛋 " + escape(view.furnished.capitalize()))

    # The share, named, and placed BEFORE the link. Without the line a plan that
    # delivers a fraction of the matches is withholding them silently, which is the
    # one thing this service exists not to do — and after the link it would sit
    # between the link and the photograph the link produces, splitting the one part
    # of the message that belongs together.
    if view.share is not None and view.share < 100:
        lines.append("")
        lines.append(
            f"💎 You're currently seeing only {view.share}% of new listings. "
            "Upgrade to Premium and get access to every new property the moment it "
            "hits the market."
        )

    # Last, bare, on its own line — and the photograph Telegram renders from it
    # lands directly underneath. Which link the preview uses is stated explicitly in
    # the payload rather than left to position, so this order is a choice about
    # reading and not a mechanism.
    lines.append("")
    lines.append(escape(view.url))

    return "\n".join(lines)


def render(alert: Alert) -> str:
    if alert.kind == "listing":
        if alert.listing is None:
            raise ValueError("a listing alert needs a listing")
        text = render_listing(alert.listing)
    else:
        text = alert.text or ""
        if alert.actions:
            text += "\n\n" + "\n".join(
                f"{action.label}: {action.url}" for action in alert.actions
            )
    return text[:LIMIT]


Sender = Callable[[str, dict[str, Any]], dict[str, Any]]


def _post(url: str, payload: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return dict(json.load(response))
    except urllib.error.HTTPError as exc:
        try:
            return dict(json.load(exc))
        except Exception:  # noqa: BLE001 - a non-JSON error body still has a status
            return {"ok": False, "error_code": exc.code, "description": exc.reason}


@register_notifier
class TelegramNotifier:
    key = "telegram"

    def __init__(self, token: str | None = None, sender: Sender | None = None) -> None:
        self.token = token
        self.sender = sender or (lambda url, payload: _post(url, payload))

    @classmethod
    def from_env(cls) -> TelegramNotifier:
        """How the delivery stage obtains a sender.

        The token is read here and not handed down the pipeline, so the stage
        stays ignorant of what any particular channel needs to authenticate.
        `.env` has already been loaded by `Config` before any job runs.
        """
        return cls(os.environ.get("TELEGRAM_TOKEN") or None)

    def supports(self, kind: AlertKind) -> bool:
        return kind in ("listing", "welcome", "stopped", "expiring", "expired", "ops")

    def send(self, to: Recipient, alert: Alert) -> SendResult:
        if not self.token:
            return SendResult(ok=False, error="no telegram token configured", retryable=False)
        payload = {
            "chat_id": to.address,
            "text": render(alert),
            # HTML rather than MarkdownV2: three characters to escape instead of
            # eighteen, and `escape` is applied to every value the renderer
            # interpolates. The listing renderer needs it for the bold price and
            # the postcode's map link.
            "parse_mode": "HTML",
            # Which link the preview comes from, named rather than left to Telegram.
            #
            # Telegram previews the FIRST link in a message, and the first link in a
            # listing alert is the map on the postcode — so the photograph was a
            # picture of a street map instead of the flat. Naming the property's url
            # fixes that without reordering the message, which is the alternative and
            # a worse one: the postcode belongs near the address, not at the bottom.
            #
            # The preview sits BELOW the text, which is Telegram's default and the
            # reason `show_above_text` is absent rather than set to false. Facts
            # first, then the link, then the picture: the numbers are what decide
            # whether the photograph is worth looking at, and a photograph on top
            # pushes them off a phone screen.
            #
            # `prefer_large_media` asks for the big rendering rather than the
            # thumbnail — small is worse than none for judging a flat.
            # The way out of the reduced share, on the message that demonstrates it.
            #
            # A deep link into the bot rather than a link to the checkout page: a
            # link in a chat message lives as long as the message does, which is for
            # ever, so it must carry no secret. Tapping this opens the chat, the bot
            # mints a fresh short-lived token, and the plans are offered there.
            #
            # Only on messages where the share is being applied. On a paid plan's
            # alerts it would be an advert for something already bought.
            **(
                {
                    "reply_markup": {
                        "inline_keyboard": [[
                            {
                                "text": "💎 Upgrade to Premium",
                                "url": f"https://t.me/{_bot_username()}?start=pay",
                            }
                        ]]
                    }
                }
                if (
                    alert.kind == "listing"
                    and alert.listing is not None
                    and alert.listing.share is not None
                    and alert.listing.share < 100
                    and _bot_username()
                )
                else {}
            ),
            "link_preview_options": (
                {
                    "url": alert.listing.url,
                    "prefer_large_media": True,
                }
                if alert.kind == "listing" and alert.listing is not None
                # Off for everything else. A plan notice has no listing, and a
                # preview of the site's home page under "your trial ends tomorrow"
                # is noise.
                else {"is_disabled": True}
            ),
        }
        try:
            response = self.sender(API.format(token=self.token), payload)
        except Exception as exc:  # noqa: BLE001 - transport failures are retryable
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}", retryable=True)
        return _interpret(response)


def _interpret(response: dict[str, Any]) -> SendResult:
    if response.get("ok"):
        message_id = str((response.get("result") or {}).get("message_id") or "")
        return SendResult(ok=True, provider_msg_id=message_id or None)

    code = int(response.get("error_code") or 0)
    description = str(response.get("description") or "unknown error")
    # 403 means the person blocked the bot or deleted the chat. Retrying cannot
    # help and repeated attempts count against the bot's standing, so it is final.
    retryable = code in (429, 500, 502, 503, 504) or code == 0
    gone = code == 403 or "chat not found" in description.lower()
    return SendResult(
        ok=False, error=f"{code}: {description}", retryable=retryable, recipient_gone=gone
    )


def is_blocked_by_user(result: SendResult) -> bool:
    """Kept for readability at call sites; the judgement itself is on the result."""
    return result.recipient_gone
