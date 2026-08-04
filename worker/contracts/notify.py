"""Delivery channels.

An `Alert` carries facts, not a rendered string. Rendering belongs to the
channel, because the constraints differ sharply between them: Telegram needs
MarkdownV2 escaping within a 4096-character limit, WhatsApp needs an approved
template with a fixed number of variables, email needs HTML. If the matcher
produced text, each new channel would require its own matcher.

`ListingView` is deliberately narrower than `Listing`: it carries facts and a
link to the original, and neither photographs nor the full description. That is
a decision about content reuse, not an oversight.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

AlertKind = Literal["listing", "welcome", "stopped", "ops"]


class Recipient(BaseModel):
    channel: str
    address: str = Field(description="chat id, E.164 phone, or email; never logged")


class Action(BaseModel):
    label: str
    url: str


class ListingView(BaseModel):
    """What a notification is allowed to show."""

    price_pcm: int
    bedrooms: int
    property_type: str | None = None
    district: str | None = None
    zone: int | None = None
    available_from: date | None = None
    furnished: str = "unknown"
    pets_allowed: bool | None = None
    bills_included: bool | None = None
    min_tenancy_months: int | None = None
    source_display: str
    is_landlord_direct: bool | None = None
    url: str


class Alert(BaseModel):
    kind: AlertKind
    listing: ListingView | None = None
    text: str | None = None
    actions: list[Action] = Field(default_factory=list)


class SendResult(BaseModel):
    ok: bool
    provider_msg_id: str | None = None
    error: str | None = None
    retryable: bool = False
    cost_micros: int = 0


@runtime_checkable
class Notifier(Protocol):
    key: str

    def supports(self, kind: AlertKind) -> bool: ...

    def send(self, to: Recipient, alert: Alert) -> SendResult: ...


NOTIFIERS: dict[str, Notifier] = {}


def register_notifier(cls: type) -> type:
    instance = cls()
    NOTIFIERS[instance.key] = instance
    return cls
