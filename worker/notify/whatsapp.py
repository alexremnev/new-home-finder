from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from worker.contracts.notify import (
    Alert,
    AlertKind,
    ListingView,
    Recipient,
    SendResult,
    register_notifier,
)
from worker.notify.fields import (
    Line,
    listing_fields,
    long_date,
    money,
    plural,
    restriction_text,
)

GRAPH = "https://graph.facebook.com/{version}/{phone_id}/messages"
VERSION = "v21.0"
LIMIT = 4096

# WhatsApp allows a business-initiated free-form message only within this long
# after the person's own last message. Outside it, an approved template.
WINDOW = timedelta(hours=24)

# A template parameter may not be empty, and may not contain a newline. Absent
# facts therefore need a stand-in rather than being left out, which is the price
# of a fixed shape.
ABSENT = "—"

def _plain(line: Line) -> str:

    body = f"*{line.text}*" if line.bold else line.text
    return f"{line.icon} {body}"

def restriction_notice(view: ListingView) -> str | None:

    parts = restriction_text(view)
    if parts is None:
        return None
    emphasised, rest = parts
    return f"🔒 *{emphasised}*{rest}"

def render_listing(view: ListingView) -> str:

    # Only ever sent inside the 24-hour window, where a link preview works and
    # the message costs nothing. The map link is left out so the preview is the
    # flat: WhatsApp previews the first url it finds.
    lines = [_plain(line) for line in listing_fields(view)]
    lines.append(view.url)

    notice = restriction_notice(view)
    if notice:
        lines.append("")
        lines.append(notice)

    return "\n".join(lines)

def render(alert: Alert) -> str:

    if alert.kind == "listing":
        if alert.listing is None:
            raise ValueError("a listing alert needs a listing")
        text = render_listing(alert.listing)
    else:
        text = alert.text or ""

    # An action that exists only as a button cannot be offered here, so it is
    # left out rather than turned into an instruction nobody can follow.
    links = [a for a in alert.actions if a.url]
    if links:
        text += "\n\n" + "\n".join(f"{a.label}: {a.url}" for a in links)

    return text[:LIMIT]

def one_line(value: str | None) -> str:

    text = " ".join((value or "").split())
    return text or ABSENT

def template_params(view: ListingView) -> list[str]:

    where = ", ".join(part for part in (view.area, view.address) if part)
    if not where:
        where = view.postcode or view.district or ABSENT

    if view.property_type == "room":
        beds = "Room in a shared flat"
    elif view.bedrooms == 0:
        beds = "Studio"
    else:
        beds = plural(view.bedrooms, "Bedroom")

    return [
        one_line(where),
        one_line(f"{money(view.price_pcm)}"),
        one_line(beds),
        one_line(plural(view.bathrooms, "Bathroom") if view.bathrooms else None),
        one_line(
            f"Available from {long_date(view.available_from)}"
            if view.available_from is not None
            else None
        ),
        one_line(
            view.furnished.capitalize()
            if view.furnished and view.furnished != "unknown"
            else None
        ),
    ]

def window_open(last_inbound: datetime | None, *, now: datetime | None = None) -> bool:

    if last_inbound is None:
        return False
    moment = now or datetime.now(timezone.utc)
    if last_inbound.tzinfo is None:
        last_inbound = last_inbound.replace(tzinfo=timezone.utc)
    return moment - last_inbound < WINDOW

Sender = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]

def _post(
    url: str, payload: dict[str, Any], headers: dict[str, str], *, timeout: float = 20.0
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return dict(json.load(response))
    except urllib.error.HTTPError as exc:
        try:
            return dict(json.load(exc))
        except Exception:
            return {"error": {"message": f"{exc.code} {exc.reason}", "code": exc.code}}
    except urllib.error.URLError as exc:
        return {"error": {"message": f"unreachable: {exc.reason}", "code": 0}}

def digits(address: str) -> str:

    return "".join(c for c in address if c.isdigit())

@register_notifier
class WhatsAppNotifier:
    key = "whatsapp"

    def __init__(
        self,
        phone_id: str | None = None,
        token: str | None = None,
        template: str | None = None,
        language: str = "en",
        link_prefix: str | None = None,
        sender: Sender | None = None,
        version: str = VERSION,
    ) -> None:
        self.phone_id = phone_id
        self.token = token
        self.template = template
        self.language = language
        self.link_prefix = (link_prefix or "").rstrip("/")
        self.sender = sender or _post
        self.version = version

    @classmethod
    def from_env(cls) -> WhatsAppNotifier:

        return cls(
            os.environ.get("WA_PHONE_NUMBER_ID") or None,
            os.environ.get("WA_ACCESS_TOKEN") or None,
            os.environ.get("WA_TEMPLATE_NAME") or None,
            os.environ.get("WA_TEMPLATE_LANGUAGE") or "en",
            os.environ.get("WA_LINK_PREFIX") or None,
        )

    def supports(self, kind: AlertKind) -> bool:
        return kind in ("listing", "welcome", "stopped", "expiring", "expired")

    def body(self, to: Recipient, alert: Alert) -> dict[str, Any] | SendResult:

        number = digits(to.address)
        if not number:
            return SendResult(
                ok=False, error=f"not a phone number: {to.address!r}",
                retryable=False, recipient_gone=True,
            )

        if window_open(to.last_inbound):
            return {
                "messaging_product": "whatsapp",
                "to": number,
                "type": "text",
                "text": {"preview_url": alert.kind == "listing", "body": render(alert)},
            }

        if alert.kind != "listing" or alert.listing is None:
            # Nothing but a listing has an approved shape, and inventing one
            # here would be a message WhatsApp refuses. Retryable: the person
            # may write in, and then the free-form path opens.
            return SendResult(
                ok=False,
                error=f"outside the 24h window and {alert.kind} has no template",
                retryable=True,
            )
        if not self.template:
            return SendResult(
                ok=False, error="WA_TEMPLATE_NAME is not set", retryable=False
            )

        listing = alert.listing
        components: list[dict[str, Any]] = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": value}
                    for value in template_params(listing)
                ],
            }
        ]
        if self.link_prefix and listing.listing_id is not None:
            # The button is a fixed prefix plus one variable, so the three
            # portals cannot each be linked directly. Ours redirects, which is
            # also the only way to learn which listings get opened.
            components.append({
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [{"type": "text", "text": str(listing.listing_id)}],
            })

        return {
            "messaging_product": "whatsapp",
            "to": number,
            "type": "template",
            "template": {
                "name": self.template,
                "language": {"code": self.language},
                "components": components,
            },
        }

    def send(self, to: Recipient, alert: Alert) -> SendResult:
        if not self.phone_id or not self.token:
            return SendResult(
                ok=False,
                error="WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN must both be set",
                retryable=False,
            )

        payload = self.body(to, alert)
        if isinstance(payload, SendResult):
            return payload

        url = GRAPH.format(version=self.version, phone_id=self.phone_id)
        try:
            response = self.sender(
                url, payload, {"Authorization": f"Bearer {self.token}"}
            )
        except Exception as exc:
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}", retryable=True)
        return _interpret(response)

# Graph reports everything as an `error` object with a numeric code.
#
# WhatsApp's own codes are five and six digits, so "code >= 500 means a server
# error" — true of HTTP — would make every WhatsApp error retryable, including a
# template that does not match its parameters. Transport statuses come from
# `_post` and are three digits, hence the bounded range.
_GONE = {131026, 131052}
_PERMANENT = {131047, 132000, 132001, 132005, 132007, 132012, 132015, 133010}
# 130429 rate limit, 131048 spam rate limit, 131056 pair rate limit,
# 368 temporarily blocked, 0 unreachable.
_RETRY = {0, 368, 130429, 131048, 131056}

def _interpret(response: dict[str, Any]) -> SendResult:
    messages = response.get("messages")
    if isinstance(messages, list) and messages:
        return SendResult(ok=True, provider_msg_id=str(messages[0].get("id") or "") or None)

    error = response.get("error") or {}
    code = int(error.get("code") or 0)
    detail = int((error.get("error_data") or {}).get("details_code") or 0) or code
    message = str(error.get("message") or "unknown error")

    gone = code in _GONE or detail in _GONE
    permanent = gone or code in _PERMANENT or detail in _PERMANENT

    return SendResult(
        ok=False,
        error=f"{code}: {message}",
        retryable=not permanent and (code in _RETRY or 500 <= code < 600),
        recipient_gone=gone,
    )
