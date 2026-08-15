"""Telegram delivery.

Messages are sent as plain text with no parse mode. This is a change from the
earlier plan, which called for MarkdownV2 with escaping: a listing line contains
`£1,950 (E14)` and `-`, every one of which MarkdownV2 treats as syntax, and a
single missed escape is a rejected message rather than an ugly one. Plain text
removes that failure mode entirely, and Telegram links bare URLs by itself.

Link previews are disabled. A preview would pull the site's own photograph into
our message, which is the one thing the content rules say not to do — facts and a
link to the original, nothing reproduced.

Two things are separated on purpose: rendering is a pure function of an `Alert`,
and sending is injectable. The wording can therefore be tested without a network
and without a bot token.
"""

from __future__ import annotations

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


def short_date(value: date) -> str:
    return f"{value.day} {MONTHS[value.month - 1]}"


def render_listing(view: ListingView) -> str:
    """Render one listing.

    A line whose values are all unknown is omitted rather than filled with
    "unknown": the message is shorter and it does not claim to know things it does
    not.
    """
    lines: list[str] = []

    # A studio and a room already name the property type, so repeating it would
    # read as "studio · studio".
    if view.bedrooms == 0:
        rooms, type_is_implied = LABELS["studio"], True
    elif view.property_type == "room":
        rooms, type_is_implied = LABELS["room"], True
    else:
        rooms = plural(view.bedrooms, LABELS["bedroom"])
        type_is_implied = False
    head = [f"{money(view.price_pcm)}{LABELS['per_month']}", rooms]
    if view.property_type and not type_is_implied:
        head.append(view.property_type)
    lines.append("🏠 " + " · ".join(head))

    # Bathrooms belong beside the rooms, not in the flags: it is a count, and it
    # is the second thing people look for after the bedroom count.
    if view.bathrooms:
        lines[-1] += " · " + plural(view.bathrooms, LABELS["bathroom"])

    # The neighbourhood name first, then the code. "Leytonstone · E11 · Zone 3"
    # reads as a place; "E11 · Zone 3" reads as a database row.
    where = [
        part for part in (
            view.area, view.district, f"{LABELS['zone']} {view.zone}" if view.zone else None
        ) if part
    ]
    if where:
        lines.append("📍 " + " · ".join(where))
    if view.address:
        lines.append("   " + view.address)

    when: list[str] = []
    if view.available_from is not None:
        when.append(f"{LABELS['available']} {short_date(view.available_from)}")
    if view.min_tenancy_months:
        term = plural(view.min_tenancy_months, LABELS["month"])
        when.append(f"{LABELS['min_term']} {term}")
    if when:
        lines.append("📅 " + " · ".join(when))

    money_lines: list[str] = []
    if view.deposit_pcm:
        money_lines.append(f"{LABELS['deposit']} {money(int(view.deposit_pcm))}")
    if view.size_text:
        money_lines.append(f"{LABELS['size']} {view.size_text}")
    if money_lines:
        lines.append("📐 " + " · ".join(money_lines))

    # Everything the recipient filtered on is named, including what the listing did
    # not say. This is the other half of the matcher letting unknown values pass: a
    # listing can now reach someone whose filter it has not actually answered, and
    # they can only judge that if the gap is on the page rather than implied by its
    # absence.
    flags: list[str] = []
    flags.append(
        "🛋 " + (view.furnished.capitalize()
                if view.furnished and view.furnished != "unknown"
                else f"Furnishing {LABELS['not_stated']}")
    )
    flags.append(
        LABELS["pets_yes"] if view.pets_allowed
        else LABELS["pets_no"] if view.pets_allowed is False
        else f"🐾 Pets {LABELS['not_stated']}"
    )
    flags.append(
        LABELS["bills_yes"] if view.bills_included
        else LABELS["bills_no"] if view.bills_included is False
        else f"💡 Bills {LABELS['not_stated']}"
    )
    lines.append("   ".join(flags))

    lines.append("🔗 " + view.url)

    origin = view.source_display
    if view.is_landlord_direct:
        origin += " · " + LABELS["landlord_direct"]
    lines.append("— " + origin)

    lines.append("")
    lines.append(LABELS["unsubscribe"])
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
            "disable_web_page_preview": True,
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
