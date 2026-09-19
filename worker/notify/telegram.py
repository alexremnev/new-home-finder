from __future__ import annotations

from html import escape

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from worker.contracts.notify import (
    Alert,
    AlertKind,
    ListingView,
    Recipient,
    SendResult,
    register_notifier,
)
from worker.notify.fields import Line, listing_fields, restriction_text

API = "https://api.telegram.org/bot{token}/sendMessage"
LIMIT = 4096

def _html(line: Line) -> str:

    body = escape(line.text)
    if line.link:
        body = f'<a href="{escape(line.link)}">{body}</a>'
    if line.bold:
        body = f"<b>{body}</b>"
    return f"{line.icon} {body}"

def restriction_notice(view: ListingView) -> str | None:

    parts = restriction_text(view)
    if parts is None:
        return None
    emphasised, rest = parts
    return f"🔒 <b>{escape(emphasised)}</b>{escape(rest)}"

def render_listing(view: ListingView) -> str:

    lines = [_html(line) for line in listing_fields(view)]
    lines.append(escape(view.url))

    notice = restriction_notice(view)
    if notice:
        lines.append(notice)

    return "\n".join(lines)

def render(alert: Alert) -> str:
    if alert.kind == "listing":
        if alert.listing is None:
            raise ValueError("a listing alert needs a listing")
        text = render_listing(alert.listing)
    else:
        text = alert.text or ""
    return text[:LIMIT]

def keyboard_for(alert: Alert) -> dict[str, Any] | None:

    rows: list[list[dict[str, str]]] = []
    for action in alert.actions:
        if action.url:
            rows.append([{"text": action.label, "url": action.url}])
        elif action.callback:
            rows.append([{"text": action.label, "callback_data": action.callback}])
    return {"inline_keyboard": rows} if rows else None

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
        keyboard = keyboard_for(alert)
        payload = {
            "chat_id": to.address,
            "text": render(alert),

            "parse_mode": "HTML",

            **({"reply_markup": keyboard} if keyboard else {}),
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
