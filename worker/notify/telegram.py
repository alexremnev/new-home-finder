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

def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"

def money(amount: int) -> str:
    return f"£{amount:,}".replace(",", ",")

def long_date(value: date) -> str:

    return f"{value.day} {value:%B %Y}"

def maps_link(view: ListingView) -> str | None:

    if not view.postcode:
        return None
    return "https://www.google.com/maps/search/?api=1&query=" + quote(view.postcode)

def size_of(text: str | None) -> str | None:

    if not text:
        return None
    return re.sub(
        r"(\d+(?:\.\d+)?)\s*sq\.?\s*m\b",
        lambda m: f"{round(float(m.group(1)))} m²",
        text,
        flags=re.IGNORECASE,
    )

def _bot_username() -> str:

    return (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")

def render_listing(view: ListingView) -> str:

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

    if view.property_type == "room":
        lines.append("🛏️ Room in a shared flat")
    elif view.bedrooms == 0:
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

    if view.pets_allowed:
        lines.append("🐾 Pets allowed")
    if view.furnished and view.furnished != "unknown":
        lines.append("🛋 " + escape(view.furnished.capitalize()))

    if view.share is not None and view.share < 100:
        lines.append("")
        lines.append(
            f"💎 You're currently seeing only {view.share}% of new listings. "
            "Upgrade to Premium and get access to every new property the moment it "
            "hits the market."
        )

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
        except Exception:
            return {"ok": False, "error_code": exc.code, "description": exc.reason}

@register_notifier
class TelegramNotifier:
    key = "telegram"

    def __init__(self, token: str | None = None, sender: Sender | None = None) -> None:
        self.token = token
        self.sender = sender or (lambda url, payload: _post(url, payload))

    @classmethod
    def from_env(cls) -> TelegramNotifier:

        return cls(os.environ.get("TELEGRAM_TOKEN") or None)

    def supports(self, kind: AlertKind) -> bool:
        return kind in ("listing", "welcome", "stopped", "expiring", "expired", "ops")

    def send(self, to: Recipient, alert: Alert) -> SendResult:
        if not self.token:
            return SendResult(ok=False, error="no telegram token configured", retryable=False)
        payload = {
            "chat_id": to.address,
            "text": render(alert),

            "parse_mode": "HTML",

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

                else {"is_disabled": True}
            ),
        }
        try:
            response = self.sender(API.format(token=self.token), payload)
        except Exception as exc:
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}", retryable=True)
        return _interpret(response)

def _interpret(response: dict[str, Any]) -> SendResult:
    if response.get("ok"):
        message_id = str((response.get("result") or {}).get("message_id") or "")
        return SendResult(ok=True, provider_msg_id=message_id or None)

    code = int(response.get("error_code") or 0)
    description = str(response.get("description") or "unknown error")

    retryable = code in (429, 500, 502, 503, 504) or code == 0
    gone = code == 403 or "chat not found" in description.lower()
    return SendResult(
        ok=False, error=f"{code}: {description}", retryable=retryable, recipient_gone=gone
    )

def is_blocked_by_user(result: SendResult) -> bool:

    return result.recipient_gone
